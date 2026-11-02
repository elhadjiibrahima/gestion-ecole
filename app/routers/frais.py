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
    t = TypeFrais(nom=data.nom.strip(), montant=data.montant, obligatoire=data.obligatoire,
                  ecole_id=user.ecole_id, ordre=nb)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@router.patch("/frais/{type_id}", response_model=TypeFrais)
def modifier(type_id: int, data: TypeFraisSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
             session: Session = Depends(get_session)):
    t = _get(session, type_id, user.ecole_id)
    t.nom, t.montant, t.obligatoire = data.nom.strip(), data.montant, data.obligatoire
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@router.delete("/frais/{type_id}", status_code=204)
def supprimer(type_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
              session: Session = Depends(get_session)):
    """Retire un frais. S'il a déjà été payé, il est seulement désactivé (l'historique reste)."""
    t = _get(session, type_id, user.ecole_id)
    if session.exec(select(Paiement.id).where(Paiement.type_frais_id == t.id).limit(1)).first():
        t.actif = False
        session.add(t)
    else:
        for s in session.exec(select(SouscriptionFrais).where(SouscriptionFrais.type_frais_id == t.id)).all():
            session.delete(s)
        session.delete(t)
    session.commit()


@router.get("/eleves/{eleve_id}/frais", response_model=list[FraisEleve])
def frais_de_l_eleve(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                     session: Session = Depends(get_session)):
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    annee = annee_courante(session, user.ecole_id)
    souscrits = set(session.exec(select(SouscriptionFrais.type_frais_id).where(
        SouscriptionFrais.eleve_id == eleve.id, SouscriptionFrais.annee_scolaire == annee)).all())
    return [FraisEleve(type_frais_id=t.id, nom=t.nom, montant=t.montant, obligatoire=t.obligatoire,
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
    for s in session.exec(select(SouscriptionFrais).where(SouscriptionFrais.eleve_id == eleve.id,
                                                          SouscriptionFrais.annee_scolaire == annee)).all():
        session.delete(s)
    for tid in set(types):
        session.add(SouscriptionFrais(eleve_id=eleve.id, type_frais_id=tid, annee_scolaire=annee))
    session.commit()
    return frais_de_l_eleve(eleve_id, user, session)
