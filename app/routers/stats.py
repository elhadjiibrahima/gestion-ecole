from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel, func, select

from app.database import get_session
from app.models import Classe, Eleve, Paiement, Utilisateur
from app.security import GESTION, exiger_role
from app.services import annee_courante, calculer_solde, tranches_annee

router = APIRouter(tags=["Tableau de bord"])


class Stats(SQLModel):
    nb_classes: int
    nb_eleves: int
    total_attendu: int
    total_encaisse: int
    total_impaye: int
    nb_eleves_en_retard: int
    taux_recouvrement: float
    total_retard_echeances: int = 0          # montant des tranches échues non payées
    nb_retard_echeances: int = 0
    retard_scolarite: int = 0                # mensualités / tranches échues non payées
    nb_retard_scolarite: int = 0
    retard_autres: int = 0                   # inscription, tenue… pas encore payés
    nb_retard_autres: int = 0
    encaisse_jour: int = 0
    encaisse_mois: int = 0
    depenses_mois: int = 0
    garcons: int = 0
    filles: int = 0
    absences_jour: int = 0


@router.get("/stats", response_model=Stats)
def statistiques(
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    eleves = session.exec(select(Eleve).where(Eleve.ecole_id == user.ecole_id, Eleve.actif == True)).all()  # noqa: E712
    tranches = tranches_annee(session, user.ecole_id)
    soldes = [calculer_solde(session, e, tranches) for e in eleves]
    attendu = sum(s.frais_annuels for s in soldes)
    encaisse = session.exec(
        select(func.coalesce(func.sum(Paiement.montant), 0))
        .join(Eleve, Paiement.eleve_id == Eleve.id)
        .where(Eleve.ecole_id == user.ecole_id, Eleve.actif == True, Paiement.annule == False,  # noqa: E712
               Paiement.annee_scolaire == annee_courante(session, user.ecole_id))
    ).one()
    impaye = sum(s.reste_a_payer for s in soldes)
    from datetime import date
    from app.models import Absence, Depense, Recette
    auj = date.today()
    debut_mois = auj.replace(day=1)
    def encaisse_depuis(debut):
        p = session.exec(select(func.coalesce(func.sum(Paiement.montant), 0)).join(Eleve, Paiement.eleve_id == Eleve.id)
                         .where(Eleve.ecole_id == user.ecole_id, Paiement.annule == False,  # noqa: E712
                                Paiement.date_paiement >= debut, Paiement.date_paiement <= auj)).one()
        r = session.exec(select(func.coalesce(func.sum(Recette.montant), 0))
                         .where(Recette.ecole_id == user.ecole_id, Recette.jour >= debut, Recette.jour <= auj)).one()
        return p + r
    extra = dict(
        retard_scolarite=sum(s.retard_scolarite for s in soldes),
        nb_retard_scolarite=sum(1 for s in soldes if s.retard_scolarite > 0),
        retard_autres=sum(s.retard_autres for s in soldes),
        nb_retard_autres=sum(1 for s in soldes if s.retard_autres > 0),
        encaisse_jour=encaisse_depuis(auj), encaisse_mois=encaisse_depuis(debut_mois),
        depenses_mois=session.exec(select(func.coalesce(func.sum(Depense.montant), 0)).where(
            Depense.ecole_id == user.ecole_id, Depense.annule == False, Depense.jour >= debut_mois)).one(),  # noqa: E712
        garcons=sum(1 for e in eleves if e.sexe == "M"), filles=sum(1 for e in eleves if e.sexe == "F"),
        absences_jour=session.exec(select(func.count()).select_from(Absence).where(
            Absence.ecole_id == user.ecole_id, Absence.jour == auj, Absence.type == "absence")).one(),
    )
    return Stats(**extra,
        nb_classes=session.exec(
            select(func.count()).select_from(Classe).where(Classe.ecole_id == user.ecole_id)
        ).one(),
        nb_eleves=len(eleves),
        total_attendu=attendu,
        total_encaisse=encaisse,
        total_impaye=impaye,
        nb_eleves_en_retard=sum(1 for s in soldes if s.reste_a_payer > 0),
        taux_recouvrement=round(100 * (attendu - impaye) / attendu, 1) if attendu else 0.0,
        total_retard_echeances=sum(s.retard for s in soldes),
        nb_retard_echeances=sum(1 for s in soldes if s.retard > 0),
    )
