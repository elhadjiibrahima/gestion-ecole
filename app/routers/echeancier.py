from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import Role, Tranche, TrancheSaisie, Utilisateur
from app.security import TOUS, exiger_role
from app.services import annee_courante, tranches_annee

router = APIRouter(prefix="/tranches", tags=["Échéancier"])


@router.get("", response_model=list[Tranche])
def lister(user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    """Tranches de paiement de l'année en cours (vide : tout est dû dès l'inscription)."""
    return tranches_annee(session, user.ecole_id)


@router.put("", response_model=list[Tranche])
def definir(
    tranches: list[TrancheSaisie],
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Remplace l'échéancier de l'année. Les pourcentages doivent faire 100 %. Liste vide = pas d'échéancier."""
    if tranches:
        total = round(sum(t.pourcentage for t in tranches), 2)
        if total != 100:
            raise HTTPException(400, f"Les tranches font {total:g} % au lieu de 100 %")
        dates = [t.date_limite for t in tranches]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise HTTPException(400, "Les dates limites doivent être différentes et dans l'ordre")
    annee = annee_courante(session, user.ecole_id)
    for t in session.exec(select(Tranche).where(Tranche.ecole_id == user.ecole_id,
                                                Tranche.annee_scolaire == annee)).all():
        session.delete(t)
    for i, t in enumerate(tranches):
        session.add(Tranche(ecole_id=user.ecole_id, annee_scolaire=annee, ordre=i,
                            nom=t.nom.strip(), date_limite=t.date_limite, pourcentage=t.pourcentage))
    session.commit()
    return tranches_annee(session, user.ecole_id)
