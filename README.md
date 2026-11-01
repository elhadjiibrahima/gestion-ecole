# Gestion École — FastAPI + SQLite

Logiciel de gestion scolaire multi-écoles : classes, élèves, frais de scolarité, reçus, notes et bulletins.

## Fonctionnalités

- **Multi-écoles** : chaque école a son compte, ses données sont isolées des autres.
- **Comptes et rôles**
  - *Directeur* : tout, plus la gestion de l'équipe et des paramètres de l'école
  - *Comptable* : élèves, paiements, impayés, tableau de bord
  - *Enseignant* : notes et bulletins
- **Élèves** : inscription avec **premier paiement obligatoire** (reçu immédiat), matricule automatique, recherche, **import depuis Excel** (modèle fourni, sans paiement : pour reprendre des élèves existants)
- **Paiements** : espèces, Orange Money, MTN MoMo, virement — **reçu PDF numéroté** avec montant en lettres
- Un paiement ne peut pas dépasser le reste à payer (évite les erreurs de saisie)
- **Suivi des paiements par classe** (à jour / partiel / rien payé), état PDF par classe
- **Impayés** et **tableau de bord** (encaissé, reste à encaisser, taux de recouvrement)
- **Matières et coefficients** gérés par le directeur (liste usuelle créée automatiquement)
- **Saisie des notes en grille** : une case par matière, Entrée pour passer à la suivante, moyenne en direct, enregistrement automatique en changeant d'élève
- **Bulletins PDF** avec rang et appréciation, à l'unité ou pour toute une classe

## Installation

```bash
python -m venv venv
# Windows : venv\Scripts\activate    |  Linux/Mac : source venv/bin/activate
pip install -r requirements.txt
```

## Lancer

```bash
python -m uvicorn app.main:app --reload
```

- **http://127.0.0.1:8000** : l'application (clique sur « Créer un compte » la première fois)
- **http://127.0.0.1:8000/docs** : documentation interactive de l'API

La base `ecole.db` est créée automatiquement.

> **Tu avais déjà une ancienne version ?** Supprime le fichier `ecole.db` avant de relancer :
> la structure de la base a changé (écoles, comptes, n° de reçus).

## Tests

```bash
python -m pytest
```

## Configuration (variables d'environnement)

| Variable | Rôle | Par défaut |
|---|---|---|
| `SECRET_KEY` | Clé de signature des sessions. **À changer en production** (longue chaîne aléatoire) | valeur de dev |
| `TOKEN_HEURES` | Durée d'une session avant reconnexion | `12` |

Générer une clé : `python -c "import secrets; print(secrets.token_hex(32))"`

## Routes principales

| Méthode | Route | Rôle | Accès |
|---|---|---|---|
| POST | `/auth/inscription` | Créer une école + compte directeur | public |
| POST | `/auth/token` | Connexion (e-mail / mot de passe) | public |
| GET | `/auth/moi` | Utilisateur connecté et son école | tous |
| PATCH | `/ecole` | Modifier les infos de l'école | directeur |
| GET/POST | `/utilisateurs` | Lister / créer les comptes de l'équipe | directeur |
| PATCH | `/utilisateurs/{id}/actif` | Activer / désactiver un compte | directeur |
| GET/POST | `/classes` | Lister / créer des classes | tous / directeur |
| GET | `/classes/{id}/bulletins.pdf?trimestre=1` | Bulletins de toute la classe | tous |
| GET/POST | `/eleves` | Lister / inscrire un élève (avec `paiement` : montant, mode, référence) | tous / gestion |
| GET | `/eleves/modele-import.xlsx` | Modèle Excel d'import | gestion |
| POST | `/eleves/import` | Importer un fichier Excel | gestion |
| GET | `/eleves/{id}/bulletin?trimestre=1` | Bulletin (JSON) | tous |
| GET | `/eleves/{id}/bulletin.pdf?trimestre=1` | Bulletin PDF | tous |
| POST | `/paiements` | Enregistrer un paiement | gestion |
| GET | `/paiements/{id}/recu.pdf` | Reçu PDF | gestion |
| GET | `/impayes` | Élèves qui doivent encore de l'argent | gestion |
| GET | `/stats` | Chiffres du tableau de bord | gestion |
| GET/POST/DELETE | `/notes` | Notes | tous / pédagogie |
| GET/PUT | `/notes/grille?eleve_id=&trimestre=` | Grille de notes d'un élève (une case par matière) | tous / pédagogie |
| GET/POST/PATCH/DELETE | `/matieres` | Matières et coefficients | tous / directeur |
| POST | `/matieres/defaut` | Ajouter les matières usuelles manquantes | directeur |

*gestion* = directeur + comptable · *pédagogie* = directeur + enseignant

## Structure

```
app/
  main.py            point d'entrée FastAPI
  database.py        connexion SQLite
  models.py          tables et schémas (SQLModel)
  security.py        mots de passe, jetons JWT, rôles
  services.py        soldes, bulletins, rangs
  pdf.py             reçus et bulletins (ReportLab)
  excel.py           modèle et import Excel (openpyxl)
  routers/           auth, classes, eleves, matieres, paiements, notes, stats
  static/index.html  interface web
tests/               tests automatiques (pytest)
```

## Prochaines étapes

1. Rappels SMS aux parents en retard de paiement
2. Suivi des absences
3. Mise en ligne (PostgreSQL / Supabase + hébergement)
