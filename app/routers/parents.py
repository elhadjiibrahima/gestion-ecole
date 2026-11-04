"""Espace parents : chaque famille consulte la situation de son enfant avec le matricule + un code."""
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    Absence, AbsenceParent, Classe, CodeParent, ConnexionParent, Ecole, Eleve, EspaceParent, Paiement,
    PaiementParent, PhotoEleve, Token, Utilisateur,
)
from app.cursus import regles_classe
from app.routers.scolarite import travail_a_faire
from app.pdf import en_tete_pdf, pdf_bulletins, pdf_recu
from app.security import (
    GESTION, creer_token_parent, eleve_du_parent, exiger_role, generer_code_parent, normaliser_code,
)
from app.routers.paiements import nom_frais
from app.services import bulletin_eleve, calculer_solde, get_classe, get_eleve

router = APIRouter(tags=["Espace parents"])

# Protection contre les essais au hasard : 5 erreurs par matricule et par quart d'heure
_ECHECS: dict[str, list[float]] = {}
MAX_ECHECS, FENETRE = 5, 15 * 60


def _bloque(matricule: str) -> bool:
    maintenant = time.time()
    _ECHECS[matricule] = [t for t in _ECHECS.get(matricule, []) if maintenant - t < FENETRE]
    return len(_ECHECS[matricule]) >= MAX_ECHECS


@router.post("/parents/connexion", response_model=Token)
def connexion_parent(data: ConnexionParent, session: Session = Depends(get_session)):
    matricule = data.matricule.strip().upper()
    if _bloque(matricule):
        raise HTTPException(429, "Trop d'essais. Réessayez dans 15 minutes.")
    eleve = session.exec(select(Eleve).where(Eleve.matricule == matricule)).first()
    if not eleve or not eleve.code_parent or eleve.code_parent != normaliser_code(data.code):
        _ECHECS.setdefault(matricule, []).append(time.time())
        raise HTTPException(401, "Matricule ou code incorrect")
    _ECHECS.pop(matricule, None)
    return Token(access_token=creer_token_parent(eleve))


@router.get("/parents/moi", response_model=EspaceParent)
def espace(eleve: Eleve = Depends(eleve_du_parent), session: Session = Depends(get_session)):
    ecole = session.get(Ecole, eleve.ecole_id)
    classe = session.get(Classe, eleve.classe_id)
    regles = regles_classe(session, classe)
    solde = calculer_solde(session, eleve)
    retenus = ecole.bloquer_bulletin_impaye and solde.retard_scolarite > 0
    annee = ecole.annee_scolaire
    paiements = session.exec(select(Paiement).where(Paiement.eleve_id == eleve.id, Paiement.annee_scolaire == annee)
                             .order_by(Paiement.date_paiement.desc(), Paiement.id.desc())).all()
    absences = session.exec(select(Absence).where(Absence.eleve_id == eleve.id, Absence.annee_scolaire == annee)
                            .order_by(Absence.jour.desc())).all()
    return EspaceParent(
        eleve_id=eleve.id, nom=eleve.nom, prenom=eleve.prenom, matricule=eleve.matricule, classe=classe.nom,
        ecole=ecole.nom, ecole_telephone=ecole.telephone, devise=ecole.devise, annee_scolaire=annee,
        a_photo=session.get(PhotoEleve, eleve.id) is not None,
        solde=solde,
        paiements=[PaiementParent(id=p.id, numero_recu=p.numero_recu, date_paiement=p.date_paiement,
                                  montant=p.montant, mode=p.mode, annule=p.annule,
                                  frais=nom_frais(session, p.type_frais_id)) for p in paiements],
        bulletins=[] if retenus else [b for b in (bulletin_eleve(session, eleve, t) for t in range(1, regles.periodes + 1)) if b.publie],
        bulletins_retenus=retenus,
        absences=[AbsenceParent(jour=a.jour, type=a.type, justifiee=a.justifiee, motif=a.motif) for a in absences],
        periode=regles.nom_periode, nb_periodes=regles.periodes, bareme=regles.bareme,
        cahier=travail_a_faire(session, classe.id, annee),
    )


