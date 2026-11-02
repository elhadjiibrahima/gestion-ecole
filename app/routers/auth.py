import secrets
import time
from datetime import date, datetime, timedelta, timezone

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    Affectation, AffectationSaisie, ChangementMotDePasse, Classe, CodeReinitialisation, DemandeCode, ImageEcole,
    Matiere, ProfilSaisie, Reinitialisation, ReinitialisationMotDePasse,
    Ecole, EcoleUpdate, InscriptionEcole, Moi, Role, Token, Utilisateur,
    UtilisateurCreate, UtilisateurPublic,
)
from app.config import ESSAI_JOURS, INSCRIPTION_PUBLIQUE
from app.notifications import email_disponible, envoyer_email, envoyer_sms, sms_disponible
from app.routers.messages import numero_international
from app.routers.matieres import ajouter_matieres_defaut
from app.services import etat_abonnement
from app.security import (
    TOUS, affectations_de, creer_token, exiger_role, hacher_mot_de_passe, utilisateur_courant,
    verifier_mot_de_passe,
)

router = APIRouter(tags=["Comptes"])


def _email_libre(session: Session, email: str) -> None:
    if session.exec(select(Utilisateur).where(Utilisateur.email == email.lower())).first():
        raise HTTPException(400, "Cet e-mail est déjà utilisé")


@router.post("/auth/inscription", response_model=Token, status_code=201)
def inscrire_ecole(data: InscriptionEcole, session: Session = Depends(get_session)):
    """Crée une nouvelle école (en période d'essai) et le compte de son directeur."""
    if not INSCRIPTION_PUBLIQUE:
        raise HTTPException(403, "Les inscriptions sont fermées : contacte l'éditeur du logiciel pour ouvrir un compte")
    return creer_ecole_et_directeur(session, data)


def creer_ecole_et_directeur(session: Session, data: InscriptionEcole, statut: str = "essai",
                             fin: date | None = None) -> Token:
    _email_libre(session, data.email)
    ecole = Ecole(nom=data.nom_ecole, adresse=data.adresse, telephone=data.telephone, devise=data.devise,
                  abonnement_statut=statut, abonnement_fin=fin or date.today() + timedelta(days=ESSAI_JOURS))
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


@router.patch("/auth/moi", response_model=Moi)
def modifier_profil(data: ProfilSaisie, user: Utilisateur = Depends(utilisateur_courant),
                    session: Session = Depends(get_session)):
    """Chacun renseigne son téléphone (pour recevoir un code s'il oublie son mot de passe)."""
    user.telephone = (data.telephone or "").strip() or None
    session.add(user)
    session.commit()
    return moi(user, session)


# ---------- Mot de passe oublié ----------
_DEMANDES: dict[str, list[float]] = {}


@router.get("/auth/recuperation")
def recuperation_possible():
    """Quels moyens de récupération sont en place sur ce serveur."""
    return {"sms": sms_disponible(), "email": email_disponible()}


@router.post("/auth/mot-de-passe-oublie", status_code=202)
def demander_code(data: DemandeCode, session: Session = Depends(get_session)):
    """Envoie un code à 6 chiffres par SMS (si le téléphone est connu) ou par e-mail.
    Répond toujours pareil, que l'adresse existe ou non (on ne révèle pas qui a un compte)."""
    email = data.email.strip().lower()
    maintenant = time.time()
    _DEMANDES[email] = [t for t in _DEMANDES.get(email, []) if maintenant - t < 3600]
    reponse = {"message": "Si ce compte existe, un code vient d'être envoyé (valable 15 minutes)."}
    if len(_DEMANDES[email]) >= 3:
        raise HTTPException(429, "Trop de demandes : réessaie dans une heure")
    _DEMANDES[email].append(maintenant)
    user = session.exec(select(Utilisateur).where(Utilisateur.email == email)).first()
    if not user or not user.actif:
        return reponse
    code = f"{secrets.randbelow(1_000_000):06d}"
    for ancien in session.exec(select(CodeReinitialisation).where(CodeReinitialisation.utilisateur_id == user.id,
                                                                  CodeReinitialisation.utilise == False)).all():  # noqa: E712
        ancien.utilise = True
        session.add(ancien)
    session.add(CodeReinitialisation(utilisateur_id=user.id, code_hash=hacher_mot_de_passe(code),
                                     expire_le=datetime.now(timezone.utc) + timedelta(minutes=15)))
    session.commit()
    texte = f"Gestion École : ton code pour changer de mot de passe est {code} (valable 15 minutes). Ne le donne à personne."
    envoye = False
    if user.telephone and sms_disponible():
        ecole = session.get(Ecole, user.ecole_id)
        numero = numero_international(user.telephone, ecole.indicatif_tel)
        envoye = bool(numero) and envoyer_sms(numero, texte)[0]
    if not envoye and email_disponible():
        envoyer_email(user.email, "Code de récupération du mot de passe", texte)
    return reponse


