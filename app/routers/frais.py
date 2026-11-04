"""Autres frais que la scolarité : inscription, cantine, transport, tenue…"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from app.database import get_session
from app.models import (
    FraisEleve, Paiement, Role, SouscriptionFrais, TypeFrais, TypeFraisSaisie, Utilisateur,
)
from app.security import GESTION, TOUS, exiger_role
from app.services import annee_courante, get_eleve

router = APIRouter(tags=["Autres frais"])


def _get(session: Session, type_id: int, ecole_id: int) -> TypeFrais:
    t = session.get(TypeFrais, type_id)
    if not t or t.ecole_id != ecole_id:
        raise HTTPException(404, "Frais introuvable")
    return t


@router.get("/frais", response_model=list[TypeFrais])
def lister(user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    return session.exec(select(TypeFrais).where(TypeFrais.ecole_id == user.ecole_id, TypeFrais.actif == True)  # noqa: E712
                        .order_by(TypeFrais.ordre, TypeFrais.id)).all()


@router.post("/frais", response_model=TypeFrais, status_code=201)
def creer(data: TypeFraisSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
          session: Session = Depends(get_session)):
    if data.nom.strip().lower() == "scolarité":
        raise HTTPException(400, "La scolarité se règle dans chaque classe (frais annuels)")
    existe = session.exec(select(TypeFrais).where(TypeFrais.ecole_id == user.ecole_id, TypeFrais.actif == True,  # noqa: E712
                                                  func.lower(TypeFrais.nom) == data.nom.strip().lower())).first()
    if existe:
        raise HTTPException(400, f"« {data.nom} » existe déjà")
    nb = session.exec(select(func.count()).select_from(TypeFrais).where(TypeFrais.ecole_id == user.ecole_id)).one()
    t = TypeFrais(nom=data.nom.strip(), montant=data.montant, obligatoire=data.obligatoire, mensuel=data.mensuel,
                  ecole_id=user.ecole_id, ordre=nb)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@router.patch("/frais/{type_id}", response_model=TypeFrais)
def modifier(type_id: int, data: TypeFraisSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
             session: Session = Depends(get_session)):
    t = _get(session, type_id, user.ecole_id)
    if t.code:
        raise HTTPException(400, "Le montant de l'inscription se règle dans chaque classe (Élèves → Classes)")
    t.nom, t.montant, t.obligatoire, t.mensuel = data.nom.strip(), data.montant, data.obligatoire, data.mensuel
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@router.delete("/frais/{type_id}", status_code=204)
def supprimer(type_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
              session: Session = Depends(get_session)):
    """Retire un frais. S'il a déjà été payé, il est seulement désactivé (l'historique reste)."""
    t = _get(session, type_id, user.ecole_id)
    if t.code:
        raise HTTPException(400, "Pour ne plus demander d'inscription, mets son montant à 0 dans les classes")
    if session.exec(select(Paiement.id).where(Paiement.type_frais_id == t.id).limit(1)).first():
        t.actif = False
        session.add(t)
    else:
        for s in session.exec(select(SouscriptionFrais).where(SouscriptionFrais.type_frais_id == t.id)).all():
            session.delete(s)
        session.delete(t)
    session.commit()


def _debut_souscription(session: Session, ecole_id: int):
    """Mois à partir duquel compte une nouvelle souscription : aucun si l'année n'a pas commencé."""
    from datetime import date
    from app.models import Ecole
    from app.services import mensualites
    mois = mensualites(session.get(Ecole, ecole_id))
    auj = date.today()
    return auj.replace(day=1) if mois and auj > mois[0].date_limite.replace(day=1) else None


@router.get("/eleves/{eleve_id}/frais", response_model=list[FraisEleve])
def frais_de_l_eleve(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                     session: Session = Depends(get_session)):
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    annee = annee_courante(session, user.ecole_id)
    souscrits = set(session.exec(select(SouscriptionFrais.type_frais_id).where(
        SouscriptionFrais.eleve_id == eleve.id, SouscriptionFrais.annee_scolaire == annee)).all())
    return [FraisEleve(type_frais_id=t.id, nom=t.nom, montant=t.montant, obligatoire=t.obligatoire, mensuel=t.mensuel,
                       souscrit=t.obligatoire or t.id in souscrits)
            for t in lister(user, session)]


@router.put("/eleves/{eleve_id}/frais", response_model=list[FraisEleve])
def choisir_frais(eleve_id: int, types: list[int], user: Utilisateur = Depends(exiger_role(*GESTION)),
                  session: Session = Depends(get_session)):
    """Frais facultatifs de l'élève pour l'année (ex : cantine, transport). Remplace la liste."""
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    annee = annee_courante(session, user.ecole_id)
    facultatifs = {t.id for t in lister(user, session) if not t.obligatoire}
    if set(types) - facultatifs:
        raise HTTPException(400, "Frais inconnu ou obligatoire")
    existantes = {s.type_frais_id: s for s in session.exec(select(SouscriptionFrais).where(
        SouscriptionFrais.eleve_id == eleve.id, SouscriptionFrais.annee_scolaire == annee)).all()}
    for tid, s in existantes.items():
        if tid not in types:
            session.delete(s)
    debut = _debut_souscription(session, user.ecole_id)
    for tid in set(types) - set(existantes):
        # un frais au mois pris en cours d'année n'est dû qu'à partir du mois en cours
        session.add(SouscriptionFrais(eleve_id=eleve.id, type_frais_id=tid, annee_scolaire=annee, debut=debut))
    session.commit()
    return frais_de_l_eleve(eleve_id, user, session)
