"""Mots de passe, jetons JWT et contrôle des rôles."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.database import get_session
from app.models import Role, Utilisateur

# ⚠️ En production, définis la variable d'environnement SECRET_KEY
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-a-changer-en-production")
ALGORITHM = "HS256"
DUREE_TOKEN = timedelta(hours=int(os.getenv("TOKEN_HEURES", "12")))
PBKDF2_ITERATIONS = int(os.getenv("PBKDF2_ITERATIONS", "390000"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


# ---------- Mots de passe (PBKDF2-SHA256, bibliothèque standard) ----------
def hacher_mot_de_passe(mot_de_passe: str) -> str:
    sel = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), sel.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${sel}${h.hex()}"


def verifier_mot_de_passe(mot_de_passe: str, stocke: str) -> bool:
    try:
        _, iterations, sel, attendu = stocke.split("$")
        h = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), sel.encode(), int(iterations))
        return hmac.compare_digest(h.hex(), attendu)
    except ValueError:
        return False


# ---------- JWT ----------
def creer_token(utilisateur: Utilisateur) -> str:
    payload = {
        "sub": str(utilisateur.id),
        "ecole": utilisateur.ecole_id,
        "role": utilisateur.role.value,
        "exp": datetime.now(timezone.utc) + DUREE_TOKEN,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def utilisateur_courant(
    token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)
) -> Utilisateur:
    erreur = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Session expirée ou invalide, reconnecte-toi",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") == "parent":      # un jeton parent n'ouvre pas l'espace du personnel
            raise erreur
        user = session.get(Utilisateur, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise erreur
    if not user or not user.actif:
        raise erreur
    return user


def exiger_role(*roles: Role):
    """Dépendance FastAPI : refuse l'accès si le rôle n'est pas autorisé."""
    def verifier(user: Utilisateur = Depends(utilisateur_courant)) -> Utilisateur:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé pour ton rôle")
        return user
    return verifier


TOUS = (Role.directeur, Role.comptable, Role.enseignant)
GESTION = (Role.directeur, Role.comptable)
PEDAGOGIE = (Role.directeur, Role.enseignant)


# ---------- Espace parents ----------
ALPHABET_CODE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sans 0/O ni 1/I
DUREE_TOKEN_PARENT = timedelta(days=30)
oauth2_parent = OAuth2PasswordBearer(tokenUrl="/parents/connexion", auto_error=True)


def generer_code_parent() -> str:
    brut = "".join(secrets.choice(ALPHABET_CODE) for _ in range(8))
    return f"{brut[:4]}-{brut[4:]}"


def normaliser_code(code: str) -> str:
    brut = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{brut[:4]}-{brut[4:]}" if len(brut) == 8 else brut


def _empreinte_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()[:12]


def creer_token_parent(eleve) -> str:
    payload = {"sub": str(eleve.id), "type": "parent", "ecole": eleve.ecole_id,
               "cv": _empreinte_code(eleve.code_parent),   # change si l'école génère un nouveau code
               "exp": datetime.now(timezone.utc) + DUREE_TOKEN_PARENT}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def eleve_du_parent(token: str = Depends(oauth2_parent), session: Session = Depends(get_session)):
    from app.models import Eleve
    erreur = HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée, reconnectez-vous",
                           headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "parent":
            raise erreur
        eleve = session.get(Eleve, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise erreur
    if not eleve or not eleve.code_parent or payload.get("cv") != _empreinte_code(eleve.code_parent):
        raise erreur                             # code régénéré ou retiré : accès coupé
    return eleve
