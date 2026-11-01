from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import Classe, ClasseCreate, Ecole, Eleve, Role, Utilisateur
from app.pdf import en_tete_pdf, pdf_bulletins, pdf_etat_paiements
from app.security import GESTION, TOUS, exiger_role
from app.models import ResultatAnnuel
from app.services import bulletins_classe, get_classe, resultats_annuels, soldes_ecole

router = APIRouter(prefix="/classes", tags=["Classes"])


@router.post("", response_model=Classe, status_code=201)
def creer_classe(
    data: ClasseCreate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    classe = Classe.model_validate(data, update={"ecole_id": user.ecole_id})
    session.add(classe)
    session.commit()
    session.refresh(classe)
    return classe


@router.get("", response_model=list[Classe])
def lister_classes(
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    return session.exec(
        select(Classe).where(Classe.ecole_id == user.ecole_id).order_by(Classe.nom)
    ).all()


@router.patch("/{classe_id}", response_model=Classe)
def modifier_classe(
    classe_id: int,
    data: ClasseCreate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    classe = get_classe(session, classe_id, user.ecole_id)
    classe.sqlmodel_update(data.model_dump())
    session.add(classe)
    session.commit()
    session.refresh(classe)
    return classe


@router.get("/{classe_id}/eleves", response_model=list[Eleve])
def eleves_de_la_classe(
    classe_id: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    get_classe(session, classe_id, user.ecole_id)
    return session.exec(
        select(Eleve).where(Eleve.classe_id == classe_id, Eleve.actif == True).order_by(Eleve.nom)  # noqa: E712
    ).all()


@router.get("/{classe_id}/bulletins.pdf", response_class=Response)
def bulletins_pdf(
    classe_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Tous les bulletins de la classe dans un seul PDF (une page par élève)."""
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    classe = get_classe(session, classe_id, user.ecole_id)
    bulletins = bulletins_classe(session, classe, trimestre)
    if not bulletins:
        raise HTTPException(400, "Aucun élève dans cette classe")
    pdf = pdf_bulletins(session.get(Ecole, user.ecole_id), bulletins)
    return Response(pdf, media_type="application/pdf",
                    headers=en_tete_pdf(f"bulletins_{classe.nom}_T{trimestre}.pdf"))


@router.get("/{classe_id}/paiements.pdf", response_class=Response)
def etat_paiements_pdf(
    classe_id: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """État des paiements de la classe : chaque élève, payé, reste, statut, totaux."""
    classe = get_classe(session, classe_id, user.ecole_id)
    soldes = soldes_ecole(session, user.ecole_id, classe_id)
    pdf = pdf_etat_paiements(session.get(Ecole, user.ecole_id), classe, soldes)
    return Response(pdf, media_type="application/pdf",
                    headers=en_tete_pdf(f"paiements_{classe.nom}.pdf"))


@router.get("/{classe_id}/resultats-annuels", response_model=list[ResultatAnnuel])
def resultats_de_l_annee(
    classe_id: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Moyennes des 3 trimestres, moyenne annuelle, rang et décision (admis / redouble)."""
    classe = get_classe(session, classe_id, user.ecole_id)
    return sorted(resultats_annuels(session, classe), key=lambda r: (r.rang is None, r.rang or 0, r.eleve))
