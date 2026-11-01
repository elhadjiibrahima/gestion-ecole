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


def _entete_ecole(ecole: Ecole) -> list:
    infos = " · ".join(x for x in (ecole.adresse, ecole.telephone and f"Tél : {ecole.telephone}") if x)
    elems = [Paragraph(ecole.nom, S_ECOLE)]
    if infos:
        elems.append(Paragraph(infos, S_INFO))
    elems.append(Paragraph(f"Année scolaire {ecole.annee_scolaire}", S_INFO))
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


def _signatures(gauche: str, droite: str, largeur: float) -> Table:
    t = Table([[gauche, droite], ["", ""], ["", ""], ["", ""]],
              colWidths=[largeur / 2, largeur / 2], rowHeights=[14, 14, 14, 14])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    return t


# ---------- Reçu de paiement ----------
def pdf_recu(ecole: Ecole, paiement: Paiement, eleve: Eleve, classe: Classe,
             solde: Solde, caissier: str | None) -> bytes:
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

    situation = Table(
        [["Frais annuels", argent(solde.frais_annuels, d)],
         ["Total payé à ce jour", argent(solde.total_paye, d)],
         ["Reste à payer", argent(solde.reste_a_payer, d)]],
        colWidths=[largeur * 0.6, largeur * 0.4],
    )
    situation.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.lightgrey),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("TEXTCOLOR", (1, 2), (1, 2), colors.HexColor("#dc2626") if solde.reste_a_payer else colors.HexColor("#059669")),
    ]))

    infos = [
        ("Élève", f"{eleve.prenom} {eleve.nom}"),
        ("Matricule", eleve.matricule or "–"),
        ("Classe", classe.nom),
        ("Date du paiement", date_fr(paiement.date_paiement)),
        ("Mode de paiement", MODES.get(paiement.mode.value, paiement.mode.value)),
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
        _signatures("Le parent", f"Le caissier{' : ' + caissier if caissier else ''}", largeur),
        Spacer(1, 6),
        Paragraph(f"Édité le {date_fr(date.today())} — conservez ce reçu.", S_PETIT),
    ]
    doc.build(elems)
    return buf.getvalue()


# ---------- Bulletins ----------
def _page_bulletin(ecole: Ecole, b: Bulletin, largeur: float) -> list:
    entetes = ["Matière", "Coef.", "Moyenne /20", "Points"]
    lignes = [[m.matiere, str(m.coefficient), f"{m.moyenne:.2f}", f"{m.moyenne * m.coefficient:.2f}"]
              for m in b.matieres]
    total_coef = sum(m.coefficient for m in b.matieres)
    total_pts = sum(m.moyenne * m.coefficient for m in b.matieres)
    if not lignes:
        lignes = [["Aucune note pour ce trimestre", "", "", ""]]
    pied = ["TOTAL", str(total_coef or ""), "", f"{total_pts:.2f}" if total_coef else ""]

    t = Table([entetes] + lignes + [pied],
              colWidths=[largeur * 0.46, largeur * 0.14, largeur * 0.2, largeur * 0.2], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLEU),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), FOND),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, m in enumerate(b.matieres, start=1):
        if m.moyenne < 10:
            style.append(("TEXTCOLOR", (2, i), (2, i), colors.HexColor("#dc2626")))
    t.setStyle(TableStyle(style))

    moy = "–" if b.moyenne_generale is None else f"{b.moyenne_generale:.2f} / 20"
    rang = "–" if b.rang is None else f"{b.rang}{'er' if b.rang == 1 else 'e'} sur {b.effectif}"
    resume = Table(
        [[Paragraph("<b>Moyenne générale</b>", S_NORMAL), Paragraph(f"<font size=13><b>{moy}</b></font>", S_DROITE)],
         [Paragraph("<b>Rang</b>", S_NORMAL), Paragraph(f"<b>{rang}</b>", S_DROITE)],
         [Paragraph("<b>Appréciation</b>", S_NORMAL), Paragraph(b.appreciation, S_DROITE)],
         [Paragraph("<b>Assiduité</b>", S_NORMAL), Paragraph(
             f"{b.absences} absence(s) dont {b.absences_justifiees} justifiée(s) · {b.retards} retard(s)", S_DROITE)]],
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
        _signatures("Signature du parent", "Le directeur", largeur),
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
        _signatures("Le comptable", "Le directeur", largeur),
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
        c.drawString(x + 3 * mm, y + H - 5 * mm, texte_coupe(ecole.nom, "Helvetica-Bold", 9, L - 6 * mm))
        c.setFont("Helvetica", 6.5)
        c.drawString(x + 3 * mm, y + H - 9 * mm, f"CARTE SCOLAIRE  ·  {ecole.annee_scolaire}")
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
