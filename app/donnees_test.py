"""Remplit une école de test : toutes les classes du complexe, 3 élèves par classe, un professeur par matière,
le reste du personnel, les salaires, des paiements et des notes du 1er trimestre / semestre.

Lancement : ./remplir_test.sh (Linux, Mac, Git Bash) ou remplir_test.bat (Windows),
ou directement : python -m app.cli remplir-test
"""
import random
import re
import secrets
import unicodedata
from datetime import date, timedelta

from sqlmodel import Session, select

from app.cursus import regles_classe
from app.models import (
    Affectation, Classe, Ecole, Eleve, InscriptionEcole, ModePaiement, Note, Role, Utilisateur,
)
from app.routers.paie import postes_ecole
from app.security import hacher_mot_de_passe
from app.services import creer_paiement, matieres_classe, trier_classes, type_inscription

PRENOMS_F = ["Aminata", "Fatou", "Awa", "Mariama", "Khady", "Ndeye", "Aïssatou", "Coumba", "Astou", "Bineta"]
PRENOMS_M = ["Modou", "Cheikh", "Ousmane", "Moussa", "Ibrahima", "Abdoulaye", "Mamadou", "Serigne", "Pape", "Alioune"]
NOMS = ["Ndiaye", "Diop", "Fall", "Sow", "Ba", "Gueye", "Faye", "Sarr", "Mbaye", "Cissé", "Diallo", "Thiam", "Sy", "Kane"]
VILLES = ["Dakar", "Thiès", "Kaolack", "Saint-Louis", "Ziguinchor", "Touba", "Rufisque", "Mbour"]

# Mensualité et inscription de départ par cycle (à modifier ensuite dans Élèves → Classes)
TARIFS = {"prescolaire": (15000, 20000), "primaire": (20000, 25000), "college": (25000, 30000), "lycee": (30000, 35000)}
SALAIRES = {"directeur": 500000, "comptable": 250000, "enseignant": 3000, "surveillant": 150000, "agent": 75000}
AGE_CLASSE = {"petite section": 3, "moyenne section": 4, "grande section": 5, "ci": 6, "cp": 7, "cp1": 6, "cp2": 7,
              "ce1": 8, "ce2": 9, "cm1": 10, "cm2": 11}


