"""Caisse de l'école : dépenses et bilan des entrées / sorties."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from openpyxl import Workbook
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    AnnulationPaiement, BilanCaisse, CategorieDepense, Depense, DepenseCreate, DepenseDetail, Ecole, Eleve,
    MoisCaisse, Paiement, Role, Utilisateur,
)
from app.security import GESTION, exiger_role
from app.services import annee_courante

router = APIRouter(tags=["Caisse"])

CATEGORIES = {
    "salaires": "Salaires", "loyer": "Loyer", "fournitures": "Fournitures", "electricite_eau": "Électricité / eau",
    "transport": "Transport", "entretien": "Entretien / réparations", "impots": "Impôts / taxes", "autre": "Autre",
}


def _details(session: Session, depenses: list[Depense]) -> list[DepenseDetail]:
    ids = {d.saisi_par_id for d in depenses} | {d.annule_par_id for d in depenses}
    ids -= {None}
    noms = {u.id: u.nom for u in session.exec(select(Utilisateur).where(Utilisateur.id.in_(ids)))} if ids else {}
    return [DepenseDetail(id=d.id, jour=d.jour, categorie=d.categorie, libelle=d.libelle, montant=d.montant,
                          mode=d.mode, beneficiaire=d.beneficiaire, saisi_par=noms.get(d.saisi_par_id),
                          annule=d.annule, annule_par=noms.get(d.annule_par_id),
                          motif_annulation=d.motif_annulation) for d in depenses]


@router.post("/depenses", response_model=DepenseDetail, status_code=201)
def ajouter(
    data: DepenseCreate,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    d = Depense(**data.model_dump(), ecole_id=user.ecole_id, saisi_par_id=user.id,
                annee_scolaire=annee_courante(session, user.ecole_id))
    d.libelle = d.libelle.strip()
    session.add(d)
    session.commit()
    session.refresh(d)
    return _details(session, [d])[0]


@router.get("/depenses", response_model=list[DepenseDetail])
def lister(
    mois: str | None = None,
    categorie: CategorieDepense | None = None,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Dépenses de l'année en cours (annulées comprises), les plus récentes d'abord. `mois` : 2026-11."""
    depenses = session.exec(
        select(Depense).where(Depense.ecole_id == user.ecole_id,
                              Depense.annee_scolaire == annee_courante(session, user.ecole_id))
        .order_by(Depense.jour.desc(), Depense.id.desc())
    ).all()
    if mois:
        depenses = [d for d in depenses if d.jour.strftime("%Y-%m") == mois]
    if categorie:
        depenses = [d for d in depenses if d.categorie == categorie]
    return _details(session, depenses)


@router.post("/depenses/{depense_id}/annuler", response_model=DepenseDetail)
def annuler(
    depense_id: int,
    data: AnnulationPaiement,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    d = session.get(Depense, depense_id)
    if not d or d.ecole_id != user.ecole_id:
        raise HTTPException(404, "Dépense introuvable")
    if d.annule:
        raise HTTPException(400, "Cette dépense est déjà annulée")
    d.annule, d.annule_le, d.annule_par_id = True, datetime.now(timezone.utc), user.id
    d.motif_annulation = data.motif.strip()
    session.add(d)
    session.commit()
    session.refresh(d)
    return _details(session, [d])[0]


def bilan(session: Session, ecole_id: int) -> BilanCaisse:
    annee = annee_courante(session, ecole_id)
    paiements = session.exec(
        select(Paiement).join(Eleve, Paiement.eleve_id == Eleve.id)
        .where(Eleve.ecole_id == ecole_id, Paiement.annee_scolaire == annee, Paiement.annule == False)  # noqa: E712
    ).all()
    depenses = session.exec(
        select(Depense).where(Depense.ecole_id == ecole_id, Depense.annee_scolaire == annee,
                              Depense.annule == False)  # noqa: E712
    ).all()
    entrees: dict[str, int] = {}
    sorties: dict[str, int] = {}
    for p in paiements:
        k = p.date_paiement.strftime("%Y-%m")
        entrees[k] = entrees.get(k, 0) + p.montant
    par_categorie = {c: 0 for c in CATEGORIES}
    for d in depenses:
        k = d.jour.strftime("%Y-%m")
        sorties[k] = sorties.get(k, 0) + d.montant
        par_categorie[d.categorie.value] += d.montant
    cumul, mois = 0, []
    for k in sorted(set(entrees) | set(sorties)):
        e, s = entrees.get(k, 0), sorties.get(k, 0)
        cumul += e - s
        mois.append(MoisCaisse(mois=k, entrees=e, sorties=s, solde=e - s, cumul=cumul))
    te, ts = sum(entrees.values()), sum(sorties.values())
    return BilanCaisse(annee_scolaire=annee, total_entrees=te, total_sorties=ts, solde=te - ts, mois=mois,
                       par_categorie={k: v for k, v in par_categorie.items() if v})


@router.get("/caisse/bilan", response_model=BilanCaisse)
def bilan_caisse(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Entrées (paiements des élèves), sorties (dépenses) et solde, mois par mois."""
    return bilan(session, user.ecole_id)


@router.get("/export/caisse.xlsx", response_class=Response)
def export_caisse(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    from app.routers.exports import _feuille, _reponse, _suffixe, MODES
    ecole = session.get(Ecole, user.ecole_id)
    b = bilan(session, user.ecole_id)
    wb = Workbook()
    _feuille(wb, "Bilan mensuel", ["Mois", f"Entrées ({ecole.devise})", "Sorties", "Solde du mois", "Cumul"],
             [[m.mois, m.entrees, m.sorties, m.solde, m.cumul] for m in b.mois]
             + [["TOTAL", b.total_entrees, b.total_sorties, b.solde, None]],
             {i: "#,##0" for i in (2, 3, 4, 5)})
    deps = lister(None, None, user, session)
    _feuille(wb, "Dépenses", ["Date", "Catégorie", "Libellé", f"Montant ({ecole.devise})", "Mode", "Bénéficiaire",
                              "Saisi par", "État", "Motif d'annulation"],
             [[d.jour, CATEGORIES[d.categorie.value], d.libelle, d.montant, MODES.get(d.mode.value, d.mode.value),
               d.beneficiaire, d.saisi_par, "ANNULÉE" if d.annule else "Valide", d.motif_annulation]
              for d in reversed(deps)],
             {1: "DD/MM/YYYY", 4: "#,##0"})
    return _reponse(wb, f"caisse_{_suffixe(ecole)}.xlsx")
