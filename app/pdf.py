"""Génération des PDF : reçus de paiement et bulletins de notes (ReportLab)."""
import io
import re
import unicodedata
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.models import Bulletin, Classe, Ecole, Eleve, Paiement, Solde

BLEU = colors.HexColor("#1d4ed8")
GRIS = colors.HexColor("#6b7280")
FOND = colors.HexColor("#eef2ff")
MODES = {"especes": "Espèces", "orange_money": "Orange Money", "mtn_momo": "MTN MoMo", "virement": "Virement"}

_styles = getSampleStyleSheet()
S_ECOLE = ParagraphStyle("ecole", parent=_styles["Title"], fontSize=15, textColor=BLEU, spaceAfter=2)
S_INFO = ParagraphStyle("info", parent=_styles["Normal"], fontSize=8.5, textColor=GRIS, alignment=TA_CENTER)
S_TITRE = ParagraphStyle("titre", parent=_styles["Heading2"], alignment=TA_CENTER, spaceBefore=6, spaceAfter=6)
S_NORMAL = ParagraphStyle("n", parent=_styles["Normal"], fontSize=10, leading=14)
S_PETIT = ParagraphStyle("p", parent=S_NORMAL, fontSize=8.5, textColor=GRIS)
S_DROITE = ParagraphStyle("d", parent=S_NORMAL, alignment=TA_RIGHT)


# ---------- Utilitaires ----------
def en_tete_pdf(nom_fichier: str) -> dict:
    """En-tête HTTP avec un nom de fichier sans accents ni espaces."""
    ascii_ = unicodedata.normalize("NFKD", nom_fichier).encode("ascii", "ignore").decode()
    ascii_ = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_)
    return {"Content-Disposition": f'inline; filename="{ascii_}"'}


def argent(montant: int, devise: str) -> str:
    return f"{montant:,}".replace(",", " ") + f" {devise}"


def date_fr(d: date) -> str:
    return d.strftime("%d/%m/%Y")


_UNITES = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
           "onze", "douze", "treize", "quatorze", "quinze", "seize"]
_DIZAINES = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}


def _moins_de_100(n: int) -> str:
    if n <= 16:
        return _UNITES[n]
    if n < 20:
        return "dix-" + _UNITES[n - 10]
    if n < 70:
        d, u = divmod(n, 10)
        base = _DIZAINES[d * 10]
        return base if u == 0 else base + (" et un" if u == 1 else "-" + _UNITES[u])
    if n < 80:
        return "soixante" + (" et onze" if n == 71 else "-" + _moins_de_100(n - 60))
    if n == 80:
        return "quatre-vingts"
    return "quatre-vingt-" + _moins_de_100(n - 80)


def _moins_de_1000(n: int) -> str:
    c, r = divmod(n, 100)
    if c == 0:
        return _moins_de_100(r)
    debut = "cent" if c == 1 else _UNITES[c] + " cent" + ("s" if r == 0 else "")
    return debut if r == 0 else debut + " " + _moins_de_100(r)


def en_lettres(n: int) -> str:
    """Montant en toutes lettres (français), ex : 250000 → deux cent cinquante mille."""
    if n == 0:
        return "zéro"
    morceaux = []
    for valeur, nom in ((1_000_000_000, "milliard"), (1_000_000, "million")):
        q, n = divmod(n, valeur)
        if q:
            morceaux.append(f"{_moins_de_1000(q)} {nom}{'s' if q > 1 else ''}")
    q, n = divmod(n, 1000)
    if q:
        morceaux.append("mille" if q == 1 else _moins_de_1000(q).removesuffix("s") + " mille")
    if n:
        morceaux.append(_moins_de_1000(n))
    return " ".join(morceaux)


def _images(ecole: Ecole) -> dict:
    """Logo et cachet de l'école, lus dans la session d'où vient l'objet école."""
    from sqlalchemy.orm import object_session
    from app.services import images_ecole
    session = object_session(ecole)
    return images_ecole(session, ecole.id) if session is not None and ecole.id else {"logo": None, "cachet": None}


def _image(png: bytes, largeur: float, hauteur: float):
    """Image réduite pour tenir dans le cadre, proportions gardées."""
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image as ImagePdf
    l, h = ImageReader(io.BytesIO(png)).getSize()
    r = min(largeur / l, hauteur / h)
    return ImagePdf(io.BytesIO(png), width=l * r, height=h * r)


