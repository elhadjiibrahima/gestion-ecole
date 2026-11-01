from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import Classe, ClasseCreate, Eleve

router = APIRouter(prefix="/classes", tags=["Classes"])


@router.post("", response_model=Classe, status_code=201)
def creer_classe(data: ClasseCreate, session: Session = Depends(get_session)):
    classe = Classe.model_validate(data)
    session.add(classe)
    session.commit()
    session.refresh(classe)
    return classe


@router.get("", response_model=list[Classe])
def lister_classes(session: Session = Depends(get_session)):
    return session.exec(select(Classe).order_by(Classe.nom)).all()


@router.get("/{classe_id}/eleves", response_model=list[Eleve])
def eleves_de_la_classe(classe_id: int, session: Session = Depends(get_session)):
    if not session.get(Classe, classe_id):
        raise HTTPException(404, "Classe introuvable")
    return session.exec(
        select(Eleve).where(Eleve.classe_id == classe_id).order_by(Eleve.nom)
    ).all()
