from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import Eleve, Note, NoteCreate, Utilisateur
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
