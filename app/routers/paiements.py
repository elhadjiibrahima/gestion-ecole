from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    AnnulationPaiement, Classe, Ecole, Eleve, Paiement, PaiementCreate, PaiementDetail, Role, Solde,
    Utilisateur,
)
from app.pdf import en_tete_pdf, pdf_recu
from app.security import GESTION, exiger_role
from app.services import calculer_solde, creer_paiement, get_eleve, soldes_ecole

router = APIRouter(tags=["Paiements"])


def nom_frais(session: Session, type_frais_id: int | None) -> str:
    if type_frais_id is None:
        return "Scolarité"
    from app.models import TypeFrais
    t = session.get(TypeFrais, type_frais_id)
    return t.nom if t else "?"


def details_paiements(session: Session, paiements: list[Paiement]) -> list[PaiementDetail]:
    ids = {p.enregistre_par_id for p in paiements} | {p.annule_par_id for p in paiements}
    noms = {u.id: u.nom for u in session.exec(select(Utilisateur).where(Utilisateur.id.in_(ids - {None})))} if ids else {}
    return [
        PaiementDetail(**p.model_dump(include=set(PaiementDetail.model_fields) - {"enregistre_par", "annule_par", "frais"}),
                       frais=nom_frais(session, p.type_frais_id),
                       enregistre_par=noms.get(p.enregistre_par_id), annule_par=noms.get(p.annule_par_id))
        for p in paiements
    ]


def _get_paiement(session: Session, paiement_id: int, ecole_id: int) -> tuple[Paiement, Eleve]:
    paiement = session.get(Paiement, paiement_id)
    eleve = session.get(Eleve, paiement.eleve_id) if paiement else None
    if not eleve or eleve.ecole_id != ecole_id:
        raise HTTPException(404, "Paiement introuvable")
    return paiement, eleve


@router.post("/paiements", response_model=Paiement, status_code=201)
def enregistrer_paiement(
    data: PaiementCreate,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    eleve = get_eleve(session, data.eleve_id, user.ecole_id, code=400)
    if data.cle_client:   # déjà reçu (renvoi après une coupure réseau) : on ne l'enregistre pas deux fois
        deja = session.exec(select(Paiement).where(Paiement.cle_client == data.cle_client,
                                                   Paiement.eleve_id == eleve.id)).first()
        if deja:
            return deja
    if not eleve.actif:
        raise HTTPException(400, "Cet élève ne fait plus partie de l'école")
    paiement = creer_paiement(session, eleve, data.montant, data.mode, data.reference,
                              data.date_paiement, user.id, data.type_frais_id)
    paiement.cle_client = data.cle_client
    session.commit()
    session.refresh(paiement)
    return paiement


@router.post("/paiements/{paiement_id}/annuler", response_model=PaiementDetail)
def annuler_paiement(
    paiement_id: int,
    data: AnnulationPaiement,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Annule un paiement saisi par erreur. Il reste visible (barré) avec le motif et l'auteur."""
    paiement, _ = _get_paiement(session, paiement_id, user.ecole_id)
    if paiement.annule:
        raise HTTPException(400, "Ce paiement est déjà annulé")
    paiement.annule = True
    paiement.annule_le = datetime.now(timezone.utc)
    paiement.annule_par_id = user.id
    paiement.motif_annulation = data.motif.strip()
    session.add(paiement)
    session.commit()
    session.refresh(paiement)
    return details_paiements(session, [paiement])[0]


@router.get("/eleves/{eleve_id}/paiements", response_model=list[PaiementDetail])
def historique_paiements(
    eleve_id: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Tous les paiements de l'élève (toutes années, annulés compris), du plus ancien au plus récent."""
    get_eleve(session, eleve_id, user.ecole_id)
    paiements = session.exec(
        select(Paiement).where(Paiement.eleve_id == eleve_id).order_by(Paiement.date_paiement, Paiement.id)
    ).all()
    return details_paiements(session, paiements)


@router.get("/paiements/{paiement_id}/recu.pdf", response_class=Response)
def recu_pdf(
    paiement_id: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    paiement, eleve = _get_paiement(session, paiement_id, user.ecole_id)
    solde = calculer_solde(session, eleve)
    caissier = session.get(Utilisateur, paiement.enregistre_par_id) if paiement.enregistre_par_id else None
    pdf = pdf_recu(
        session.get(Ecole, user.ecole_id), paiement, eleve,
        session.get(Classe, eleve.classe_id), solde, caissier.nom if caissier else None,
        nom_frais(session, paiement.type_frais_id),
    )
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"recu_{paiement.numero_recu}.pdf"))


@router.get("/impayes", response_model=list[Solde])
def liste_impayes(
    classe_id: int | None = None,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Élèves qui doivent encore de l'argent, du plus gros montant au plus petit."""
    return sorted(
        (s for s in soldes_ecole(session, user.ecole_id, classe_id) if s.reste_a_payer > 0),
        key=lambda s: s.reste_a_payer,
        reverse=True,
    )
