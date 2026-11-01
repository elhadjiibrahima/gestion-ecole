from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    ChangementMotDePasse, ReinitialisationMotDePasse,
    Ecole, EcoleUpdate, InscriptionEcole, Moi, Role, Token, Utilisateur,
    UtilisateurCreate, UtilisateurPublic,
)
from app.routers.matieres import ajouter_matieres_defaut
from app.security import (
    creer_token, exiger_role, hacher_mot_de_passe, utilisateur_courant,
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


@router.post("/auth/token", response_model=Token)
def connexion(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    """Connexion : `username` = e-mail, `password` = mot de passe."""
    user = session.exec(select(Utilisateur).where(Utilisateur.email == form.username.lower())).first()
    if not user or not user.actif or not verifier_mot_de_passe(form.password, user.mot_de_passe_hash):
        raise HTTPException(401, "E-mail ou mot de passe incorrect")
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
    return Moi(utilisateur=UtilisateurPublic.model_validate(user), ecole=session.get(Ecole, user.ecole_id))


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
    return session.exec(
        select(Utilisateur).where(Utilisateur.ecole_id == user.ecole_id).order_by(Utilisateur.nom)
    ).all()


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
    session.commit()
    session.refresh(nouveau)
    return nouveau


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
    return cible
