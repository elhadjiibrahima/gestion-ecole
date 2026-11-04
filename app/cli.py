"""Commandes à lancer sur le serveur.

    python -m app.cli creer-superadmin      crée (ou remet à zéro) un compte super admin
    python -m app.cli generer-cle           affiche une nouvelle SECRET_KEY à mettre dans .env
    python -m app.cli remplir-test          crée une école de test complète (demande seulement le compte directeur)

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


def remplir_test() -> None:
    """École de test : toutes les classes, 3 élèves par classe, un prof par matière, le personnel, des notes."""
    from app.database import engine, init_db
    from app.donnees_test import remplir
    from app.models import Utilisateur

    init_db()
    print("=== Création d'une école de TEST (données fictives) ===\n")
    nom_ecole = input("Nom de l'école [Complexe Scolaire Test] : ").strip() or "Complexe Scolaire Test"
    pays = (input("Pays : 1 = Sénégal, 2 = Guinée [1] : ").strip() or "1")
    pays = {"1": "senegal", "2": "guinee"}.get(pays, "senegal")
    print("\nCompte du directeur (c'est avec lui que tu te connecteras) :")
    nom = input("Nom du directeur : ").strip()
    if len(nom) < 2:
        sys.exit("Nom trop court.")
    email = input("E-mail : ").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        sys.exit("E-mail invalide.")
    mdp = getpass.getpass("Mot de passe (8 caractères minimum) : ")
    if len(mdp) < 8:
        sys.exit("Mot de passe trop court (8 caractères minimum).")
    if getpass.getpass("Confirme le mot de passe : ") != mdp:
        sys.exit("Les deux mots de passe sont différents.")
    with Session(engine) as session:
        if session.exec(select(Utilisateur).where(Utilisateur.email == email)).first():
            sys.exit("Cet e-mail est déjà utilisé : choisis-en un autre.")
        print("\nCréation en cours…")
        r = remplir(session, nom_ecole, pays, nom, email, mdp)
    print(f"\n✔ {r['ecole']} : {r['classes']} classes, {r['eleves']} élèves, {r['professeurs']} professeurs.")
    print(f"\nConnecte-toi avec {email} et ton mot de passe.")
    print(f"Les autres comptes ont tous le mot de passe : {r['mot_de_passe_equipe']}\n")
    for role, nom_u, mail in r["comptes"]:
        print(f"  {role:<12} {nom_u:<28} {mail}")
    print("\nGardien, cuisinière et agent d'entretien sont dans la paie (sans accès au logiciel).")
    print("Les notes de la 1re période sont saisies mais pas publiées aux parents.")


COMMANDES = {"creer-superadmin": creer_superadmin, "generer-cle": generer_cle, "remplir-test": remplir_test}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDES:
        sys.exit(__doc__)
    COMMANDES[sys.argv[1]]()
