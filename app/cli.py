"""Commandes à lancer sur le serveur.

    python -m app.cli creer-superadmin      crée (ou remet à zéro) un compte super admin
    python -m app.cli generer-cle           affiche une nouvelle SECRET_KEY à mettre dans .env

Le mot de passe est demandé au clavier (il ne s'affiche pas et n'est jamais écrit dans un fichier).
"""
import getpass
import secrets
import sys

from sqlmodel import Session, select


def creer_superadmin() -> None:
    from app.database import engine, init_db
    from app.models import SuperAdmin
    from app.security import hacher_mot_de_passe

    init_db()
    email = input("E-mail du super admin : ").strip().lower()
    if "@" not in email:
        sys.exit("E-mail invalide.")
    nom = input("Nom : ").strip() or "Super admin"
    mdp = getpass.getpass("Mot de passe (12 caractères minimum) : ")
    if len(mdp) < 12:
        sys.exit("Mot de passe trop court (12 caractères minimum).")
    if getpass.getpass("Confirme le mot de passe : ") != mdp:
        sys.exit("Les deux mots de passe sont différents.")
    with Session(engine) as session:
        admin = session.exec(select(SuperAdmin).where(SuperAdmin.email == email)).first()
        if admin:
            admin.mot_de_passe_hash, admin.nom = hacher_mot_de_passe(mdp), nom
            message = "Mot de passe du super admin mis à jour."
        else:
            admin = SuperAdmin(email=email, nom=nom, mot_de_passe_hash=hacher_mot_de_passe(mdp))
            message = "Super admin créé."
        session.add(admin)
        session.commit()
    print(f"{message} Connecte-toi sur /admin avec {email}.")


def generer_cle() -> None:
    print(secrets.token_hex(32))


COMMANDES = {"creer-superadmin": creer_superadmin, "generer-cle": generer_cle}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDES:
        sys.exit(__doc__)
    COMMANDES[sys.argv[1]]()
