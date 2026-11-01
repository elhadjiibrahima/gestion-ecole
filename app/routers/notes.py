from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import CaseNote, Eleve, Note, NoteCreate, SaisieNote, Utilisateur
from app.routers.matieres import matieres_ecole
from app.security import PEDAGOGIE, TOUS, exiger_role
from app.services import get_eleve

router = APIRouter(prefix="/notes", tags=["Notes"])


@router.post("", response_model=Note, status_code=201)
def ajouter_note(
    data: NoteCreate,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    get_eleve(session, data.eleve_id, user.ecole_id, code=400)
    note = Note.model_validate(data)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.get("", response_model=list[Note])
def notes_eleve(
    eleve_id: int,
    trimestre: int | None = None,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    get_eleve(session, eleve_id, user.ecole_id)
    stmt = select(Note).where(Note.eleve_id == eleve_id)
    if trimestre:
        stmt = stmt.where(Note.trimestre == trimestre)
    return session.exec(stmt.order_by(Note.matiere, Note.id)).all()


def _verifier_trimestre(trimestre: int) -> None:
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")


def _grille(session: Session, eleve: Eleve, trimestre: int) -> list[CaseNote]:
    notes = session.exec(
        select(Note).where(Note.eleve_id == eleve.id, Note.trimestre == trimestre).order_by(Note.id)
    ).all()
    par_matiere: dict[str, list[Note]] = {}
    for n in notes:
        par_matiere.setdefault(n.matiere.lower(), []).append(n)

    def valeur(ns):  # plusieurs anciennes notes dans la même matière : on affiche leur moyenne
        return round(sum(n.valeur for n in ns) / len(ns), 2) if ns else None

    cases = []
    for m in matieres_ecole(session, eleve.ecole_id):
        cases.append(CaseNote(matiere_id=m.id, matiere=m.nom, coefficient=m.coefficient,
                              valeur=valeur(par_matiere.pop(m.nom.lower(), []))))
    for ns in par_matiere.values():  # notes d'une matière qui n'est plus dans la liste
        cases.append(CaseNote(matiere=ns[0].matiere, coefficient=ns[0].coefficient, valeur=valeur(ns)))
    return cases


@router.get("/grille", response_model=list[CaseNote])
def grille(
    eleve_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Une case par matière, avec la note de l'élève pour le trimestre (ou vide)."""
    _verifier_trimestre(trimestre)
    return _grille(session, get_eleve(session, eleve_id, user.ecole_id), trimestre)


@router.put("/grille", response_model=list[CaseNote])
def enregistrer_grille(
    eleve_id: int,
    trimestre: int,
    saisies: list[SaisieNote],
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    """Enregistre la grille : une note par matière. Une case vide efface la note."""
    _verifier_trimestre(trimestre)
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    matieres = {m.id: m for m in matieres_ecole(session, user.ecole_id)}
    for s in saisies:
        m = matieres.get(s.matiere_id)
        if not m:
            raise HTTPException(400, "Matière introuvable")
        anciennes = session.exec(
            select(Note).where(Note.eleve_id == eleve.id, Note.trimestre == trimestre, Note.matiere == m.nom)
        ).all()
        for n in anciennes:
            session.delete(n)
        if s.valeur is not None:
            session.add(Note(eleve_id=eleve.id, matiere=m.nom, coefficient=m.coefficient,
                             valeur=s.valeur, trimestre=trimestre))
    session.commit()
    return _grille(session, eleve, trimestre)


@router.delete("/{note_id}", status_code=204)
def supprimer_note(
    note_id: int,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    note = session.get(Note, note_id)
    eleve = session.get(Eleve, note.eleve_id) if note else None
    if not eleve or eleve.ecole_id != user.ecole_id:
        raise HTTPException(404, "Note introuvable")
    session.delete(note)
    session.commit()
