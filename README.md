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
- Formulaire de paiement : classe puis élève, seuls les élèves qui doivent encore sont proposés
- **Suivi des paiements par classe** (à jour / partiel / rien payé), état PDF par classe
- **Annulation d'un paiement** saisi par erreur (directeur, motif obligatoire) : le paiement reste visible, barré, avec qui l'a annulé
- **Impayés** et **tableau de bord** (encaissé, reste à encaisser, taux de recouvrement)
- **Matières et coefficients** gérés par le directeur (liste usuelle créée automatiquement)
- **Saisie des notes en grille** : une case par matière, Entrée pour passer à la suivante, moyenne en direct, enregistrement automatique en changeant d'élève
- **Historique des notes** : qui a ajouté, modifié ou effacé chaque note, et quand (le directeur voit tout, l'enseignant ses saisies) ; la grille indique qui a rempli chaque case
- **Coefficients propres à une classe** (ex : Maths coef 5 en 3ème), applicables à plusieurs classes d'un coup
- **Absences et retards** : appel par classe en quelques clics, justification, total sur le bulletin
- **Bulletins PDF** avec rang et appréciation, à l'unité ou pour toute une classe

- **Messages aux parents** : rappels d'impayés et annonce des bulletins, envoyés par WhatsApp ou SMS en un clic (texte prérempli, aucun abonnement)
- **Exports Excel** : élèves, paiements (annulés compris), notes par classe
- **Mots de passe** : chacun change le sien, le directeur réinitialise ceux de l'équipe
- **Nouvelle année scolaire** : passage en classe supérieure proposé automatiquement, redoublants, élèves de fin de cycle ; frais remis à zéro, historique conservé

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

> **Mise à jour depuis une ancienne version :** la base `ecole.db` est mise à jour automatiquement
> au démarrage (nouvelles colonnes ajoutées, données conservées).

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
| POST | `/paiements/{id}/annuler` | Annuler un paiement (motif) | directeur |
| POST | `/auth/mot-de-passe` | Changer son mot de passe | tous |
| POST | `/utilisateurs/{id}/mot-de-passe` | Réinitialiser le mot de passe d'un membre | directeur |
| GET/PUT | `/matieres/coefficients` | Coefficients propres à une classe | tous / directeur |
| GET/POST | `/absences/appel` | Appel d'une classe pour un jour | pédagogie |
| GET/PATCH/DELETE | `/absences` | Liste, justification, suppression | pédagogie |
| GET | `/absences/resume?classe_id=` | Totaux par élève | pédagogie |
| GET | `/messages/impayes`, `/messages/bulletins` | Messages prêts pour WhatsApp / SMS | gestion |
| GET | `/export/eleves.xlsx`, `/export/paiements.xlsx` | Exports Excel | gestion |
| GET | `/export/notes.xlsx?trimestre=` | Export des notes (une feuille par classe) | pédagogie |
| GET/POST | `/annee/proposition`, `/annee/nouvelle` | Passage à la nouvelle année | directeur |
| GET | `/stats` | Chiffres du tableau de bord | gestion |
| GET/POST/DELETE | `/notes` | Notes | tous / pédagogie |
| GET | `/notes/historique?classe_id=&eleve_id=&utilisateur_id=` | Qui a saisi quoi | pédagogie |
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
  routers/           auth, classes, eleves, matieres, paiements, notes, stats,
                     absences, messages, exports, annee
  static/index.html  interface web
tests/               tests automatiques (pytest)
```

## Prochaines étapes

1. Mise en ligne (PostgreSQL + hébergement)
