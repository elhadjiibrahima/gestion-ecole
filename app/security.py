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
