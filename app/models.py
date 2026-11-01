from datetime import date
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


# ---------- Classes ----------
class ClasseBase(SQLModel):
    nom: str = Field(index=True)            # ex : "6ème A"
    niveau: str                              # ex : "Collège"
    frais_annuels: int = Field(ge=0)         # montant total à payer par élève


class Classe(ClasseBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


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
    matricule: str = Field(unique=True, index=True)


class EleveCreate(EleveBase):
    pass


class EleveUpdate(SQLModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    date_naissance: Optional[date] = None
    telephone_parent: Optional[str] = None
    classe_id: Optional[int] = None


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


class PaiementCreate(PaiementBase):
    pass


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


# ---------- Réponses calculées ----------
class Solde(SQLModel):
    eleve_id: int
    nom_complet: str
    classe: str
    frais_annuels: int
    total_paye: int
    reste_a_payer: int


class LigneBulletin(SQLModel):
    matiere: str
    moyenne: float
    coefficient: int


class Bulletin(SQLModel):
    eleve_id: int
    nom_complet: str
    classe: str
    trimestre: int
    matieres: list[LigneBulletin]
    moyenne_generale: Optional[float]
