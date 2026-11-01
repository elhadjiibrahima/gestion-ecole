from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


# ---------- Écoles ----------
class EcoleBase(SQLModel):
    nom: str
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: str = "FCFA"
    annee_scolaire: str = "2026-2027"


class Ecole(EcoleBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EcoleUpdate(SQLModel):
    nom: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: Optional[str] = None
    annee_scolaire: Optional[str] = None


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


class NoteCreate(NoteBase):
    pass


class CaseNote(SQLModel):
    """Une case de la grille de saisie : une matière, sa note (ou vide)."""
    matiere_id: Optional[int] = None         # None : ancienne note sans matière enregistrée
    matiere: str
    coefficient: int
    valeur: Optional[float] = None


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