def _entete_ecole(ecole: Ecole) -> list:
    infos = " · ".join(x for x in (ecole.adresse, ecole.telephone and f"Tél : {ecole.telephone}") if x)
    texte = [Paragraph(ecole.nom, S_ECOLE)]
    if infos:
        texte.append(Paragraph(infos, S_INFO))
    texte.append(Paragraph(f"Année scolaire {ecole.annee_scolaire}", S_INFO))
    logo = _images(ecole).get("logo")
    if logo:   # logo à gauche, texte centré, colonne vide à droite pour garder le centrage
        t = Table([[_image(logo, 22 * mm, 22 * mm), texte, ""]], colWidths=[26 * mm, None, 26 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        elems = [t]
    else:
        elems = texte
    elems.append(Spacer(1, 4))
    elems.append(HRFlowable(width="100%", thickness=1.2, color=BLEU))
    return elems


def _tableau_infos(lignes: list[tuple[str, str]], largeur: float) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", S_NORMAL), Paragraph(v, S_NORMAL)] for k, v in lignes]
    t = Table(data, colWidths=[largeur * 0.35, largeur * 0.65])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _signatures(gauche: str, droite: str, largeur: float, ecole: Ecole | None = None) -> Table:
    """Zone de signatures ; le cachet de l'école (s'il existe) est posé côté école, à droite."""
    cachet = _images(ecole).get("cachet") if ecole is not None else None
    if cachet:
        t = Table([[gauche, droite], ["", _image(cachet, 34 * mm, 24 * mm)]],
                  colWidths=[largeur / 2, largeur / 2], rowHeights=[14, 26 * mm])
    else:
        t = Table([[gauche, droite], ["", ""], ["", ""], ["", ""]],
                  colWidths=[largeur / 2, largeur / 2], rowHeights=[14, 14, 14, 14])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


# ---------- Reçu de paiement ----------
def pdf_recu(ecole: Ecole, paiement: Paiement, eleve: Eleve, classe: Classe,
             solde: Solde, caissier: str | None, frais: str = "Scolarité") -> bytes:
    buf = io.BytesIO()
    marge = 12 * mm
    doc = SimpleDocTemplate(buf, pagesize=A5, leftMargin=marge, rightMargin=marge,
                            topMargin=marge, bottomMargin=marge,
                            title=f"Reçu {paiement.numero_recu}", author=ecole.nom)
    largeur = A5[0] - 2 * marge
    d = ecole.devise

    montant = Table(
        [[Paragraph("<b>MONTANT REÇU</b>", S_NORMAL),
          Paragraph(f"<font size=14><b>{argent(paiement.montant, d)}</b></font>", S_DROITE)],
         [Paragraph(f"<i>{en_lettres(paiement.montant).capitalize()} {d}</i>", S_PETIT), ""]],
        colWidths=[largeur * 0.45, largeur * 0.55],
    )
    montant.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FOND),
        ("SPAN", (0, 1), (1, 1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.8, BLEU),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    lignes_situation = []
    if solde.reduction:
        lignes_situation.append([f"Réduction sur la scolarité ({solde.reduction_motif or '—'})",
                                 "− " + argent(solde.reduction, d)])
    for l in (solde.lignes or []):
        lignes_situation.append([f"{l.nom} : payé {argent(l.paye, d)} / {argent(l.du, d)}",
                                 "soldé" if l.reste == 0 else f"reste {argent(l.reste, d)}"])
    situation = Table(
        lignes_situation + [["Total payé à ce jour", argent(solde.total_paye, d)],
                            ["Reste à payer (tous frais)", argent(solde.reste_a_payer, d)]],
        colWidths=[largeur * 0.62, largeur * 0.38],
    )
    situation.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.lightgrey),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (1, -1), (1, -1), colors.HexColor("#dc2626") if solde.reste_a_payer else colors.HexColor("#059669")),
    ]))

    infos = [
        ("Élève", f"{eleve.prenom} {eleve.nom}"),
        ("Matricule", eleve.matricule or "–"),
        ("Classe", classe.nom),
        ("Date du paiement", date_fr(paiement.date_paiement)),
        ("Mode de paiement", MODES.get(paiement.mode.value, paiement.mode.value)),
        ("Pour", frais),
    ]
    if paiement.reference:
        infos.append(("Référence", paiement.reference))

    annulation = []
    if paiement.annule:
        annulation = [Paragraph(
            f"<font color='#dc2626'><b>PAIEMENT ANNULÉ</b> le {date_fr(paiement.annule_le.date())}"
            f" — motif : {paiement.motif_annulation}</font>", S_NORMAL), Spacer(1, 4)]
    elems = _entete_ecole(ecole) + [
        Paragraph(f"REÇU DE PAIEMENT N° {paiement.numero_recu}", S_TITRE),
        *annulation,
        _tableau_infos(infos, largeur),
        Spacer(1, 8), montant, Spacer(1, 10),
        Paragraph("<b>Situation du compte</b>", S_NORMAL), Spacer(1, 3), situation,
        Spacer(1, 14),
        _signatures("Le parent", f"Le caissier{' : ' + caissier if caissier else ''}", largeur, ecole),
        Spacer(1, 6),
        Paragraph(f"Édité le {date_fr(date.today())} — conservez ce reçu.", S_PETIT),
    ]
    doc.build(elems)
    return buf.getvalue()