@router.get("/parents/photo", response_class=Response)
def photo(eleve: Eleve = Depends(eleve_du_parent), session: Session = Depends(get_session)):
    p = session.get(PhotoEleve, eleve.id)
    if not p:
        raise HTTPException(404, "Pas de photo")
    return Response(p.image, media_type="image/jpeg")


@router.get("/parents/recu/{paiement_id}.pdf", response_class=Response)
def recu(paiement_id: int, eleve: Eleve = Depends(eleve_du_parent), session: Session = Depends(get_session)):
    p = session.get(Paiement, paiement_id)
    if not p or p.eleve_id != eleve.id:
        raise HTTPException(404, "Reçu introuvable")
    caissier = session.get(Utilisateur, p.enregistre_par_id) if p.enregistre_par_id else None
    pdf = pdf_recu(session.get(Ecole, eleve.ecole_id), p, eleve, session.get(Classe, eleve.classe_id),
                   calculer_solde(session, eleve), caissier.nom if caissier else None,
                   nom_frais(session, p.type_frais_id))
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"recu_{p.numero_recu}.pdf"))


@router.get("/parents/bulletin.pdf", response_class=Response)
def bulletin(trimestre: int, eleve: Eleve = Depends(eleve_du_parent), session: Session = Depends(get_session)):
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    b = bulletin_eleve(session, eleve, trimestre)
    ecole = session.get(Ecole, eleve.ecole_id)
    if ecole.bloquer_bulletin_impaye and calculer_solde(session, eleve).retard_scolarite > 0:
        raise HTTPException(403, "Le bulletin est disponible à l'école une fois la scolarité à jour")
    if not b.publie:
        raise HTTPException(404, "Les résultats de cette période ne sont pas encore publiés par l'école")
    pdf = pdf_bulletins(session.get(Ecole, eleve.ecole_id), [b])
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"bulletin_T{trimestre}.pdf"))


# ---------- Côté école : distribuer les codes ----------
def code_de(session: Session, eleve: Eleve, nouveau: bool = False) -> str:
    if nouveau or not eleve.code_parent:
        eleve.code_parent = generer_code_parent()
        session.add(eleve)
        session.commit()
        session.refresh(eleve)
    return eleve.code_parent


@router.get("/eleves/{eleve_id}/code-parent", response_model=CodeParent)
def voir_code(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
              session: Session = Depends(get_session)):
    e = get_eleve(session, eleve_id, user.ecole_id)
    return CodeParent(eleve_id=e.id, matricule=e.matricule, code=code_de(session, e))


@router.post("/eleves/{eleve_id}/code-parent", response_model=CodeParent)
def nouveau_code(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                 session: Session = Depends(get_session)):
    """Nouveau code (code perdu ou divulgué) : l'ancien ne marche plus."""
    e = get_eleve(session, eleve_id, user.ecole_id)
    return CodeParent(eleve_id=e.id, matricule=e.matricule, code=code_de(session, e, nouveau=True))


@router.get("/classes/{classe_id}/codes-parents.pdf", response_class=Response)
def codes_pdf(classe_id: int, request: Request, user: Utilisateur = Depends(exiger_role(*GESTION)),
              session: Session = Depends(get_session)):
    """Coupons à découper : un par élève, avec l'adresse de l'espace parents, le matricule et le code."""
    from app.pdf import pdf_codes_parents
    classe = get_classe(session, classe_id, user.ecole_id)
    eleves = session.exec(select(Eleve).where(Eleve.classe_id == classe_id, Eleve.actif == True)  # noqa: E712
                          .order_by(Eleve.nom, Eleve.prenom)).all()
    if not eleves:
        raise HTTPException(400, "Aucun élève dans cette classe")
    lignes = [(e, classe.nom, code_de(session, e)) for e in eleves]
    adresse = str(request.base_url).rstrip("/") + "/espace-parents"
    return Response(pdf_codes_parents(session.get(Ecole, user.ecole_id), lignes, adresse),
                    media_type="application/pdf", headers=en_tete_pdf(f"codes_parents_{classe.nom}.pdf"))
