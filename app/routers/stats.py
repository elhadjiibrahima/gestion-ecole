from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel, func, select

from app.database import get_session
from app.models import Classe, Eleve, Paiement, Utilisateur
from app.security import GESTION, exiger_role
from app.services import annee_courante, calculer_solde

router = APIRouter(tags=["Tableau de bord"])


class Stats(SQLModel):
    nb_classes: int
    nb_eleves: int
    total_attendu: int
    total_encaisse: int
    total_impaye: int
    nb_eleves_en_retard: int
    taux_recouvrement: float


@router.get("/stats", response_model=Stats)
def statistiques(
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    eleves = session.exec(select(Eleve).where(Eleve.ecole_id == user.ecole_id, Eleve.actif == True)).all()  # noqa: E712
    soldes = [calculer_solde(session, e) for e in eleves]
    attendu = sum(s.frais_annuels for s in soldes)
    encaisse = session.exec(
        select(func.coalesce(func.sum(Paiement.montant), 0))
        .join(Eleve, Paiement.eleve_id == Eleve.id)
        .where(Eleve.ecole_id == user.ecole_id, Eleve.actif == True, Paiement.annule == False,  # noqa: E712
               Paiement.annee_scolaire == annee_courante(session, user.ecole_id))
    ).one()
    impaye = sum(s.reste_a_payer for s in soldes)
    return Stats(
        nb_classes=session.exec(
            select(func.count()).select_from(Classe).where(Classe.ecole_id == user.ecole_id)
        ).one(),
        nb_eleves=len(eleves),
        total_attendu=attendu,
        total_encaisse=encaisse,
        total_impaye=impaye,
        nb_eleves_en_retard=sum(1 for s in soldes if s.reste_a_payer > 0),
        taux_recouvrement=round(100 * (attendu - impaye) / attendu, 1) if attendu else 0.0,
    )
