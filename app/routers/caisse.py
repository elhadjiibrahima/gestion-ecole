"""Caisse de l'école : dépenses et bilan des entrées / sorties."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from openpyxl import Workbook
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    AnnulationPaiement, BilanCaisse, CaisseJour, CategorieDepense, Depense, DepenseCreate, DepenseDetail, Ecole, Eleve,
    LigneCaisseJour, MoisCaisse, Paiement, Recette, RecetteSaisie, Role, Utilisateur,
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
    from app.routers.guichet import alerter
    alerter(session, user.ecole_id, "annulation", f"Dépense « {d.libelle} » ({d.montant:,}) annulée par {user.nom} : "
            f"{d.motif_annulation}".replace(",", " "))
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
    recettes = session.exec(select(Recette).where(Recette.ecole_id == ecole_id, Recette.annee_scolaire == annee)).all()
    entrees: dict[str, int] = {}
    sorties: dict[str, int] = {}
    for p in paiements:
        k = p.date_paiement.strftime("%Y-%m")
        entrees[k] = entrees.get(k, 0) + p.montant
    for r in recettes:
        k = r.jour.strftime("%Y-%m")
        entrees[k] = entrees.get(k, 0) + r.montant
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
                       total_recettes=sum(r.montant for r in recettes),
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


# ---------- Autres entrées d'argent ----------
CATEGORIES_RECETTES = {"don": "Don", "vente": "Vente", "location": "Location", "subvention": "Subvention", "autre": "Autre"}


@router.post("/recettes", response_model=Recette, status_code=201)
def ajouter_recette(data: RecetteSaisie, user: Utilisateur = Depends(exiger_role(*GESTION)),
                    session: Session = Depends(get_session)):
    """Argent reçu en dehors des frais des élèves (don, vente de tenues, location de salle…)."""
    r = Recette(ecole_id=user.ecole_id, annee_scolaire=annee_courante(session, user.ecole_id),
                jour=data.jour or date.today(), categorie=data.categorie, libelle=data.libelle.strip(),
                montant=data.montant, mode=data.mode, saisi_par_id=user.id)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


@router.get("/recettes", response_model=list[Recette])
def lister_recettes(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    return session.exec(select(Recette).where(Recette.ecole_id == user.ecole_id,
                                              Recette.annee_scolaire == annee_courante(session, user.ecole_id))
                        .order_by(Recette.jour.desc(), Recette.id.desc())).all()


@router.delete("/recettes/{recette_id}", status_code=204)
def supprimer_recette(recette_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                      session: Session = Depends(get_session)):
    r = session.get(Recette, recette_id)
    if not r or r.ecole_id != user.ecole_id:
        raise HTTPException(404, "Entrée introuvable")
    session.delete(r)
    session.commit()


# ---------- Caisse du jour (contrôle du soir) ----------
MODES_FR = {"especes": "Espèces", "orange_money": "Orange Money", "mtn_momo": "MTN MoMo", "virement": "Virement",
            "wave": "Wave", "free_money": "Free Money"}


@router.get("/caisse/jour", response_model=CaisseJour)
def caisse_du_jour(jour: date | None = None, user: Utilisateur = Depends(exiger_role(*GESTION)),
                   session: Session = Depends(get_session)):
    """Tout ce qui est entré et sorti ce jour-là : par mode (l'argent liquide à compter) et par caissier."""
    jour = jour or date.today()
    noms = {u.id: u.nom for u in session.exec(select(Utilisateur).where(Utilisateur.ecole_id == user.ecole_id)).all()}
    lignes: list[LigneCaisseJour] = []
    for p, e in session.exec(select(Paiement, Eleve).join(Eleve, Paiement.eleve_id == Eleve.id)
                             .where(Eleve.ecole_id == user.ecole_id, Paiement.date_paiement == jour,
                                    Paiement.annule == False)).all():  # noqa: E712
        lignes.append(LigneCaisseJour(heure=p.cree_le, type="paiement", libelle=f"{e.prenom} {e.nom}",
                                      mode=p.mode.value, montant=p.montant, par=noms.get(p.enregistre_par_id),
                                      numero=p.numero_recu))
    for r in session.exec(select(Recette).where(Recette.ecole_id == user.ecole_id, Recette.jour == jour)).all():
        lignes.append(LigneCaisseJour(type="recette", libelle=f"{CATEGORIES_RECETTES.get(r.categorie, r.categorie)} : {r.libelle}",
                                      mode=r.mode.value, montant=r.montant, par=noms.get(r.saisi_par_id)))
    for d in session.exec(select(Depense).where(Depense.ecole_id == user.ecole_id, Depense.jour == jour,
                                                Depense.annule == False)).all():  # noqa: E712
        lignes.append(LigneCaisseJour(type="depense", libelle=f"{CATEGORIES.get(d.categorie.value)} : {d.libelle}",
                                      mode=d.mode.value, montant=-d.montant, par=noms.get(d.saisi_par_id)))
    par_mode: dict[str, int] = {}
    par_caissier: dict[str, int] = {}
    for l in lignes:
        par_mode[l.mode] = par_mode.get(l.mode, 0) + l.montant
        if l.montant > 0:
            par_caissier[l.par or "?"] = par_caissier.get(l.par or "?", 0) + l.montant
    entrees = sum(l.montant for l in lignes if l.montant > 0)
    sorties = -sum(l.montant for l in lignes if l.montant < 0)
    return CaisseJour(jour=jour, entrees=entrees, sorties=sorties, solde=entrees - sorties, par_mode=par_mode,
                      par_caissier=par_caissier, lignes=lignes)
