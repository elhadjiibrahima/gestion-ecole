"""Exports Excel : les données de l'école restent récupérables à tout moment."""
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlmodel import Session, select

from app.database import get_session
from app.models import Classe, Ecole, Eleve, Paiement, Utilisateur
from app.pdf import en_tete_pdf
from app.routers.paiements import details_paiements
from app.security import GESTION, PEDAGOGIE, exiger_role
from app.services import bulletins_classe, matieres_ecole, soldes_ecole
from app.services import cle_classe, trier_classes

router = APIRouter(prefix="/export", tags=["Exports"])

MODES = {"especes": "Espèces", "orange_money": "Orange Money", "mtn_momo": "MTN MoMo", "wave": "Wave",
         "free_money": "Free Money", "virement": "Virement", "cheque": "Chèque"}
STATUTS = {"a_jour": "À jour", "partiel": "Partiel", "rien_paye": "Rien payé"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _feuille(wb: Workbook, titre: str, entetes: list[str], lignes: list[list], formats: dict[int, str] | None = None):
    ws = wb.create_sheet(titre[:31])
    ws.append(entetes)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1D4ED8")
    for l in lignes:
        ws.append(l)
    for i, h in enumerate(entetes, start=1):
        largeur = max([len(str(h))] + [len(str(l[i - 1])) for l in lignes if i - 1 < len(l) and l[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, largeur + 2), 45)
    for col, fmt in (formats or {}).items():
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            row[0].number_format = fmt
    ws.freeze_panes = "A2"
    if lignes:
        ws.auto_filter.ref = ws.dimensions
    return ws


def _reponse(wb: Workbook, nom: str) -> Response:
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    entete = en_tete_pdf(nom)
    entete["Content-Disposition"] = entete["Content-Disposition"].replace("inline", "attachment")
    return Response(buf.getvalue(), media_type=XLSX, headers=entete)


def _suffixe(ecole: Ecole) -> str:
    return f"{ecole.nom}_{date.today():%Y-%m-%d}"


@router.get("/eleves.xlsx", response_class=Response)
def export_eleves(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Élèves inscrits avec leur situation de paiement (année en cours)."""
    ecole = session.get(Ecole, user.ecole_id)
    soldes = {s.eleve_id: s for s in soldes_ecole(session, user.ecole_id)}
    eleves = session.exec(select(Eleve, Classe).join(Classe, Eleve.classe_id == Classe.id)
                          .where(Eleve.ecole_id == user.ecole_id, Eleve.actif == True)  # noqa: E712
                          .order_by(Classe.nom, Eleve.nom, Eleve.prenom)).all()
    lignes = []
    for e, c in eleves:
        s = soldes[e.id]
        lignes.append([e.matricule, e.nom, e.prenom, c.nom, e.date_naissance, e.telephone_parent,
                       s.frais_annuels, s.total_paye, s.reste_a_payer, STATUTS[s.statut.value],
                       e.sexe, e.lieu_naissance, e.nom_tuteur, e.adresse])
    lignes.sort(key=lambda l: (cle_classe(l[3]), l[1], l[2]))
    wb = Workbook()
    _feuille(wb, "Eleves", ["Matricule", "Nom", "Prénom", "Classe", "Date de naissance", "Téléphone parent",
                            f"Frais ({ecole.devise})", "Payé", "Reste", "Statut", "Sexe", "Lieu de naissance",
                            "Parent / tuteur", "Adresse"],
             lignes, {5: "DD/MM/YYYY", 7: "#,##0", 8: "#,##0", 9: "#,##0"})
    return _reponse(wb, f"eleves_{_suffixe(ecole)}.xlsx")


@router.get("/paiements.xlsx", response_class=Response)
def export_paiements(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Tous les paiements (toutes années), annulés compris."""
    ecole = session.get(Ecole, user.ecole_id)
    rows = session.exec(select(Paiement, Eleve, Classe)
                        .join(Eleve, Paiement.eleve_id == Eleve.id).join(Classe, Eleve.classe_id == Classe.id)
                        .where(Eleve.ecole_id == user.ecole_id)
                        .order_by(Paiement.date_paiement, Paiement.id)).all()
    details = {d.id: d for d in details_paiements(session, [p for p, _, _ in rows])}
    lignes = []
    for p, e, c in rows:
        d = details[p.id]
        lignes.append([p.numero_recu, p.date_paiement, p.annee_scolaire, e.matricule, f"{e.nom} {e.prenom}", c.nom,
                       d.frais, p.montant, MODES.get(p.mode.value, p.mode.value), p.reference, d.enregistre_par,
                       "ANNULÉ" if p.annule else "Valide", p.motif_annulation, d.annule_par])
    wb = Workbook()
    _feuille(wb, "Paiements", ["N° reçu", "Date", "Année scolaire", "Matricule", "Élève", "Classe", "Frais",
                               f"Montant ({ecole.devise})", "Mode", "Référence", "Enregistré par",
                               "État", "Motif d'annulation", "Annulé par"],
             lignes, {2: "DD/MM/YYYY", 8: "#,##0"})
    return _reponse(wb, f"paiements_{_suffixe(ecole)}.xlsx")


@router.get("/notes.xlsx", response_class=Response)
def export_notes(
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    """Une feuille par classe : moyennes par matière, moyenne générale, rang, absences."""
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    ecole = session.get(Ecole, user.ecole_id)
    matieres = [m.nom for m in matieres_ecole(session, user.ecole_id)]
    wb = Workbook()
    classes = trier_classes(session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all())
    for classe in classes:
        bulletins = bulletins_classe(session, classe, trimestre)
        autres = sorted({l.matiere for b in bulletins for l in b.matieres} - set(matieres))
        colonnes = matieres + autres
        lignes = []
        for b in sorted(bulletins, key=lambda b: (b.rang is None, b.rang or 0, b.nom_complet)):
            moy = {l.matiere: l.moyenne for l in b.matieres}
            lignes.append([b.matricule, b.nom_complet] + [moy.get(m) for m in colonnes]
                          + [b.moyenne_generale, b.rang, b.appreciation, b.absences, b.retards])
        _feuille(wb, classe.nom, ["Matricule", "Élève"] + colonnes
                 + ["Moyenne générale", "Rang", "Appréciation", "Absences", "Retards"], lignes)
    if not classes:
        _feuille(wb, "Notes", ["Aucune classe"], [])
    return _reponse(wb, f"notes_T{trimestre}_{_suffixe(ecole)}.xlsx")
