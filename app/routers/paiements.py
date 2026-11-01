from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import Classe, Ecole, Eleve, Paiement, PaiementCreate, Solde, Utilisateur
from app.pdf import en_tete_pdf, pdf_recu
from app.security import GESTION, exiger_role
from app.services import calculer_solde, get_eleve

router = APIRouter(tags=["Paiements"])


@router.post("/paiements", response_model=Paiement, status_code=201)
def enregistrer_paiement(
    data: PaiementCreate,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    get_eleve(session, data.eleve_id, user.ecole_id, code=400)
    paiement = Paiement.model_validate(data, update={"enregistre_par_id": user.id})
    session.add(paiement)
    session.flush()
    paiement.numero_recu = f"R{date.today().year}-{paiement.id:06d}"
    session.commit()
    session.refresh(paiement)
    return paiement


@router.get("/eleves/{eleve_id}/paiements", response_model=list[Paiement])
def historique_paiements(
    eleve_id: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    get_eleve(session, eleve_id, user.ecole_id)
    return session.exec(
        select(Paiement).where(Paiement.eleve_id == eleve_id).order_by(Paiement.date_paiement, Paiement.id)
    ).all()


@router.get("/paiements/{paiement_id}/recu.pdf", response_class=Response)
def recu_pdf(
    paiement_id: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    paiement = session.get(Paiement, paiement_id)
    eleve = session.get(Eleve, paiement.eleve_id) if paiement else None
    if not eleve or eleve.ecole_id != user.ecole_id:
        raise HTTPException(404, "Paiement introuvable")
    solde = calculer_solde(session, eleve)
    caissier = session.get(Utilisateur, paiement.enregistre_par_id) if paiement.enregistre_par_id else None
    pdf = pdf_recu(
        session.get(Ecole, user.ecole_id), paiement, eleve,
        session.get(Classe, eleve.classe_id), solde, caissier.nom if caissier else None,
    )
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"recu_{paiement.numero_recu}.pdf"))


@router.get("/impayes", response_model=list[Solde])
def liste_impayes(
    classe_id: int | None = None,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Élèves qui doivent encore de l'argent, du plus gros montant au plus petit."""
    stmt = select(Eleve).where(Eleve.ecole_id == user.ecole_id)
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    soldes = [calculer_solde(session, e) for e in session.exec(stmt).all()]
    return sorted(
        (s for s in soldes if s.reste_a_payer > 0),
        key=lambda s: s.reste_a_payer,
        reverse=True,
    )
