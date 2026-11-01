"""Génération des PDF : reçus de paiement et bulletins de notes (ReportLab)."""
import io
import re
import unicodedata
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, A5
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

    elems = _entete_ecole(ecole) + [
        Paragraph(f"REÇU DE PAIEMENT N° {paiement.numero_recu}", S_TITRE),
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
         [Paragraph("<b>Appréciation</b>", S_NORMAL), Paragraph(b.appreciation, S_DROITE)]],
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
