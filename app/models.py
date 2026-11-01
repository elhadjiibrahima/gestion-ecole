from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import EmailStr
from sqlalchemy import Column, LargeBinary
from sqlmodel import Field, SQLModel


# ---------- Écoles ----------
class EcoleBase(SQLModel):
    nom: str
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: str = "FCFA"
    annee_scolaire: str = "2026-2027"
    indicatif_tel: str = "224"               # ajouté aux numéros des parents sans indicatif
    nb_devoirs: int = 2                      # colonnes « devoir » dans la grille de notes
    poids_composition: float = 2             # moyenne = (devoirs + composition × poids) / (1 + poids)
    seuil_admission: float = 10              # moyenne annuelle minimale pour passer


class Ecole(EcoleBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Abonnement (géré uniquement par le super admin)
    abonnement_statut: str = "essai"         # essai / actif / suspendu
    abonnement_fin: Optional[date] = None    # dernier jour d'accès
    plan: Optional[str] = None
    motif_suspension: Optional[str] = None


class EcoleUpdate(SQLModel):
    nom: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: Optional[str] = None
    indicatif_tel: Optional[str] = None
    nb_devoirs: Optional[int] = Field(default=None, ge=0, le=6)
    poids_composition: Optional[float] = Field(default=None, ge=0, le=10)
    seuil_admission: Optional[float] = Field(default=None, ge=0, le=20)


# ---------- Utilisateurs ----------
class Role(str, Enum):
    directeur = "directeur"      # tout
    comptable = "comptable"      # élèves, paiements, impayés
    enseignant = "enseignant"    # notes de ses matières dans ses classes, appel de ses classes
    surveillant = "surveillant"  # appel de toutes les classes, justification des absences et retards


class UtilisateurBase(SQLModel):
    nom: str
    email: str = Field(index=True, unique=True)
    role: Role


class Utilisateur(UtilisateurBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    mot_de_passe_hash: str
    actif: bool = True


class AffectationSaisie(SQLModel):
    matiere_id: int
    classe_id: int


class UtilisateurCreate(SQLModel):
    nom: str
    email: EmailStr
    role: Role
    mot_de_passe: str = Field(min_length=8)
    affectations: list[AffectationSaisie] = []   # enseignant : matières × classes


class UtilisateurPublic(UtilisateurBase):
    id: int
    ecole_id: int
    actif: bool
    affectations: list[AffectationSaisie] = []


class InscriptionEcole(SQLModel):
    """Création d'une nouvelle école + compte du directeur."""
    nom_ecole: str
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: str = "FCFA"
    nom_directeur: str
    email: EmailStr
    mot_de_passe: str = Field(min_length=8)


class ChangementMotDePasse(SQLModel):
    ancien: str
    nouveau: str = Field(min_length=8)


class ReinitialisationMotDePasse(SQLModel):
    nouveau: str = Field(min_length=8)


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


class AbonnementInfo(SQLModel):
    statut: str                              # essai / actif / expire / suspendu
    fin: Optional[date]
    jours_restants: Optional[int]
    message: Optional[str] = None
    contact: Optional[str] = None


class Moi(SQLModel):
    utilisateur: UtilisateurPublic
    ecole: Ecole
    abonnement: Optional[AbonnementInfo] = None


# ---------- Classes ----------
class ClasseBase(SQLModel):
    nom: str = Field(index=True)            # ex : "6ème A"
    niveau: str                              # ex : "Collège"
    frais_annuels: int = Field(ge=0)         # montant total à payer par élève


class Classe(ClasseBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)


class ClasseCreate(ClasseBase):
    pass


# ---------- Élèves ----------
class EleveBase(SQLModel):
    nom: str = Field(index=True)
    prenom: str
    date_naissance: Optional[date] = None
    telephone_parent: Optional[str] = None
    classe_id: int = Field(foreign_key="classe.id")


class Eleve(EleveBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    matricule: Optional[str] = Field(default=None, unique=True, index=True)
    actif: bool = True                       # False : a quitté l'école (fin de cycle, départ)
    annee_sortie: Optional[str] = None
    code_parent: Optional[str] = Field(default=None, index=True, exclude=True)   # jamais renvoyé dans les listes
    reduction_pct: float = 0                 # réduction sur les frais (2e enfant, enfant d'enseignant…)
    reduction_montant: int = 0               # ou montant fixe (bourse)
    reduction_motif: Optional[str] = None


class EleveUpdate(SQLModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    date_naissance: Optional[date] = None
    telephone_parent: Optional[str] = None
    classe_id: Optional[int] = None


class ResultatImport(SQLModel):
    importes: int
    erreurs: list[str]


# ---------- Paiements ----------
class ModePaiement(str, Enum):
    especes = "especes"
    orange_money = "orange_money"
    mtn_momo = "mtn_momo"
    virement = "virement"


class PaiementBase(SQLModel):
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    reference: Optional[str] = None          # n° de transaction Orange Money, etc.
    date_paiement: date = Field(default_factory=date.today)


class Paiement(PaiementBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    numero_recu: Optional[str] = Field(default=None, unique=True)
    enregistre_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    annee_scolaire: Optional[str] = Field(default=None, index=True)
    # Annulation (le paiement n'est jamais supprimé : il reste visible, barré)
    annule: bool = False
    annule_le: Optional[datetime] = None
    annule_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    motif_annulation: Optional[str] = None
    cle_client: Optional[str] = Field(default=None, index=True)


class AnnulationPaiement(SQLModel):
    motif: str = Field(min_length=3, max_length=200)


class PaiementDetail(SQLModel):
    """Paiement avec les noms (pour l'historique et les exports)."""
    id: int
    numero_recu: Optional[str]
    eleve_id: int
    montant: int
    mode: ModePaiement
    reference: Optional[str]
    date_paiement: date
    annee_scolaire: Optional[str]
    enregistre_par: Optional[str]
    annule: bool
    annule_le: Optional[datetime]
    annule_par: Optional[str]
    motif_annulation: Optional[str]


class PaiementCreate(PaiementBase):
    cle_client: Optional[str] = Field(default=None, max_length=64)   # évite les doublons (mode hors connexion)


# ---------- Inscription (élève + premier paiement) ----------
class PaiementInitial(SQLModel):
    """Premier versement, obligatoire à l'inscription."""
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    reference: Optional[str] = None


class EleveCreate(EleveBase):
    paiement: PaiementInitial


class EleveInscrit(SQLModel):
    eleve: Eleve
    paiement: Paiement



# ---------- Matières ----------
class MatiereBase(SQLModel):
    nom: str = Field(min_length=1, max_length=60)
    coefficient: int = Field(default=1, ge=1, le=20)


class Matiere(MatiereBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    ordre: int = 0                           # ordre d'affichage


class MatiereCreate(MatiereBase):
    pass


class MatiereUpdate(SQLModel):
    nom: Optional[str] = Field(default=None, min_length=1, max_length=60)
    coefficient: Optional[int] = Field(default=None, ge=1, le=20)


MATIERES_PAR_DEFAUT = [
    ("Mathématiques", 4), ("Français", 4), ("Anglais", 2), ("Physique-Chimie", 3),
    ("SVT", 3), ("Histoire-Géographie", 2), ("Éducation civique", 1), ("EPS", 1),
]


# ---------- Notes ----------
class NoteBase(SQLModel):
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    matiere: str
    valeur: float = Field(ge=0, le=20)
    coefficient: int = Field(default=1, ge=1)
    trimestre: int = Field(ge=1, le=3)
    type: str = Field(default="devoir")      # devoir / composition
    numero: int = Field(default=1, ge=1)     # devoir 1, devoir 2…


class Note(NoteBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    annee_scolaire: Optional[str] = Field(default=None, index=True)


class NoteCreate(NoteBase):
    pass


class CaseNote(SQLModel):
    """Une ligne de la grille de saisie : une matière, ses devoirs, sa composition et sa moyenne."""
    matiere_id: Optional[int] = None         # None : ancienne note sans matière enregistrée
    matiere: str
    coefficient: int
    devoirs: list[Optional[float]] = []
    composition: Optional[float] = None
    valeur: Optional[float] = None           # moyenne de la matière
    modifiable: bool = True                  # l'utilisateur peut-il saisir cette matière ?
    saisi_par: Optional[str] = None          # dernière personne à avoir modifié la case
    saisi_le: Optional[datetime] = None


# ---------- Historique des notes (qui a saisi quoi) ----------
class NoteHistorique(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    utilisateur_id: int = Field(foreign_key="utilisateur.id", index=True)
    matiere: str
    trimestre: int
    ancienne_valeur: Optional[float] = None  # None : la case était vide
    nouvelle_valeur: Optional[float] = None  # None : la note a été effacée
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    annee_scolaire: Optional[str] = Field(default=None, index=True)
    type_note: Optional[str] = None          # devoir 1, devoir 2, composition


class LigneHistorique(SQLModel):
    id: int
    date: datetime
    utilisateur: str
    role: str
    eleve_id: int
    eleve: str
    classe: str
    matiere: str
    trimestre: int
    ancienne_valeur: Optional[float]
    nouvelle_valeur: Optional[float]
    action: str                              # ajout / modification / suppression
    type_note: Optional[str] = None


class SaisieNote(SQLModel):
    matiere_id: int
    devoirs: Optional[list[Optional[float]]] = None              # None : ne pas toucher aux devoirs
    composition: Optional[float] = Field(default=None, ge=0, le=20)
    valeur: Optional[float] = Field(default=None, ge=0, le=20)   # ancienne saisie (une note) = composition
    effacer_composition: bool = False


# ---------- Réponses calculées ----------
class StatutPaiement(str, Enum):
    a_jour = "a_jour"            # tout payé
    partiel = "partiel"          # a commencé à payer
    rien_paye = "rien_paye"      # aucun paiement


class Echeance(SQLModel):
    nom: str
    date_limite: date
    montant: int
    paye: int                                # part de cette tranche déjà couverte
    reste: int
    statut: Literal["payee", "en_retard", "a_venir"]


class Solde(SQLModel):
    eleve_id: int
    matricule: Optional[str] = None
    nom_complet: str
    classe_id: int
    classe: str
    frais_annuels: int
    total_paye: int
    reste_a_payer: int
    nb_paiements: int = 0
    dernier_paiement: Optional[date] = None
    statut: StatutPaiement
    # Échéancier : ce qui devait déjà être payé à ce jour
    montant_exigible: int = 0
    retard: int = 0
    prochaine_echeance: Optional[date] = None
    prochain_montant: int = 0
    echeances: list[Echeance] = []
    frais_classe: int = 0                    # frais de la classe avant réduction
    reduction: int = 0
    reduction_motif: Optional[str] = None


class LigneBulletin(SQLModel):
    matiere: str
    moyenne: float
    coefficient: int


class Bulletin(SQLModel):
    eleve_id: int
    matricule: Optional[str]
    nom_complet: str
    classe: str
    trimestre: int
    matieres: list[LigneBulletin]
    moyenne_generale: Optional[float]
    rang: Optional[int]
    effectif: int
    appreciation: str
    absences: int = 0
    absences_justifiees: int = 0
    retards: int = 0
    # Renseignés sur le bulletin du 3e trimestre
    moyennes_trimestres: list[Optional[float]] = []
    moyenne_annuelle: Optional[float] = None
    rang_annuel: Optional[int] = None
    decision: Optional[str] = None           # Admis(e) / Redouble


# ---------- Coefficients spécifiques à une classe ----------
class CoefficientClasse(SQLModel, table=True):
    """Remplace le coefficient par défaut d'une matière pour une classe donnée."""
    id: Optional[int] = Field(default=None, primary_key=True)
    matiere_id: int = Field(foreign_key="matiere.id", index=True)
    classe_id: int = Field(foreign_key="classe.id", index=True)
    coefficient: int = Field(ge=1, le=20)


class LigneCoefficient(SQLModel):
    matiere_id: int
    matiere: str
    coefficient_defaut: int
    coefficient: int
    specifique: bool


class SaisieCoefficient(SQLModel):
    matiere_id: int
    coefficient: Optional[int] = Field(default=None, ge=1, le=20)   # None : revenir au défaut


class CoefficientsClasses(SQLModel):
    classe_ids: list[int] = Field(min_length=1)
    coefficients: list[SaisieCoefficient]


# ---------- Absences et retards ----------
class TypeAbsence(str, Enum):
    absence = "absence"
    retard = "retard"


class Absence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    jour: date = Field(index=True)
    type: TypeAbsence = TypeAbsence.absence
    trimestre: int = Field(ge=1, le=3)
    justifiee: bool = False
    motif: Optional[str] = None
    saisi_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    annee_scolaire: Optional[str] = Field(default=None, index=True)


class Appel(SQLModel):
    """Appel d'une classe pour un jour : la liste des absents et des retardataires."""
    classe_id: int
    jour: date
    trimestre: int = Field(ge=1, le=3)
    absents: list[int] = []
    retards: list[int] = []


class AbsenceUpdate(SQLModel):
    justifiee: Optional[bool] = None
    motif: Optional[str] = None


class AbsenceDetail(SQLModel):
    id: int
    eleve_id: int
    eleve: str
    classe: str
    jour: date
    type: TypeAbsence
    trimestre: int
    justifiee: bool
    motif: Optional[str]
    saisi_par: Optional[str]


class ResumeAbsences(SQLModel):
    eleve_id: int
    eleve: str
    absences: int
    absences_justifiees: int
    retards: int


# ---------- Messages aux parents ----------
class MessageEnvoye(SQLModel, table=True):
    """Journal des messages préparés pour les parents (WhatsApp / SMS)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    type: str                                # impaye / bulletin
    canal: str                               # whatsapp / sms
    utilisateur_id: int = Field(foreign_key="utilisateur.id")
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageParent(SQLModel):
    eleve_id: int
    eleve: str
    classe: str
    telephone: Optional[str]
    telephone_international: Optional[str]   # chiffres seulement, avec indicatif
    texte: str
    lien_whatsapp: Optional[str]
    lien_sms: Optional[str]
    dernier_envoi: Optional[datetime]


class JournalMessage(SQLModel):
    eleve_id: int
    type: Literal["impaye", "bulletin", "acces"]
    canal: Literal["whatsapp", "sms"]


# ---------- Nouvelle année scolaire ----------
class PassageClasse(SQLModel):
    classe_id: int
    vers_classe_id: Optional[int] = None     # None : les élèves quittent l'école (fin de cycle)
    redoublants: list[int] = []              # restent dans la même classe


class NouvelleAnnee(SQLModel):
    nouvelle_annee: str                      # ex : 2027-2028 (vérifié dans la route)
    passages: list[PassageClasse]


class ResultatNouvelleAnnee(SQLModel):
    annee_scolaire: str
    passes: int
    redoublants: int
    sortants: int


# ---------- Échéancier (tranches de paiement) ----------
class Tranche(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    annee_scolaire: str = Field(index=True)
    ordre: int = 0
    nom: str
    date_limite: date
    pourcentage: float                       # part des frais annuels de la classe


class TrancheSaisie(SQLModel):
    nom: str = Field(min_length=1, max_length=40)
    date_limite: date
    pourcentage: float = Field(gt=0, le=100)


# ---------- Caisse : dépenses ----------
class CategorieDepense(str, Enum):
    salaires = "salaires"
    loyer = "loyer"
    fournitures = "fournitures"
    electricite_eau = "electricite_eau"
    transport = "transport"
    entretien = "entretien"
    impots = "impots"
    autre = "autre"


class DepenseCreate(SQLModel):
    jour: date = Field(default_factory=date.today)
    categorie: CategorieDepense
    libelle: str = Field(min_length=2, max_length=120)
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    beneficiaire: Optional[str] = Field(default=None, max_length=80)


class Depense(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    annee_scolaire: str = Field(index=True)
    jour: date = Field(index=True)
    categorie: CategorieDepense
    libelle: str
    montant: int
    mode: ModePaiement = ModePaiement.especes
    beneficiaire: Optional[str] = None
    saisi_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    annule: bool = False
    annule_le: Optional[datetime] = None
    annule_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    motif_annulation: Optional[str] = None


class DepenseDetail(SQLModel):
    id: int
    jour: date
    categorie: CategorieDepense
    libelle: str
    montant: int
    mode: ModePaiement
    beneficiaire: Optional[str]
    saisi_par: Optional[str]
    annule: bool
    annule_par: Optional[str]
    motif_annulation: Optional[str]


class MoisCaisse(SQLModel):
    mois: str                                # 2026-11
    entrees: int
    sorties: int
    solde: int
    cumul: int


class BilanCaisse(SQLModel):
    annee_scolaire: str
    total_entrees: int
    total_sorties: int
    solde: int
    mois: list[MoisCaisse]
    par_categorie: dict[str, int]


# ---------- Photos des élèves ----------
class PhotoEleve(SQLModel, table=True):
    eleve_id: int = Field(foreign_key="eleve.id", primary_key=True)
    image: bytes = Field(sa_column=Column(LargeBinary, nullable=False))        # JPEG ≤ 400 px
    miniature: bytes = Field(sa_column=Column(LargeBinary, nullable=False))    # JPEG 80 px
    maj_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Miniature(SQLModel):
    eleve_id: int
    data_url: str


# ---------- Espace parents ----------
class ConnexionParent(SQLModel):
    matricule: str
    code: str


class CodeParent(SQLModel):
    eleve_id: int
    matricule: Optional[str]
    code: str


class AbsenceParent(SQLModel):
    jour: date
    type: TypeAbsence
    justifiee: bool
    motif: Optional[str]


class PaiementParent(SQLModel):
    id: int
    numero_recu: Optional[str]
    date_paiement: date
    montant: int
    mode: ModePaiement
    annule: bool


class EspaceParent(SQLModel):
    eleve_id: int
    nom: str
    prenom: str
    matricule: Optional[str]
    classe: str
    ecole: str
    ecole_telephone: Optional[str]
    devise: str
    annee_scolaire: str
    a_photo: bool
    solde: Solde
    paiements: list[PaiementParent]
    bulletins: list[Bulletin]
    absences: list[AbsenceParent]


# ---------- Enseignants : matières et classes attribuées ----------
class Affectation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    utilisateur_id: int = Field(foreign_key="utilisateur.id", index=True)
    matiere_id: int = Field(foreign_key="matiere.id", index=True)
    classe_id: int = Field(foreign_key="classe.id", index=True)


# ---------- Réductions ----------
class Reduction(SQLModel):
    pourcentage: float = Field(default=0, ge=0, le=100)
    montant: int = Field(default=0, ge=0)
    motif: Optional[str] = Field(default=None, max_length=80)


# ---------- Résultats annuels ----------
class ResultatAnnuel(SQLModel):
    eleve_id: int
    matricule: Optional[str]
    eleve: str
    moyennes_trimestres: list[Optional[float]]
    moyenne_annuelle: Optional[float]
    rang: Optional[int]
    decision: Optional[str]


# ---------- Super admin (gestion de toutes les écoles) ----------
class SuperAdmin(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    nom: str
    mot_de_passe_hash: str
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaiementAbonnement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    montant: int = Field(ge=0)
    devise: str = "FCFA"
    mois: int = Field(ge=0)
    debut: date
    fin: date
    mode: str = "especes"
    reference: Optional[str] = None
    note: Optional[str] = None
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    saisi_par_id: Optional[int] = Field(default=None, foreign_key="superadmin.id")


class ProlongationAbonnement(SQLModel):
    mois: int = Field(ge=1, le=36)
    montant: int = Field(ge=0)
    devise: str = "FCFA"
    mode: str = "especes"
    reference: Optional[str] = Field(default=None, max_length=60)
    note: Optional[str] = Field(default=None, max_length=200)
    plan: Optional[str] = Field(default=None, max_length=40)


class CorrectionAbonnement(SQLModel):
    fin: Optional[date] = None
    statut: Optional[Literal["essai", "actif"]] = None
    plan: Optional[str] = Field(default=None, max_length=40)


class Suspension(SQLModel):
    motif: str = Field(min_length=3, max_length=200)


class EcoleAdmin(SQLModel):
    """Vue d'une école pour le super admin."""
    id: int
    nom: str
    telephone: Optional[str]
    adresse: Optional[str]
    cree_le: datetime
    annee_scolaire: str
    directeur: Optional[str]
    email_directeur: Optional[str]
    nb_eleves: int
    nb_utilisateurs: int
    abonnement: AbonnementInfo
    plan: Optional[str]
    motif_suspension: Optional[str]
    total_abonnements: int
    derniere_activite: Optional[datetime]


class StatsPlateforme(SQLModel):
    nb_ecoles: int
    actives: int
    en_essai: int
    expirees: int
    suspendues: int
    expirent_bientot: int
    nb_eleves: int
    revenus_total: int
    revenus_mois: int
