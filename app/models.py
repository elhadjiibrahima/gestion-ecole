from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


# ---------- Écoles ----------
class EcoleBase(SQLModel):
    nom: str
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: str = "FCFA"
    annee_scolaire: str = "2026-2027"
    indicatif_tel: str = "224"               # ajouté aux numéros des parents sans indicatif


class Ecole(EcoleBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EcoleUpdate(SQLModel):
    nom: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: Optional[str] = None
    indicatif_tel: Optional[str] = None


# ---------- Utilisateurs ----------
class Role(str, Enum):
    directeur = "directeur"      # tout
    comptable = "comptable"      # élèves, paiements, impayés
    enseignant = "enseignant"    # notes, bulletins


class UtilisateurBase(SQLModel):
    nom: str
    email: str = Field(index=True, unique=True)
    role: Role


class Utilisateur(UtilisateurBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    mot_de_passe_hash: str
    actif: bool = True


class UtilisateurCreate(SQLModel):
    nom: str
    email: EmailStr
    role: Role
    mot_de_passe: str = Field(min_length=8)


class UtilisateurPublic(UtilisateurBase):
    id: int
    ecole_id: int
    actif: bool


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


class Moi(SQLModel):
    utilisateur: UtilisateurPublic
    ecole: Ecole


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
    pass


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


class Note(NoteBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    annee_scolaire: Optional[str] = Field(default=None, index=True)


class NoteCreate(NoteBase):
    pass


class CaseNote(SQLModel):
    """Une case de la grille de saisie : une matière, sa note (ou vide)."""
    matiere_id: Optional[int] = None         # None : ancienne note sans matière enregistrée
    matiere: str
    coefficient: int
    valeur: Optional[float] = None
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


class SaisieNote(SQLModel):
    matiere_id: int
    valeur: Optional[float] = Field(default=None, ge=0, le=20)   # vide = effacer


# ---------- Réponses calculées ----------
class StatutPaiement(str, Enum):
    a_jour = "a_jour"            # tout payé
    partiel = "partiel"          # a commencé à payer
    rien_paye = "rien_paye"      # aucun paiement


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
    type: Literal["impaye", "bulletin"]
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
