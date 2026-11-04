# Gestion École — FastAPI + SQLite

Logiciel de gestion scolaire multi-écoles : classes, élèves, frais de scolarité, reçus, notes et bulletins.

## Fonctionnalités

- **Multi-écoles** : chaque école a son compte, ses données sont isolées des autres.
- **Comptes et rôles**
  - *Directeur* : tout, plus la gestion de l'équipe et des paramètres de l'école
  - *Comptable* : élèves, paiements, impayés, caisse, tableau de bord
  - *Enseignant* : notes de **ses matières dans ses classes** (attribuées par le directeur), appel de ses classes
  - *Surveillant* : appel de toutes les classes, **justification** des absences et retards
  - 5 mots de passe faux = compte bloqué 15 minutes
- **Élèves** : inscription avec **premier paiement obligatoire** (reçu immédiat), matricule automatique, recherche, **import depuis Excel** (modèle fourni, sans paiement : pour reprendre des élèves existants)
- **Paiements** : espèces, Orange Money, MTN MoMo, virement — **reçu PDF numéroté** avec montant en lettres
- **Réductions et bourses** par élève (pourcentage et/ou montant, avec motif), prises en compte partout (reçus, soldes, rappels)
- Un paiement ne peut pas dépasser le reste à payer (évite les erreurs de saisie)
- Formulaire de paiement : classe puis élève, seuls les élèves qui doivent encore sont proposés
- **Suivi des paiements par classe** (à jour / partiel / rien payé), état PDF par classe
- **Annulation d'un paiement** saisi par erreur (directeur, motif obligatoire) : le paiement reste visible, barré, avec qui l'a annulé
- **Échéancier** : frais découpés en tranches avec dates limites ; retard calculé par élève, rappels ciblés
- **Caisse** : dépenses par catégorie (salaires, loyer, électricité…), bilan mensuel entrées / sorties / solde, export Excel
- **Impayés** et **tableau de bord** (encaissé, reste à encaisser, taux de recouvrement)
- **Matières et coefficients** gérés par le directeur (liste usuelle créée automatiquement)
- **Devoirs et compositions** : plusieurs devoirs + une composition par matière ; moyenne = (devoirs + composition × poids) ÷ (1 + poids), réglable
- **Moyenne annuelle** : moyenne des trimestres, rang annuel, décision admis / redouble selon un seuil réglable ; les redoublants sont proposés au passage d'année
- **Saisie des notes en grille** : une case par matière, Entrée pour passer à la suivante, moyenne en direct, enregistrement automatique en changeant d'élève
- **Historique des notes** : qui a ajouté, modifié ou effacé chaque note, et quand (le directeur voit tout, l'enseignant ses saisies) ; la grille indique qui a rempli chaque case
- **Coefficients propres à une classe** (ex : Maths coef 5 en 3ème), applicables à plusieurs classes d'un coup
- **Absences et retards** : appel par classe en quelques clics, justification, total sur le bulletin
- **Bulletins PDF** avec rang et appréciation, à l'unité ou pour toute une classe

- **Messages aux parents** : rappels d'impayés et annonce des bulletins, envoyés par WhatsApp ou SMS en un clic (texte prérempli, aucun abonnement)
- **Exports Excel** : élèves, paiements (annulés compris), notes par classe, caisse
- **Photos et cartes scolaires** : photo de l'élève (recadrée automatiquement), cartes PDF avec QR code, 10 par page
- **Espace parents** (`/espace-parents`) : avec le matricule et un code, le parent suit sur son téléphone les frais, l'échéancier, les reçus, les notes, les bulletins et les absences ; codes distribués en PDF ou par WhatsApp
- **Mode hors connexion** : application installable sur téléphone ; les écrans déjà consultés restent lisibles, et les paiements, notes et appels faits sans réseau sont envoyés automatiquement au retour de la connexion (sans doublon)
- **Mots de passe** : chacun change le sien, le directeur réinitialise ceux de l'équipe
- **Modifier un élève ou une classe** (nom, date de naissance, téléphone, changement de classe, frais) ; supprimer une classe créée par erreur
- **Départ en cours d'année** (motif, date) : l'élève sort des listes et des impayés, son historique reste ; réintégration possible
- **Paiement mensuel** (par défaut) : chaque classe a une mensualité et des frais d'inscription ; nombre de mois, premier mois et jour limite réglables ; mois réglés affichés au paiement et sur le reçu. Le paiement par tranches reste possible
- **Plusieurs types de frais** : scolarité + frais d'inscription, cantine, transport… pour tous ou pour les élèves choisis ; chaque paiement indique ce qu'il règle ; **prix par niveau** (tenue, cantine, transport moins chers au primaire…, 0 = gratuit)
- **Envoi groupé** des rappels par SMS (fournisseur payant à configurer), ou assistant WhatsApp « un clic par parent »
- **Logo et cachet** de l'école sur les reçus, bulletins, cartes et fiches de paie
- **Bulletin complet** : moyenne, min et max de la classe et rang par matière, appréciation de l'enseignant, avis du conseil de classe
- **Menu simplifié** en 5 groupes, utilisable sur téléphone
- **Mot de passe oublié** : code à 6 chiffres par SMS ou e-mail (si configuré), sinon le directeur réinitialise
- **Emploi du temps** par classe et par enseignant, contrôle des chevauchements (classe, enseignant, salle), PDF
- **Paie** : le directeur fixe un salaire par poste (Directeur, Comptable, Enseignant, Surveillant, et postes ajoutés : vacataire…), au mois ou à l'heure (heures proposées d'après l'emploi du temps), primes, retenues, avances retirées automatiquement, fiche de paie PDF, salaire ajouté aux dépenses de la caisse
- **Complexe scolaire (modèle sénégalais)** : à la création de l'école, choix du pays (Sénégal, Guinée) et des cycles ; les classes
  (Petite section… CI, CP… CM2, 6ème… 3ème, 2nde L/S… Terminale L/S), les matières de chaque cycle et leurs coefficients sont créés
  tout de suite. Classes types à ajouter en un clic, classe parallèle (6ème A → 6ème B), tarifs de toutes les classes sur un écran
- **Niveaux à soi** : en plus des cycles types, l'école peut créer un niveau (maternelle, formation professionnelle…),
  le placer dans le parcours, avec ses classes, ses règles de notes et ses matières
- **Règles par cycle** : 3 trimestres ou 2 semestres, notes sur 10 (primaire) ou sur 20, moyenne de passage ; matières et
  coefficients propres à chaque cycle, coefficients des séries L / S au lycée ; passage CM2 → 6ème, 3ème → 2nde (série à vérifier)
- **Organisation des enseignants** : un maître par classe au préscolaire et au primaire ; au collège un professeur enseigne
  1 ou 2 matières (maths-PC, français-HG…), au lycée une seule ; un enseignant reste dans son cycle (règles modifiables
  par cycle). Dans l'emploi du temps, la classe propose ses enseignants et l'enseignant ses matières
- **Saisie des notes par matière pour toute la classe** (un élève par ligne, Entrée descend dans la colonne), en plus de la saisie par élève
- **Publication des résultats** : les parents ne voient notes et bulletins qu'une fois publiés par le directeur
- **Bulletin officiel** : en-tête « République du Sénégal / Ministère / Inspection », sexe, date et lieu de naissance, professeur
  de chaque matière, distinction (félicitations, encouragements, tableau d'honneur, avertissement)
- **Fiche élève complète** (sexe, lieu de naissance, parent / tuteur, adresse) et **dossier** : frais, mois payés, résultats, paiements, absences
- **Guichet d'encaissement** : recherche par nom, matricule ou téléphone ; toute la famille (frères et sœurs reconnus au
  téléphone du parent) et tous ses frais (scolarité, inscription, tenue, cantine, transport…) sur un écran ; montants en un clic
  (1, 2, 3 mois, le retard, tout) ; un seul paiement et un seul reçu pour plusieurs frais et plusieurs enfants ; reçu envoyé
  au parent par WhatsApp
- **Frais au mois** (cantine, transport) dus à partir du mois d'inscription ; **réduction famille** à partir du 2e enfant ;
  **organismes qui paient** une part de la scolarité (ONG…), avec relevé PDF ; **délai accordé** à une famille ;
  **promesses de paiement**
- **Relances** : rappel avant l'échéance, après un retard, le jour d'une promesse ; un message par famille ; envoi automatique
  chaque matin par SMS si un fournisseur est configuré
- **Contrôle** : clôture de caisse du soir (argent compté, écart, versement banque, validation du directeur) ; alertes au
  directeur (annulation, réduction, écart, délai) ; rapprochement avec le relevé Wave / Orange Money ; prévision de trésorerie ;
  bulletin retenu tant que la scolarité n'est pas à jour (au choix)
- **Paiements Wave, Free Money, chèque** ; **caisse du jour** (par mode et par caissier, à imprimer le soir) ; **autres entrées**
  (dons, ventes, location) ; tableau de bord du jour et du mois, retards de mensualités séparés des autres frais
- **Personnel sans accès au logiciel** (gardien, cuisinière…) dans la paie ; **absences du personnel** : heures non payées ou retenue proposée
- **Cahier de textes** (cours et travail à faire, visibles des parents) et **statistiques** (effectifs par sexe, taux de réussite, Excel)
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

## École de test (données fictives)

Pour essayer le logiciel avec une école déjà remplie, lance (serveur arrêté ou non) :

- Windows : double-clic sur `remplir_test.bat`
- Linux, Mac, Git Bash : `./remplir_test.sh`
- ou : `python -m app.cli remplir-test`

Seuls le nom de l'école, le pays et le compte du directeur sont demandés. Sont créés : toutes les classes du complexe
(CI → terminale), 3 élèves par classe avec inscription et mensualités payées, un maître par classe au primaire, des professeurs de 2 matières au collège et d'une matière au lycée, un emploi du temps léger pour chaque
classe (1 h par matière, 2 h pour les grosses matières, sans conflit), des prix de tenue et de cantine par niveau, un comptable, un surveillant, un gardien, une cuisinière, un agent d'entretien,
les salaires des postes et les notes de la 1re période. Le mot de passe des comptes créés est affiché à la fin.

## Tests

```bash
python -m pytest
```

## Configuration et clés secrètes

**Aucune clé n'est écrite dans le code.** Tout ce qui est secret ou propre à une installation se règle par
des variables d'environnement, ou dans un fichier `.env` à la racine (modèle : `.env.example`).
Le fichier `.env` et la base `ecole.db` sont exclus de Git (`.gitignore`) : ils ne vont jamais sur GitHub.

| Variable | Rôle | Par défaut |
|---|---|---|
| `SECRET_KEY` | Signe les sessions (64 caractères aléatoires) | créée automatiquement dans `.env` sur un poste de développement |
| `ENVIRONNEMENT` | `production` : refuse de démarrer sans `SECRET_KEY` | `developpement` |
| `DATABASE_URL` | Base de données | `sqlite:///./ecole.db` |
| `ESSAI_JOURS` | Période d'essai d'une nouvelle école | `30` |
| `CONTACT_ABONNEMENT` | Contact affiché quand l'abonnement expire | – |
| `INSCRIPTION_PUBLIQUE` | `non` : seules les écoles ouvertes par le super admin existent | `oui` |
| `TOKEN_HEURES` | Durée d'une session | `12` |
| `SMS_FOURNISSEUR` | `orange` (API SMS Orange, payant) ou `simulation` ; vide = pas de SMS automatiques | – |
| `ORANGE_CLIENT_ID`, `ORANGE_CLIENT_SECRET` | Identifiants de l'API Orange (secrets) | – |
| `ORANGE_EXPEDITEUR`, `ORANGE_NOM_EXPEDITEUR` | Numéro et nom d'expéditeur des SMS | – |
| `SMTP_HOTE`, `SMTP_PORT`, `SMTP_UTILISATEUR`, `SMTP_MOT_DE_PASSE`, `SMTP_EXPEDITEUR` | Serveur d'e-mail pour les codes « mot de passe oublié » (`SMTP_HOTE=simulation` pour tester) | – |

Générer une clé : `python -m app.cli generer-cle`

Les mots de passe ne sont jamais stockés en clair (PBKDF2-SHA256 avec sel).

## Super admin (éditeur du logiciel)

Le super admin gère toutes les écoles : abonnements, suspensions, assistance. Son compte **ne se crée pas
depuis le site**, seulement sur le serveur :

```bash
python -m app.cli creer-superadmin
```

Puis connexion sur **http://127.0.0.1:8000/admin** :
- tableau de bord : écoles abonnées, en essai, expirées, qui finissent bientôt, revenus ;
- encaisser un abonnement (1, 3, 6 ou 12 mois) : l'accès est prolongé à la suite de la date de fin ;
- corriger une date, suspendre / réactiver une école, réinitialiser le mot de passe d'un directeur,
  ouvrir une école directement (avec des mois offerts).

Côté école : alerte 7 jours avant la fin ; à l'expiration, l'application est bloquée mais les **exports Excel
restent disponibles** (l'école peut récupérer ses données) ; une école **suspendue** est entièrement bloquée,
espace parents compris.

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
| GET/PUT | `/tranches` | Échéancier de l'année | tous / directeur |
| GET/POST | `/depenses`, `/depenses/{id}/annuler` | Dépenses | gestion / directeur |
| GET | `/caisse/bilan`, `/export/caisse.xlsx` | Bilan de caisse | gestion |
| PUT/GET/DELETE | `/eleves/{id}/photo` | Photo de l'élève | gestion / tous |
| GET | `/eleves/{id}/carte.pdf`, `/classes/{id}/cartes.pdf` | Cartes scolaires | gestion |
| GET/POST | `/eleves/{id}/code-parent` | Code d'accès parent (voir / nouveau) | gestion |
| GET | `/classes/{id}/codes-parents.pdf`, `/messages/acces` | Distribuer les codes | gestion |
| POST | `/parents/connexion` | Connexion parent (matricule + code) | public |
| GET | `/parents/moi`, `/parents/recu/{id}.pdf`, `/parents/bulletin.pdf` | Espace parents | parent |
| PUT | `/utilisateurs/{id}/affectations` | Matières × classes d'un enseignant | directeur |
| GET | `/classes/{id}/resultats-annuels` | Moyennes annuelles, rang, décision | tous |
| PUT | `/eleves/{id}/reduction` | Réduction / bourse | directeur |
| POST | `/admin/connexion` | Connexion super admin | public |
| GET | `/admin/stats`, `/admin/ecoles` | Vue de toutes les écoles | super admin |
| POST | `/admin/ecoles/{id}/abonnement` | Encaisser et prolonger | super admin |
| PATCH | `/admin/ecoles/{id}` | Corriger fin / statut / formule | super admin |
| POST | `/admin/ecoles/{id}/suspendre`, `/reactiver` | Suspendre / réactiver | super admin |
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