@router.post("/auth/reinitialiser", status_code=204)
def reinitialiser_avec_code(data: Reinitialisation, session: Session = Depends(get_session)):
    erreur = HTTPException(400, "Code incorrect ou expiré : demande un nouveau code")
    user = session.exec(select(Utilisateur).where(Utilisateur.email == data.email.strip().lower())).first()
    if not user:
        raise erreur
    c = session.exec(select(CodeReinitialisation).where(CodeReinitialisation.utilisateur_id == user.id,
                                                        CodeReinitialisation.utilise == False)  # noqa: E712
                     .order_by(CodeReinitialisation.id.desc())).first()
    expire = c.expire_le.replace(tzinfo=timezone.utc) if c and c.expire_le.tzinfo is None else (c.expire_le if c else None)
    if not c or c.essais >= 5 or expire < datetime.now(timezone.utc):
        raise erreur
    if not verifier_mot_de_passe(data.code, c.code_hash):
        c.essais += 1
        session.add(c)
        session.commit()
        raise erreur
    c.utilise = True
    user.mot_de_passe_hash = hacher_mot_de_passe(data.nouveau)
    session.add(c)
    session.add(user)
    session.commit()
    _ECHECS.pop(user.email, None)     # débloque le compte s'il l'était


@router.get("/auth/moi", response_model=Moi)
def moi(user: Utilisateur = Depends(utilisateur_courant), session: Session = Depends(get_session)):
    ecole = session.get(Ecole, user.ecole_id)
    return Moi(utilisateur=public(session, user), ecole=ecole, abonnement=etat_abonnement(ecole))


def public(session: Session, u: Utilisateur) -> UtilisateurPublic:
    return UtilisateurPublic(
        id=u.id, nom=u.nom, email=u.email, role=u.role, ecole_id=u.ecole_id, actif=u.actif, telephone=u.telephone,
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
        telephone=(data.telephone or "").strip() or None,
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


# ---------- Logo et cachet ----------
def _preparer_image(contenu: bytes, taille_max: int) -> bytes:
    import io
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(contenu))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "Ce fichier n'est pas une image valide (PNG ou JPEG)")
    img = ImageOps.exif_transpose(img).convert("RGBA")   # garde la transparence (cachet)
    img.thumbnail((taille_max, taille_max))
    sortie = io.BytesIO()
    img.save(sortie, "PNG", optimize=True)
    return sortie.getvalue()


@router.put("/ecole/{type_image}", status_code=204)
async def envoyer_image(
    type_image: Literal["logo", "cachet"],
    fichier: UploadFile = File(...),
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Logo (en-tête des documents) ou cachet / signature (bas des reçus et bulletins). PNG transparent conseillé."""
    contenu = await fichier.read()
    if len(contenu) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image trop lourde (5 Mo maximum)")
    png = _preparer_image(contenu, 500)
    img = session.exec(select(ImageEcole).where(ImageEcole.ecole_id == user.ecole_id,
                                                ImageEcole.type == type_image)).first()
    if img:
        img.image = png
    else:
        img = ImageEcole(ecole_id=user.ecole_id, type=type_image, image=png)
    session.add(img)
    session.commit()


@router.get("/ecole/{type_image}", response_class=Response)
def voir_image(type_image: Literal["logo", "cachet"], user: Utilisateur = Depends(exiger_role(*TOUS)),
               session: Session = Depends(get_session)):
    img = session.exec(select(ImageEcole).where(ImageEcole.ecole_id == user.ecole_id,
                                                ImageEcole.type == type_image)).first()
    if not img:
        raise HTTPException(404, "Aucune image")
    return Response(img.image, media_type="image/png")


@router.delete("/ecole/{type_image}", status_code=204)
def supprimer_image(type_image: Literal["logo", "cachet"], user: Utilisateur = Depends(exiger_role(Role.directeur)),
                    session: Session = Depends(get_session)):
    img = session.exec(select(ImageEcole).where(ImageEcole.ecole_id == user.ecole_id,
                                                ImageEcole.type == type_image)).first()
    if img:
        session.delete(img)
        session.commit()
