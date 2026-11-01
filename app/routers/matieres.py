from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from app.database import get_session
from app.models import (
    MATIERES_PAR_DEFAUT, Eleve, Matiere, MatiereCreate, MatiereUpdate, Note, Role, Utilisateur,
)
from app.security import TOUS, exiger_role

router = APIRouter(prefix="/matieres", tags=["Matières"])


def matieres_ecole(session: Session, ecole_id: int) -> list[Matiere]:
    return session.exec(
        select(Matiere).where(Matiere.ecole_id == ecole_id).order_by(Matiere.ordre, Matiere.id)
    ).all()


def ajouter_matieres_defaut(session: Session, ecole_id: int) -> int:
    """Ajoute les matières par défaut qui n'existent pas encore. Renvoie le nombre ajouté."""
    existantes = {m.nom.lower() for m in matieres_ecole(session, ecole_id)}
    ordre = len(existantes)
    ajoutees = 0
    for nom, coef in MATIERES_PAR_DEFAUT:
        if nom.lower() not in existantes:
            session.add(Matiere(nom=nom, coefficient=coef, ecole_id=ecole_id, ordre=ordre))
            ordre += 1
            ajoutees += 1
    return ajoutees


def _get(session: Session, matiere_id: int, ecole_id: int) -> Matiere:
    m = session.get(Matiere, matiere_id)
    if not m or m.ecole_id != ecole_id:
        raise HTTPException(404, "Matière introuvable")
    return m


def _nom_libre(session: Session, ecole_id: int, nom: str, sauf_id: int | None = None) -> str:
    nom = nom.strip()
    if not nom:
        raise HTTPException(400, "Le nom de la matière est vide")
    doublon = session.exec(
        select(Matiere).where(Matiere.ecole_id == ecole_id, func.lower(Matiere.nom) == nom.lower())
    ).first()
    if doublon and doublon.id != sauf_id:
        raise HTTPException(400, f"La matière « {nom} » existe déjà")
    return nom


def _notes_de_l_ecole(session: Session, ecole_id: int, nom: str):
    return session.exec(
        select(Note).join(Eleve, Note.eleve_id == Eleve.id)
        .where(Eleve.ecole_id == ecole_id, Note.matiere == nom)
    ).all()


@router.get("", response_model=list[Matiere])
def lister(user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    return matieres_ecole(session, user.ecole_id)


@router.post("", response_model=Matiere, status_code=201)
def creer(
    data: MatiereCreate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    nom = _nom_libre(session, user.ecole_id, data.nom)
    ordre = len(matieres_ecole(session, user.ecole_id))
    m = Matiere(nom=nom, coefficient=data.coefficient, ecole_id=user.ecole_id, ordre=ordre)
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@router.post("/defaut", response_model=list[Matiere])
def creer_defaut(user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    """Ajoute les matières usuelles du collège qui manquent."""
    ajouter_matieres_defaut(session, user.ecole_id)
    session.commit()
    return matieres_ecole(session, user.ecole_id)


@router.patch("/{matiere_id}", response_model=Matiere)
def modifier(
    matiere_id: int,
    data: MatiereUpdate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Renommer ou changer le coefficient : les notes déjà saisies suivent."""
    m = _get(session, matiere_id, user.ecole_id)
    notes = _notes_de_l_ecole(session, user.ecole_id, m.nom)
    if data.nom is not None:
        m.nom = _nom_libre(session, user.ecole_id, data.nom, sauf_id=m.id)
    if data.coefficient is not None:
        m.coefficient = data.coefficient
    for n in notes:
        n.matiere, n.coefficient = m.nom, m.coefficient
        session.add(n)
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@router.delete("/{matiere_id}", status_code=204)
def supprimer(
    matiere_id: int,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    m = _get(session, matiere_id, user.ecole_id)
    if _notes_de_l_ecole(session, user.ecole_id, m.nom):
        raise HTTPException(400, f"Impossible : des notes existent déjà en « {m.nom} ». Efface-les d'abord.")
    session.delete(m)
    session.commit()
