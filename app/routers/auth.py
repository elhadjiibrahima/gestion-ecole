import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    Affectation, AffectationSaisie, ChangementMotDePasse, Classe, Matiere, ReinitialisationMotDePasse,
    Ecole, EcoleUpdate, InscriptionEcole, Moi, Role, Token, Utilisateur,
    UtilisateurCreate, UtilisateurPublic,
)
from app.routers.matieres import ajouter_matieres_defaut
from app.security import (
    affectations_de, creer_token, exiger_role, hacher_mot_de_passe, utilisateur_courant,
    verifier_mot_de_passe,
)

router = APIRouter(tags=["Comptes"])


def _email_libre(session: Session, email: str) -> None:
    if session.exec(select(Utilisateur).where(Utilisateur.email == email.lower())).first():
        raise HTTPException(400, "Cet e-mail est déjà utilisé")


@router.post("/auth/inscription", response_model=Token, status_code=201)
def inscrire_ecole(data: InscriptionEcole, session: Session = Depends(get_session)):
    """Crée une nouvelle école et le compte de son directeur."""
    _email_libre(session, data.email)
    ecole = Ecole(nom=data.nom_ecole, adresse=data.adresse, telephone=data.telephone, devise=data.devise)
    session.add(ecole)
    session.flush()
    directeur = Utilisateur(
        nom=data.nom_directeur, email=data.email.lower(), role=Role.directeur,
        ecole_id=ecole.id, mot_de_passe_hash=hacher_mot_de_passe(data.mot_de_passe),
    )
    session.add(directeur)
    ajouter_matieres_defaut(session, ecole.id)   # liste de départ, modifiable ensuite
    session.commit()
    session.refresh(directeur)
    return Token(access_token=creer_token(directeur))


# Protection contre les essais de mots de passe : 5 erreurs par e-mail et par quart d'heure
_ECHECS: dict[str, list[float]] = {}
MAX_ECHECS, FENETRE = 5, 15 * 60


