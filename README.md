# Gestion École — API (FastAPI + SQLite)

Première version d'un logiciel de gestion scolaire : classes, élèves, frais de scolarité et notes.

## Installation

```bash
python -m venv venv
# Windows : venv\Scripts\activate    |  Linux/Mac : source venv/bin/activate
pip install -r requirements.txt
```

## Lancer le serveur

```bash
uvicorn app.main:app --reload
```

Ouvre ensuite :
- **http://127.0.0.1:8000** : l'interface web (tableau de bord, classes, élèves, paiements, bulletins)
- **http://127.0.0.1:8000/docs** : la documentation interactive de l'API
La base `ecole.db` est créée automatiquement au premier lancement.

## Lancer les tests

```bash
pytest
```

## Routes principales

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/classes` | Créer une classe (nom, niveau, frais annuels) |
| GET | `/classes` | Lister les classes |
| GET | `/classes/{id}/eleves` | Élèves d'une classe |
| POST | `/eleves` | Inscrire un élève (matricule généré automatiquement) |
| GET | `/eleves?q=...` | Rechercher par nom, prénom ou matricule |
| PATCH | `/eleves/{id}` | Modifier un élève |
| GET | `/eleves/{id}/solde` | Total payé et reste à payer |
| GET | `/eleves/{id}/bulletin?trimestre=1` | Moyennes par matière et moyenne générale |
| POST | `/paiements` | Enregistrer un paiement (espèces, Orange Money, MTN MoMo, virement) |
| GET | `/eleves/{id}/paiements` | Historique des paiements |
| GET | `/stats` | Chiffres du tableau de bord (encaissé, impayés, taux de recouvrement) |
| GET | `/impayes?classe_id=...` | Élèves qui doivent encore de l'argent |
| POST | `/notes` | Ajouter une note (/20, coefficient, trimestre) |
| DELETE | `/notes/{id}` | Supprimer une note |

## Structure

```
app/
  main.py          point d'entrée FastAPI
  database.py      connexion SQLite
  models.py        tables et schémas (SQLModel)
  routers/         classes, eleves, paiements, notes, stats
  static/index.html  interface web (HTML + JS, sans framework)
tests/test_api.py  tests automatiques
```

## Prochaines étapes

1. Authentification (directeur / comptable) avec JWT
2. Reçus et bulletins en PDF
3. Import des élèves depuis Excel
4. Passage à PostgreSQL (Supabase) pour la production
