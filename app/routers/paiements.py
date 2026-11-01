from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import Eleve, Paiement, PaiementCreate, Solde
from app.routers.eleves import calculer_solde

router = APIRouter(tags=["Paiements"])


@router.post("/paiements", response_model=Paiement, status_code=201)
def enregistrer_paiement(data: PaiementCreate, session: Session = Depends(get_session)):
    if not session.get(Eleve, data.eleve_id):
        raise HTTPException(400, "Élève introuvable")
    paiement = Paiement.model_validate(data)
    session.add(paiement)
    session.commit()
    session.refresh(paiement)
    return paiement


@router.get("/eleves/{eleve_id}/paiements", response_model=list[Paiement])
def historique_paiements(eleve_id: int, session: Session = Depends(get_session)):
    if not session.get(Eleve, eleve_id):
        raise HTTPException(404, "Élève introuvable")
    return session.exec(
        select(Paiement).where(Paiement.eleve_id == eleve_id).order_by(Paiement.date_paiement)
    ).all()


@router.get("/impayes", response_model=list[Solde])
def liste_impayes(classe_id: int | None = None, session: Session = Depends(get_session)):
    """Élèves qui doivent encore de l'argent, du plus gros montant au plus petit."""
    stmt = select(Eleve)
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    soldes = [calculer_solde(session, e) for e in session.exec(stmt).all()]
    return sorted(
        (s for s in soldes if s.reste_a_payer > 0),
        key=lambda s: s.reste_a_payer,
        reverse=True,
    )