@router.post("/auth/token", response_model=Token)
def connexion(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    """Connexion : `username` = e-mail, `password` = mot de passe."""
    email = form.username.strip().lower()
    maintenant = time.time()
    _ECHECS[email] = [t for t in _ECHECS.get(email, []) if maintenant - t < FENETRE]
    if len(_ECHECS[email]) >= MAX_ECHECS:
        attente = int((FENETRE - (maintenant - _ECHECS[email][0])) // 60) + 1
        raise HTTPException(429, f"Trop d'essais : compte bloqué, réessaie dans {attente} minute(s)")
    user = session.exec(select(Utilisateur).where(Utilisateur.email == email)).first()
    if not user or not user.actif or not verifier_mot_de_passe(form.password, user.mot_de_passe_hash):
        _ECHECS[email].append(maintenant)
        restant = MAX_ECHECS - len(_ECHECS[email])
        raise HTTPException(401, "E-mail ou mot de passe incorrect"
                            + (f" ({restant} essai(s) restant(s))" if restant <= 2 else ""))
    _ECHECS.pop(email, None)
    return Token(access_token=creer_token(user))


@router.post("/auth/mot-de-passe", status_code=204)
def changer_mot_de_passe(
    data: ChangementMotDePasse,
    user: Utilisateur = Depends(utilisateur_courant),
    session: Session = Depends(get_session),
):
    """Chacun peut changer son propre mot de passe (il faut connaître l'ancien)."""
    if not verifier_mot_de_passe(data.ancien, user.mot_de_passe_hash):
        raise HTTPException(400, "Ancien mot de passe incorrect")
    if data.ancien == data.nouveau:
        raise HTTPException(400, "Le nouveau mot de passe doit être différent de l'ancien")
    user.mot_de_passe_hash = hacher_mot_de_passe(data.nouveau)
    session.add(user)
    session.commit()


@router.get("/auth/moi", response_model=Moi)
def moi(user: Utilisateur = Depends(utilisateur_courant), session: Session = Depends(get_session)):
    return Moi(utilisateur=public(session, user), ecole=session.get(Ecole, user.ecole_id))


def public(session: Session, u: Utilisateur) -> UtilisateurPublic:
    return UtilisateurPublic(
        id=u.id, nom=u.nom, email=u.email, role=u.role, ecole_id=u.ecole_id, actif=u.actif,
        affectations=[AffectationSaisie(matiere_id=m, classe_id=c) for m, c in sorted(affectations_de(session, u))],
    )


def _enregistrer_affectations(session: Session, cible: Utilisateur, affectations: list[AffectationSaisie]) -> None:
    """Remplace les matières × classes d'un enseignant (vérifiées dans l'école)."""
    if affectations and cible.role != Role.enseignant:
        raise HTTPException(400, "Seul un enseignant reçoit des matières et des classes")
    matieres = {m.id for m in session.exec(select(Matiere).where(Matiere.ecole_id == cible.ecole_id))}
    classes = {c.id for c in session.exec(select(Classe).where(Classe.ecole_id == cible.ecole_id))}
    for a in affectations:
        if a.matiere_id not in matieres or a.classe_id not in classes:
            raise HTTPException(400, "Matière ou classe introuvable")
    for a in session.exec(select(Affectation).where(Affectation.utilisateur_id == cible.id)).all():
        session.delete(a)
    for m, c in {(a.matiere_id, a.classe_id) for a in affectations}:
        session.add(Affectation(utilisateur_id=cible.id, matiere_id=m, classe_id=c))


# ---------- Paramètres de l'école ----------
@router.patch("/ecole", response_model=Ecole)
def modifier_ecole(
    data: EcoleUpdate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    ecole = session.get(Ecole, user.ecole_id)
    ecole.sqlmodel_update(data.model_dump(exclude_unset=True))
    session.add(ecole)
    session.commit()
    session.refresh(ecole)
    return ecole


# ---------- Gestion des comptes du personnel ----------
@router.get("/utilisateurs", response_model=list[UtilisateurPublic])
def lister_utilisateurs(
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    return [public(session, u) for u in session.exec(
        select(Utilisateur).where(Utilisateur.ecole_id == user.ecole_id).order_by(Utilisateur.nom)).all()]


@router.post("/utilisateurs", response_model=UtilisateurPublic, status_code=201)
def creer_utilisateur(
    data: UtilisateurCreate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    _email_libre(session, data.email)
    nouveau = Utilisateur(
        nom=data.nom, email=data.email.lower(), role=data.role, ecole_id=user.ecole_id,
        mot_de_passe_hash=hacher_mot_de_passe(data.mot_de_passe),
    )
    session.add(nouveau)
    session.flush()
    _enregistrer_affectations(session, nouveau, data.affectations)
    session.commit()
    session.refresh(nouveau)
    return public(session, nouveau)


@router.put("/utilisateurs/{utilisateur_id}/affectations", response_model=UtilisateurPublic)
def modifier_affectations(
    utilisateur_id: int,
    affectations: list[AffectationSaisie],
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Matières et classes d'un enseignant (remplace la liste)."""
    cible = session.get(Utilisateur, utilisateur_id)
    if not cible or cible.ecole_id != user.ecole_id:
        raise HTTPException(404, "Utilisateur introuvable")
    _enregistrer_affectations(session, cible, affectations)
    session.commit()
    return public(session, cible)


@router.post("/utilisateurs/{utilisateur_id}/mot-de-passe", status_code=204)
def reinitialiser_mot_de_passe(
    utilisateur_id: int,
    data: ReinitialisationMotDePasse,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Le directeur donne un nouveau mot de passe à un membre de l'équipe (mot de passe oublié)."""
    cible = session.get(Utilisateur, utilisateur_id)
    if not cible or cible.ecole_id != user.ecole_id:
        raise HTTPException(404, "Utilisateur introuvable")
    cible.mot_de_passe_hash = hacher_mot_de_passe(data.nouveau)
    session.add(cible)
    session.commit()


@router.patch("/utilisateurs/{utilisateur_id}/actif", response_model=UtilisateurPublic)
def activer_desactiver(
    utilisateur_id: int,
    actif: bool,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    cible = session.get(Utilisateur, utilisateur_id)
    if not cible or cible.ecole_id != user.ecole_id:
        raise HTTPException(404, "Utilisateur introuvable")
    if cible.id == user.id:
        raise HTTPException(400, "Tu ne peux pas désactiver ton propre compte")
    cible.actif = actif
    session.add(cible)
    session.commit()
    session.refresh(cible)
    return public(session, cible)