# ---------- Bulletins ----------
def _page_bulletin(ecole: Ecole, b: Bulletin, largeur: float) -> list:
    S_CELL = ParagraphStyle("cell", parent=S_NORMAL, fontSize=8, leading=9.5)
    entetes = ["Matière", "Coef.", "Moy.", "Points", "Moy. classe", "Min – Max", "Rang", "Appréciation du professeur"]
    lignes = [[Paragraph(m.matiere, S_CELL), str(m.coefficient), f"{m.moyenne:.2f}", f"{m.moyenne * m.coefficient:.2f}",
               "–" if m.moyenne_classe is None else f"{m.moyenne_classe:.2f}",
               "–" if m.min_classe is None else f"{m.min_classe:.2f} – {m.max_classe:.2f}",
               "–" if m.rang_matiere is None else str(m.rang_matiere),
               Paragraph(m.appreciation or "", S_CELL)]
              for m in b.matieres]
    total_coef = sum(m.coefficient for m in b.matieres)
    total_pts = sum(m.moyenne * m.coefficient for m in b.matieres)
    if not lignes:
        lignes = [["Aucune note pour ce trimestre", "", "", "", "", "", "", ""]]
    pied = ["TOTAL", str(total_coef or ""), "", f"{total_pts:.2f}" if total_coef else "",
            "" if b.moyenne_classe is None else f"{b.moyenne_classe:.2f}", "", "", ""]

    fr = [0.19, 0.07, 0.08, 0.09, 0.1, 0.13, 0.07, 0.27]
    t = Table([entetes] + lignes + [pied], colWidths=[largeur * f for f in fr], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLEU),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), FOND),
        ("ALIGN", (1, 0), (-2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, m in enumerate(b.matieres, start=1):
        if m.moyenne < 10:
            style.append(("TEXTCOLOR", (2, i), (2, i), colors.HexColor("#dc2626")))
    t.setStyle(TableStyle(style))

    moy = "–" if b.moyenne_generale is None else f"{b.moyenne_generale:.2f} / 20"
    rang = "–" if b.rang is None else f"{b.rang}{'er' if b.rang == 1 else 'e'} sur {b.effectif}"
    resume = Table(
        [[Paragraph("<b>Moyenne générale</b>", S_NORMAL), Paragraph(f"<font size=13><b>{moy}</b></font>", S_DROITE)],
         [Paragraph("<b>Rang</b>", S_NORMAL), Paragraph(f"<b>{rang}</b>"
            + (f" — moyenne de la classe : {b.moyenne_classe:.2f}" if b.moyenne_classe is not None else ""), S_DROITE)],
         [Paragraph("<b>Appréciation</b>", S_NORMAL), Paragraph(b.appreciation, S_DROITE)],
         [Paragraph("<b>Assiduité</b>", S_NORMAL), Paragraph(
             f"{b.absences} absence(s) dont {b.absences_justifiees} justifiée(s) · {b.retards} retard(s)", S_DROITE)]]
        + ([[Paragraph("<b>Moyennes T1 / T2 / T3</b>", S_NORMAL), Paragraph(
              " / ".join("–" if m is None else f"{m:.2f}" for m in b.moyennes_trimestres), S_DROITE)],
            [Paragraph("<b>Moyenne annuelle</b>", S_NORMAL), Paragraph(
              f"<font size=13><b>{b.moyenne_annuelle:.2f} / 20</b></font>"
              + (f" — rang {b.rang_annuel}{'er' if b.rang_annuel == 1 else 'e'}" if b.rang_annuel else ""), S_DROITE)],
            [Paragraph("<b>Décision</b>", S_NORMAL), Paragraph(
              f"<font color='{'#059669' if b.decision == 'Admis(e)' else '#dc2626'}'><b>{b.decision}</b></font>", S_DROITE)]]
           if b.moyenne_annuelle is not None else [])
        + ([[Paragraph("<b>Avis du conseil de classe</b>", S_NORMAL), Paragraph(f"<i>{b.avis_conseil}</i>", S_DROITE)]]
           if b.avis_conseil else []),
        colWidths=[largeur * 0.4, largeur * 0.6],
    )
    resume.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, BLEU),
        ("BACKGROUND", (0, 0), (-1, -1), FOND),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#c7d2fe")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    return _entete_ecole(ecole) + [
        Paragraph(f"BULLETIN DE NOTES — {b.trimestre}{'er' if b.trimestre == 1 else 'e'} TRIMESTRE", S_TITRE),
        _tableau_infos([
            ("Élève", b.nom_complet),
            ("Matricule", b.matricule or "–"),
            ("Classe", f"{b.classe} ({b.effectif} élève{'s' if b.effectif > 1 else ''})"),
        ], largeur),
        Spacer(1, 10), t, Spacer(1, 12), resume, Spacer(1, 22),
        _signatures("Signature du parent", "Le directeur", largeur, ecole),
        Spacer(1, 10),
        Paragraph(f"Édité le {date_fr(date.today())}", S_PETIT),
    ]


