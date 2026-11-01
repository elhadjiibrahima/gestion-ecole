from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database import get_session
from app.models import Eleve, Note, NoteCreate

router = APIRouter(prefix="/notes", tags=["Notes"])


@router.post("", response_model=Note, status_code=201)
def ajouter_note(data: NoteCreate, session: Session = Depends(get_session)):
    if not session.get(Eleve, data.eleve_id):
        raise HTTPException(400, "Élève introuvable")
    note = Note.model_validate(data)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.delete("/{note_id}", status_code=204)
def supprimer_note(note_id: int, session: Session = Depends(get_session)):
    note = session.get(Note, note_id)
    if not note:
        raise HTTPException(404, "Note introuvable")
    session.delete(note)
    session.commit()
