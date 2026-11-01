"""Configuration : tout ce qui est secret ou propre à une installation vient de l'environnement.

Aucune clé n'est écrite dans le code. Les valeurs sont lues, dans l'ordre :
1. les variables d'environnement du serveur (hébergeur en ligne) ;
2. le fichier `.env` à la racine du projet (jamais envoyé sur GitHub, voir .gitignore).

Sur un poste de développement sans `.env`, une clé aléatoire est créée une fois et rangée
dans `.env` (pour que les sessions restent valides après un redémarrage).
En production (ENVIRONNEMENT=production), l'application refuse de démarrer sans SECRET_KEY.
"""
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
FICHIER_ENV = Path(os.getenv("FICHIER_ENV", RACINE / ".env"))
load_dotenv(FICHIER_ENV)

journal = logging.getLogger("gestion_ecole")


def _cle_secrete() -> str:
    cle = os.getenv("SECRET_KEY", "").strip()
    if cle:
        if len(cle) < 32:
            raise RuntimeError("SECRET_KEY est trop courte (32 caractères minimum).")
        return cle
    if os.getenv("ENVIRONNEMENT", "").lower() == "production":
        raise RuntimeError("SECRET_KEY manquante : définis-la dans les variables d'environnement du serveur.")
    cle = secrets.token_hex(32)
    with open(FICHIER_ENV, "a", encoding="utf-8") as f:
        f.write(f"\n# Créée automatiquement : ne la partage pas et ne l'envoie pas sur GitHub\nSECRET_KEY={cle}\n")
    os.environ["SECRET_KEY"] = cle
    journal.warning("Aucune SECRET_KEY : une clé aléatoire a été créée dans %s", FICHIER_ENV)
    return cle


SECRET_KEY = _cle_secrete()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ecole.db")
ESSAI_JOURS = int(os.getenv("ESSAI_JOURS", "30"))                 # période d'essai d'une nouvelle école
CONTACT_ABONNEMENT = os.getenv("CONTACT_ABONNEMENT", "")           # affiché quand l'abonnement expire
INSCRIPTION_PUBLIQUE = os.getenv("INSCRIPTION_PUBLIQUE", "oui").lower() in ("oui", "1", "true", "yes")