def pdf_bulletins(ecole: Ecole, bulletins: list[Bulletin]) -> bytes:
    """Un ou plusieurs bulletins, une page A4 par élève."""
    buf = io.BytesIO()
    marge = 18 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=marge, rightMargin=marge,
                            topMargin=marge, bottomMargin=marge,
                            title="Bulletins de notes", author=ecole.nom)
    largeur = A4[0] - 2 * marge
    elems = []
    for i, b in enumerate(bulletins):
        if i:
            elems.append(PageBreak())
        elems += _page_bulletin(ecole, b, largeur)
    doc.build(elems)
    return buf.getvalue()


# ---------- État des paiements d'une classe ----------
STATUTS = {"a_jour": "À jour", "partiel": "Partiel", "rien_paye": "Rien payé"}
COULEURS_STATUT = {"a_jour": "#059669", "partiel": "#d97706", "rien_paye": "#dc2626"}


def pdf_etat_paiements(ecole: Ecole, classe: Classe, soldes: list[Solde]) -> bytes:
    buf = io.BytesIO()
    marge = 14 * mm
    page = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=marge, rightMargin=marge,
                            topMargin=marge, bottomMargin=marge,
                            title=f"État des paiements — {classe.nom}", author=ecole.nom)
    largeur = page[0] - 2 * marge
    d = ecole.devise

    entetes = ["N°", "Matricule", "Élève", "Frais annuels", "Payé", "Reste à payer", "Paiements", "Dernier", "Statut"]
    lignes = []
    for i, s in enumerate(soldes, start=1):
        lignes.append([
            str(i), s.matricule or "–", s.nom_complet, argent(s.frais_annuels, d), argent(s.total_paye, d),
            argent(s.reste_a_payer, d), str(s.nb_paiements),
            date_fr(s.dernier_paiement) if s.dernier_paiement else "–", STATUTS[s.statut.value],
        ])
    if not lignes:
        lignes = [["", "", "Aucun élève dans cette classe", "", "", "", "", "", ""]]
    attendu = sum(s.frais_annuels for s in soldes)
    paye = sum(s.total_paye for s in soldes)
    reste = sum(s.reste_a_payer for s in soldes)
    pied = ["", "", f"TOTAL ({len(soldes)} élèves)", argent(attendu, d), argent(paye, d), argent(reste, d), "", "", ""]

    fr = [0.04, 0.1, 0.22, 0.12, 0.12, 0.12, 0.08, 0.09, 0.11]
    t = Table([entetes] + lignes + [pied], colWidths=[largeur * f for f in fr], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLEU),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), FOND),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (6, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (7, 0), (8, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, s in enumerate(soldes, start=1):
        c = colors.HexColor(COULEURS_STATUT[s.statut.value])
        style += [("TEXTCOLOR", (8, i), (8, i), c), ("FONTNAME", (8, i), (8, i), "Helvetica-Bold")]
        if s.reste_a_payer:
            style.append(("TEXTCOLOR", (5, i), (5, i), colors.HexColor("#dc2626")))
    t.setStyle(TableStyle(style))

    nb = {k: sum(1 for s in soldes if s.statut.value == k) for k in STATUTS}
    taux = round(100 * paye / attendu, 1) if attendu else 0
    resume = Paragraph(
        f"<b>{nb['a_jour']}</b> à jour · <b>{nb['partiel']}</b> partiel · <b>{nb['rien_paye']}</b> rien payé"
        f" — taux de recouvrement <b>{taux} %</b>", S_NORMAL)

    elems = _entete_ecole(ecole) + [
        Paragraph(f"ÉTAT DES PAIEMENTS — {classe.nom}", S_TITRE),
        Paragraph(f"Frais annuels : {argent(classe.frais_annuels, d)} par élève · "
                  f"situation au {date_fr(date.today())}", S_INFO),
        Spacer(1, 8), resume, Spacer(1, 6), t, Spacer(1, 18),
        _signatures("Le comptable", "Le directeur", largeur, ecole),
    ]
    doc.build(elems)
    return buf.getvalue()


# ---------- Cartes scolaires (format carte bancaire, 10 par page A4) ----------
def pdf_cartes(ecole: Ecole, eleves: list) -> bytes:
    """`eleves` : liste de (Eleve, nom de la classe, photo JPEG ou None)."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    L, H = 85.6 * mm, 54 * mm
    cols, lignes = 2, 5
    marge_x = (A4[0] - cols * L) / 3
    marge_y = (A4[1] - lignes * H) / 6
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Cartes scolaires — {ecole.nom}")
    c.setAuthor(ecole.nom)

    def texte_coupe(t: str, police: str, taille: float, largeur: float) -> str:
        while t and c.stringWidth(t, police, taille) > largeur:
            t = t[:-2] + "…" if not t.endswith("…") else t[:-2] + "…"
        return t

    logo = _images(ecole).get("logo")
    for i, (e, classe_nom, photo) in enumerate(eleves):
        if i and i % (cols * lignes) == 0:
            c.showPage()
        k = i % (cols * lignes)
        x = marge_x + (k % cols) * (L + marge_x)
        y = A4[1] - marge_y - (k // cols + 1) * H - (k // cols) * marge_y
        # Fond et bandeau
        c.setStrokeColor(colors.HexColor("#cbd5e1"))
        c.setFillColor(colors.white)
        c.roundRect(x, y, L, H, 3 * mm, stroke=1, fill=1)
        c.setFillColor(BLEU)
        c.roundRect(x, y + H - 11 * mm, L, 11 * mm, 3 * mm, stroke=0, fill=1)
        c.rect(x, y + H - 11 * mm, L, 4 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 3 * mm, y + H - 5 * mm, texte_coupe(ecole.nom, "Helvetica-Bold", 9, L - (16 if logo else 6) * mm))
        c.setFont("Helvetica", 6.5)
        c.drawString(x + 3 * mm, y + H - 9 * mm, f"CARTE SCOLAIRE  ·  {ecole.annee_scolaire}")
        if logo:   # logo sur une pastille blanche, à droite du bandeau
            c.setFillColor(colors.white)
            c.circle(x + L - 6.5 * mm, y + H - 5.5 * mm, 4.6 * mm, stroke=0, fill=1)
            c.drawImage(ImageReader(io.BytesIO(logo)), x + L - 10.3 * mm, y + H - 9.3 * mm, 7.6 * mm, 7.6 * mm,
                        preserveAspectRatio=True, anchor="c", mask="auto")
        # Photo
        px, py, pl, ph = x + 3 * mm, y + 4 * mm, 21 * mm, 28 * mm
        if photo:
            c.drawImage(ImageReader(io.BytesIO(photo)), px, py, pl, ph, preserveAspectRatio=True, anchor="c")
        else:
            c.setFillColor(FOND)
            c.rect(px, py, pl, ph, stroke=0, fill=1)
            c.setFillColor(BLEU)
            c.setFont("Helvetica-Bold", 16)
            c.drawCentredString(px + pl / 2, py + ph / 2 - 3 * mm, (e.prenom[:1] + e.nom[:1]).upper())
        c.setStrokeColor(colors.HexColor("#cbd5e1"))
        c.rect(px, py, pl, ph, stroke=1, fill=0)
        # Informations
        tx, larg = px + pl + 3 * mm, L - pl - 9 * mm
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(tx, y + H - 16.5 * mm, texte_coupe(e.nom.upper(), "Helvetica-Bold", 10, larg))
        c.setFont("Helvetica", 9)
        c.drawString(tx, y + H - 20.5 * mm, texte_coupe(e.prenom, "Helvetica", 9, larg))
        lignes_info = [("Classe", classe_nom), ("Matricule", e.matricule or "–")]
        if e.date_naissance:
            lignes_info.append(("Né(e) le", date_fr(e.date_naissance)))
        if e.telephone_parent:
            lignes_info.append(("Parent", e.telephone_parent))
        yy = y + H - 26 * mm
        for lab, val in lignes_info:
            c.setFont("Helvetica", 6.5)
            c.setFillColor(GRIS)
            c.drawString(tx, yy, lab)
            c.setFont("Helvetica-Bold", 7.5)
            c.setFillColor(colors.black)
            c.drawString(tx + 13 * mm, yy, texte_coupe(str(val), "Helvetica-Bold", 7.5, larg - 26 * mm))
            yy -= 4 * mm
        # QR code (matricule)
        if e.matricule:
            qr = QrCodeWidget(e.matricule)
            b = qr.getBounds()
            taille = 13 * mm
            d = Drawing(taille, taille, transform=[taille / (b[2] - b[0]), 0, 0, taille / (b[3] - b[1]), 0, 0])
            d.add(qr)
            renderPDF.draw(d, c, x + L - taille - 2 * mm, y + 2 * mm)
    c.save()
    return buf.getvalue()


# ---------- Coupons « accès espace parents » ----------
def pdf_codes_parents(ecole: Ecole, lignes: list, adresse: str) -> bytes:
    """`lignes` : (Eleve, nom de classe, code). 8 coupons par page, à découper."""
    from reportlab.pdfgen import canvas as rl_canvas
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Codes parents — {ecole.nom}")
    marge = 12 * mm
    larg = (A4[0] - 2 * marge) / 2
    haut = (A4[1] - 2 * marge) / 4
    for i, (e, classe_nom, code) in enumerate(lignes):
        if i and i % 8 == 0:
            c.showPage()
        k = i % 8
        x = marge + (k % 2) * larg
        y = A4[1] - marge - (k // 2 + 1) * haut
        c.setDash(3, 3)
        c.setStrokeColor(colors.HexColor("#94a3b8"))
        c.rect(x, y, larg, haut)
        c.setDash()
        cx = x + 6 * mm
        c.setFillColor(BLEU)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(cx, y + haut - 10 * mm, ecole.nom[:40])
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9)
        c.drawString(cx, y + haut - 16 * mm, "Espace parents : notes, absences, paiements et reçus")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(cx, y + haut - 24 * mm, f"{e.prenom} {e.nom} — {classe_nom}"[:48])
        c.setFont("Helvetica", 8.5)
        c.drawString(cx, y + haut - 31 * mm, "Adresse :")
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(cx + 15 * mm, y + haut - 31 * mm, adresse[:60])
        c.setFont("Helvetica", 9)
        c.drawString(cx, y + haut - 39 * mm, "Matricule :")
        c.drawString(cx, y + haut - 47 * mm, "Code :")
        c.setFont("Courier-Bold", 14)
        c.drawString(cx + 22 * mm, y + haut - 39.5 * mm, e.matricule or "")
        c.drawString(cx + 22 * mm, y + haut - 47.5 * mm, code)
        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(GRIS)
        c.drawString(cx, y + 6 * mm, "Code personnel : ne le partagez pas.")
    c.save()
    return buf.getvalue()


# ---------- Emploi du temps ----------
def pdf_emploi_du_temps(ecole: Ecole, titre: str, creneaux: list, par_classe: bool = True) -> bytes:
    from app.models import JOURS
    buf = io.BytesIO()
    marge = 12 * mm
    page = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=marge, rightMargin=marge, topMargin=marge,
                            bottomMargin=marge, title=f"Emploi du temps — {titre}", author=ecole.nom)
    largeur = page[0] - 2 * marge
    jours = [j for j in range(1, 7) if j <= 5 or any(c.jour == 6 for c in creneaux)]
    horaires = sorted({(c.debut, c.fin) for c in creneaux})
    S_C = ParagraphStyle("c", parent=S_NORMAL, fontSize=8, leading=9.5, alignment=TA_CENTER)
    data = [["Horaire"] + [JOURS[j - 1] for j in jours]]
    for debut, fin in horaires:
        ligne = [f"{debut}\n{fin}"]
        for j in jours:
            cs = [c for c in creneaux if c.jour == j and c.debut == debut and c.fin == fin]
            ligne.append(Paragraph("<br/>".join(
                f"<b>{c.matiere}</b><br/><font color='#6b7280'>{(c.enseignant or '') if par_classe else c.classe}"
                f"{' · ' + c.salle if c.salle else ''}</font>" for c in cs), S_C) if cs else "")
        data.append(ligne)
    if not horaires:
        data.append(["", "Aucun cours"] + [""] * (len(jours) - 1))
    l0 = 22 * mm
    t = Table(data, colWidths=[l0] + [(largeur - l0) / len(jours)] * len(jours), repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLEU), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (0, 1), (0, -1), FOND), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    t.setStyle(TableStyle(style))
    total = sum((int(c.fin[:2]) * 60 + int(c.fin[3:])) - (int(c.debut[:2]) * 60 + int(c.debut[3:])) for c in creneaux)
    doc.build(_entete_ecole(ecole) + [
        Paragraph(f"EMPLOI DU TEMPS — {titre}", S_TITRE),
        Paragraph(f"{len(creneaux)} cours · {total // 60} h {total % 60:02d} par semaine", S_INFO),
        Spacer(1, 8), t,
    ])
    return buf.getvalue()


# ---------- Fiche de paie ----------
def pdf_fiche_paie(ecole: Ecole, f, avances: list) -> bytes:
    buf = io.BytesIO()
    marge = 18 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=marge, rightMargin=marge, topMargin=marge,
                            bottomMargin=marge, title=f"Fiche de paie {f.mois} — {f.nom}", author=ecole.nom)
    largeur = A4[0] - 2 * marge
    d = ecole.devise
    annee, m = f.mois.split("-")
    mois_txt = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
                "octobre", "novembre", "décembre"][int(m) - 1] + " " + annee
    lignes = [["Libellé", "Base", "Montant"]]
    if f.type == "horaire":
        lignes.append([f"Heures de cours ({f.heures:g} h × {argent(f.taux, d)})", f"{f.heures:g} h", argent(f.brut, d)])
    else:
        lignes.append(["Salaire mensuel", "", argent(f.brut, d)])
    if f.primes:
        lignes.append(["Primes", "", "+ " + argent(f.primes, d)])
    if f.retenues:
        lignes.append(["Retenues (absences, autres)", "", "− " + argent(f.retenues, d)])
    for a in avances:
        lignes.append([f"Avance du {date_fr(a.jour)}" + (f" ({a.motif})" if a.motif else ""), "", "− " + argent(a.montant, d)])
    lignes.append(["NET À PAYER", "", argent(f.net, d)])
    t = Table(lignes, colWidths=[largeur * 0.58, largeur * 0.14, largeur * 0.28])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLEU), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), FOND), ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    etat = (f"Versé le {date_fr(f.paye_le)}" if f.payee else "Non encore versé")
    doc.build(_entete_ecole(ecole) + [
        Paragraph(f"FICHE DE PAIE — {mois_txt.upper()}", S_TITRE),
        _tableau_infos([("Nom", f.nom), ("Fonction", f.role),
                        ("Contrat", "Salaire mensuel" if f.type == "mensuel" else f"À l'heure ({argent(f.taux, d)} / h)")],
                       largeur),
        Spacer(1, 10), t, Spacer(1, 6),
        Paragraph(f"<i>{en_lettres(f.net).capitalize()} {d}</i> — {etat}", S_PETIT),
        *( [Spacer(1, 4), Paragraph(f"Note : {f.note}", S_PETIT)] if f.note else [] ),
        Spacer(1, 22),
        _signatures("Signature de l'employé", "Le directeur", largeur, ecole),
    ])
    return buf.getvalue()