def _slug(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", ".", t).strip(".")


def _age(classe: Classe) -> int:
    n = _slug(classe.nom).replace(".", " ")
    for k, v in AGE_CLASSE.items():
        if n.startswith(k):
            return v
    m = re.match(r"(\d+)", n)
    if m and 3 <= int(m.group(1)) <= 6:            # 6ème → 12 ans … 3ème → 15 ans
        return 12 + (6 - int(m.group(1)))
    if m and 7 <= int(m.group(1)) <= 12:           # Guinée : 7ème → 12 ans …
        return int(m.group(1)) + 5
    if n.startswith(("2nde", "seconde")):
        return 16
    if n.startswith(("1ere", "premiere")):
        return 17
    return 18


def _email_libre(session: Session, base: str, domaine: str) -> str:
    email, i = f"{base}@{domaine}", 2
    while session.exec(select(Utilisateur).where(Utilisateur.email == email)).first():
        email, i = f"{base}{i}@{domaine}", i + 1
    return email


def remplir(session: Session, nom_ecole: str, pays: str, nom_directeur: str, email: str, mot_de_passe: str,
            graine: int | None = None) -> dict:
    """Crée l'école et tout son contenu de test. Renvoie un résumé (dont le mot de passe des comptes créés)."""
    from app.routers.auth import creer_ecole_et_directeur
    hasard = random.Random(graine)
    creer_ecole_et_directeur(session, InscriptionEcole(
        nom_ecole=nom_ecole, nom_directeur=nom_directeur, email=email, mot_de_passe=mot_de_passe, pays=pays,
        adresse="Adresse de test", telephone="33 800 00 00", cycles=["prescolaire", "primaire", "college", "lycee"]),
        statut="actif", fin=date.today() + timedelta(days=365))
    directeur = session.exec(select(Utilisateur).where(Utilisateur.email == email.lower())).one()
    ecole = session.get(Ecole, directeur.ecole_id)
    domaine = email.split("@", 1)[1]
    mdp_equipe = "Test-" + secrets.token_hex(3)
    hache = hacher_mot_de_passe(mdp_equipe)
    classes = trier_classes(session.exec(select(Classe).where(Classe.ecole_id == ecole.id)).all())

    # Tarifs
    for c in classes:
        r = regles_classe(session, c)
        mens, insc = TARIFS.get(r.cycle.code if r.cycle else "college", (25000, 30000))
        if c.nom.lower().startswith(("terminale", "3ème", "cm2")):
            mens += 5000                       # classes d'examen un peu plus chères
        c.mensualite, c.frais_annuels, c.frais_inscription = mens, mens * ecole.nb_mois, insc
        session.add(c)
    type_ins = type_inscription(session, ecole.id, creer=True)
    session.flush()

    # Personnel : un professeur par matière (dans toutes les classes où elle est enseignée)
    classes_par_matiere: dict[int, tuple] = {}
    for c in classes:
        for m, _ in matieres_classe(session, c):
            classes_par_matiere.setdefault(m.id, (m, []))[1].append(c)
    comptes = []

    def membre(role: Role, nom: str, base: str) -> Utilisateur:
        u = Utilisateur(ecole_id=ecole.id, nom=nom, role=role, mot_de_passe_hash=hache,
                        email=_email_libre(session, base, domaine), telephone=f"77{hasard.randint(1000000, 9999999)}")
        session.add(u)
        session.flush()
        comptes.append((role.value, nom, u.email))
        return u

    profs = 0
    for m, cl in classes_par_matiere.values():
        sexe = hasard.choice("MF")
        nom = f"{hasard.choice(PRENOMS_F if sexe == 'F' else PRENOMS_M)} {hasard.choice(NOMS)}"
        prof = membre(Role.enseignant, nom, "prof." + _slug(m.nom))
        for c in cl:
            session.add(Affectation(utilisateur_id=prof.id, matiere_id=m.id, classe_id=c.id))
        profs += 1
    membre(Role.comptable, f"{hasard.choice(PRENOMS_F)} {hasard.choice(NOMS)}", "comptable")
    membre(Role.surveillant, f"{hasard.choice(PRENOMS_M)} {hasard.choice(NOMS)}", "surveillant")
    for metier in ("Gardien", "Cuisinière", "Agent d'entretien"):
        session.add(Utilisateur(ecole_id=ecole.id, nom=f"{hasard.choice(PRENOMS_M if metier == 'Gardien' else PRENOMS_F)} "
                                f"{hasard.choice(NOMS)} ({metier})", role=Role.agent, mot_de_passe_hash="!",
                                email=f"agent-{secrets.token_hex(6)}@sans-compte.local"))
    session.flush()
    for p in postes_ecole(session, ecole.id):
        if p.role in SALAIRES:
            p.montant = SALAIRES[p.role]
            session.add(p)

    # Élèves : 3 par classe, avec l'inscription payée et 0 à 2 mois de scolarité
    nb_eleves = 0
    modes = [ModePaiement.especes, ModePaiement.wave, ModePaiement.orange_money] if ecole.pays != "guinee" \
        else [ModePaiement.especes, ModePaiement.orange_money, ModePaiement.mtn_momo]
    annee_debut = int(ecole.annee_scolaire[:4])
    for c in classes:
        for _ in range(3):
            sexe = hasard.choice("MF")
            nom = hasard.choice(NOMS)
            naissance = date(annee_debut - _age(c), hasard.randint(1, 12), hasard.randint(1, 28))
            e = Eleve(ecole_id=ecole.id, classe_id=c.id, nom=nom, sexe=sexe,
                      prenom=hasard.choice(PRENOMS_F if sexe == "F" else PRENOMS_M), date_naissance=naissance,
                      lieu_naissance=hasard.choice(VILLES), nom_tuteur=f"{hasard.choice(PRENOMS_M)} {nom}",
                      telephone_parent=f"77{hasard.randint(1000000, 9999999)}", adresse="Quartier de test")
            session.add(e)
            session.flush()
            e.matricule = f"{date.today().year}-{e.id:05d}"
            creer_paiement(session, e, c.frais_inscription, hasard.choice(modes), None, None, directeur.id, type_ins.id)
            mois = hasard.choice([0, 1, 1, 2])
            if mois:
                creer_paiement(session, e, c.mensualite * mois, hasard.choice(modes), None, None, directeur.id)
            # Notes de la 1re période (non publiées) : 2 devoirs et une composition par matière
            bareme = regles_classe(session, c).bareme
            niveau = hasard.uniform(0.35, 0.9)            # chaque élève a son niveau
            for m, coef in matieres_classe(session, c):
                def note():
                    return round(max(0, min(bareme, hasard.gauss(niveau * bareme, bareme * 0.12))) * 4) / 4
                for typ, num in (("devoir", 1), ("devoir", 2), ("composition", 1)):
                    session.add(Note(eleve_id=e.id, matiere=m.nom, coefficient=coef, valeur=note(), trimestre=1,
                                     type=typ, numero=num, annee_scolaire=ecole.annee_scolaire))
            nb_eleves += 1
    session.commit()
    return {"ecole": ecole.nom, "classes": len(classes), "eleves": nb_eleves, "professeurs": profs,
            "comptes": comptes, "mot_de_passe_equipe": mdp_equipe}
