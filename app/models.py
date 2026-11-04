# « Date » plutôt que « date » : certains modèles ont un champ nommé « date », et Python 3.14+
# lirait alors le champ à la place du type (annotations évaluées après coup).
from datetime import date as Date, datetime, timezone
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
    # Paiement de la scolarité : « mensuel » (une mensualité par mois) ou « tranches » (échéancier libre)
    mode_paiement: str = "mensuel"
    nb_mois: int = 9                         # mois payés dans l'année (ex : octobre à juin)
    mois_debut: int = 10                     # 10 = octobre
    jour_limite: int = 5                     # la mensualité est due avant le 5 du mois
    pays: str = ""                           # senegal / guinee : en-tête officiel des bulletins
    inspection: Optional[str] = None         # ex : IA de Dakar — IEF de Dakar-Plateau (en-tête des bulletins)
    reduction_fratrie_pct: float = 0         # réduction sur la scolarité à partir du 2e enfant d'une même famille
    bloquer_bulletin_impaye: bool = False    # le parent ne voit le bulletin que si la scolarité est à jour
    relances_auto: bool = False              # rappels envoyés seuls par SMS (si un fournisseur est configuré)
    relance_jours_avant: int = 3             # rappel avant l'échéance
    relance_jours_apres: int = 5             # rappel après l'échéance non payée


class Ecole(EcoleBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Abonnement (géré uniquement par le super admin)
    abonnement_statut: str = "essai"         # essai / actif / suspendu
    abonnement_fin: Optional[Date] = None    # dernier jour d'accès
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
    mode_paiement: Optional[Literal["mensuel", "tranches"]] = None
    nb_mois: Optional[int] = Field(default=None, ge=1, le=12)
    mois_debut: Optional[int] = Field(default=None, ge=1, le=12)
    jour_limite: Optional[int] = Field(default=None, ge=1, le=28)
    reduction_fratrie_pct: Optional[float] = Field(default=None, ge=0, le=100)
    bloquer_bulletin_impaye: Optional[bool] = None
    relances_auto: Optional[bool] = None
    relance_jours_avant: Optional[int] = Field(default=None, ge=0, le=30)
    relance_jours_apres: Optional[int] = Field(default=None, ge=1, le=60)
    pays: Optional[Literal["senegal", "guinee", "autre"]] = None
    inspection: Optional[str] = Field(default=None, max_length=120)


# ---------- Utilisateurs ----------
class Role(str, Enum):
    directeur = "directeur"      # tout
    comptable = "comptable"      # élèves, paiements, impayés
    enseignant = "enseignant"    # notes de ses matières dans ses classes, appel de ses classes
    surveillant = "surveillant"  # appel de toutes les classes, justification des absences et retards
    agent = "agent"              # personnel sans accès au logiciel (gardien, cuisinière…) : seulement la paie


class UtilisateurBase(SQLModel):
    nom: str
    email: str = Field(index=True, unique=True)
    role: Role


class Utilisateur(UtilisateurBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    mot_de_passe_hash: str
    actif: bool = True
    telephone: Optional[str] = None          # pour recevoir un code si le mot de passe est oublié
    poste_id: Optional[int] = Field(default=None, foreign_key="poste.id")   # paie : vide = poste de son rôle


class AffectationSaisie(SQLModel):
    matiere_id: int
    classe_id: int


class UtilisateurCreate(SQLModel):
    nom: str
    email: EmailStr
    role: Role
    mot_de_passe: str = Field(min_length=8)
    telephone: Optional[str] = Field(default=None, max_length=30)
    affectations: list[AffectationSaisie] = []   # enseignant : matières × classes


class UtilisateurPublic(UtilisateurBase):
    id: int
    ecole_id: int
    actif: bool
    telephone: Optional[str] = None
    affectations: list[AffectationSaisie] = []


class InscriptionEcole(SQLModel):
    """Création d'une nouvelle école + compte du directeur."""
    nom_ecole: str
    adresse: Optional[str] = None
    telephone: Optional[str] = None
    devise: Optional[str] = None             # vide : celle du pays
    pays: Optional[Literal["senegal", "guinee", "autre"]] = None
    # Cycles de l'école : leurs classes et matières sont créées tout de suite (vide : matières usuelles seulement)
    cycles: list[Literal["prescolaire", "primaire", "college", "lycee"]] = []
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
    fin: Optional[Date]
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
    frais_annuels: int = Field(default=0, ge=0)   # scolarité de l'année par élève
    mensualite: int = Field(default=0, ge=0)      # paiement mensuel : frais_annuels = mensualité × nombre de mois
    frais_inscription: int = Field(default=0, ge=0)   # inscription / réinscription, due chaque année


class Classe(ClasseBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)


class ClasseCreate(ClasseBase):
    pass


# ---------- Élèves ----------
class EleveBase(SQLModel):
    nom: str = Field(index=True)
    prenom: str
    date_naissance: Optional[Date] = None
    telephone_parent: Optional[str] = None
    classe_id: int = Field(foreign_key="classe.id")
    sexe: Optional[str] = Field(default=None, max_length=1)              # M ou F
    lieu_naissance: Optional[str] = Field(default=None, max_length=80)
    nom_tuteur: Optional[str] = Field(default=None, max_length=80)      # père, mère ou tuteur
    adresse: Optional[str] = Field(default=None, max_length=120)


class Eleve(EleveBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    matricule: Optional[str] = Field(default=None, unique=True, index=True)
    actif: bool = True                       # False : a quitté l'école (fin de cycle, départ)
    annee_sortie: Optional[str] = None
    date_depart: Optional[Date] = None       # départ en cours d'année
    motif_depart: Optional[str] = None
    code_parent: Optional[str] = Field(default=None, index=True, exclude=True)   # jamais renvoyé dans les listes
    reduction_pct: float = 0                 # réduction sur les frais (2e enfant, enfant d'enseignant…)
    reduction_montant: int = 0               # ou montant fixe (bourse)
    moratoire_jusquau: Optional[Date] = None # délai accordé : pas « en retard » jusqu'à cette date
    moratoire_motif: Optional[str] = None
    tiers_id: Optional[int] = Field(default=None, foreign_key="tiers.id")   # ONG, entreprise… qui paie une part
    tiers_pct: int = 0                       # part de la scolarité payée par ce tiers
    reduction_motif: Optional[str] = None


class EleveUpdate(SQLModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    date_naissance: Optional[Date] = None
    telephone_parent: Optional[str] = None
    classe_id: Optional[int] = None
    sexe: Optional[Literal["M", "F"]] = None
    lieu_naissance: Optional[str] = Field(default=None, max_length=80)
    nom_tuteur: Optional[str] = Field(default=None, max_length=80)
    adresse: Optional[str] = Field(default=None, max_length=120)


class Depart(SQLModel):
    motif: str = Field(min_length=3, max_length=120)
    date_depart: Optional[Date] = None


class ResultatImport(SQLModel):
    importes: int
    erreurs: list[str]


# ---------- Paiements ----------
class ModePaiement(str, Enum):
    especes = "especes"
    orange_money = "orange_money"
    mtn_momo = "mtn_momo"
    wave = "wave"
    free_money = "free_money"
    virement = "virement"
    cheque = "cheque"


class PaiementBase(SQLModel):
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    type_frais_id: Optional[int] = Field(default=None, foreign_key="typefrais.id")   # vide = scolarité
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    reference: Optional[str] = None          # n° de transaction Orange Money, etc.
    date_paiement: Date = Field(default_factory=Date.today)


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
    cree_le: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))   # heure de saisie
    groupe: Optional[str] = Field(default=None, index=True)   # paiements faits ensemble (un seul reçu)
    tiers_id: Optional[int] = Field(default=None, foreign_key="tiers.id")   # payé par un tiers (ONG…)


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
    date_paiement: Date
    annee_scolaire: Optional[str]
    type_frais_id: Optional[int] = None
    frais: str = "Scolarité"
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
    type_frais_id: Optional[int] = None      # vide = scolarité ; ex : frais d'inscription
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    reference: Optional[str] = None


class EleveCreate(EleveBase):
    paiement: PaiementInitial
    autres_paiements: list[PaiementInitial] = []   # payés en même temps (tenue, 1er mois…) : un seul reçu


class EleveInscrit(SQLModel):
    eleve: Eleve
    paiement: Paiement
    paiements: list[Paiement] = []
    groupe: Optional[str] = None



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
    appreciation: Optional[str] = None
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
    appreciation: Optional[str] = Field(default=None, max_length=120)   # None : ne pas toucher


# ---------- Réponses calculées ----------
class StatutPaiement(str, Enum):
    a_jour = "a_jour"            # tout payé
    partiel = "partiel"          # a commencé à payer
    rien_paye = "rien_paye"      # aucun paiement


class LigneFrais(SQLModel):
    type_frais_id: Optional[int]             # None : scolarité
    nom: str
    du: int
    paye: int
    reste: int
    tiers_id: Optional[int] = None           # part payée par un tiers (ONG, entreprise…)
    mensuel: bool = False                    # cantine, transport… payés au mois
    montant_mois: int = 0
    exigible: int = 0                        # dû à ce jour
    retard: int = 0


class Echeance(SQLModel):
    nom: str
    date_limite: Date
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
    dernier_paiement: Optional[Date] = None
    statut: StatutPaiement
    # Échéancier : ce qui devait déjà être payé à ce jour
    montant_exigible: int = 0
    retard: int = 0
    prochaine_echeance: Optional[Date] = None
    prochain_montant: int = 0
    echeances: list[Echeance] = []
    lignes: list[LigneFrais] = []            # détail par type de frais
    paye_jusqua: Optional[str] = None        # dernier mois (ou tranche) entièrement payé
    moratoire_jusquau: Optional[Date] = None
    telephone_parent: Optional[str] = None
    tiers: Optional[str] = None
    retard_scolarite: int = 0                # mois / tranches échus non payés
    retard_autres: int = 0                   # autres frais dus (inscription, tenue…)
    frais_classe: int = 0                    # scolarité de la classe avant réduction
    reduction: int = 0
    reduction_motif: Optional[str] = None


class LigneBulletin(SQLModel):
    matiere: str
    professeur: Optional[str] = None
    moyenne: float
    coefficient: int
    moyenne_classe: Optional[float] = None
    min_classe: Optional[float] = None
    max_classe: Optional[float] = None
    rang_matiere: Optional[int] = None
    appreciation: Optional[str] = None       # écrite par l'enseignant


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
    avis_conseil: Optional[str] = None       # avis du conseil de classe (directeur)
    moyenne_classe: Optional[float] = None   # moyenne générale de la classe
    mention: Optional[str] = None            # Félicitations, Tableau d'honneur, Avertissement…
    bareme: int = 20                         # notes sur 10 (primaire) ou sur 20
    periode: str = "Trimestre"               # Trimestre ou Semestre
    nb_periodes: int = 3
    publie: bool = True                      # visible des parents
    sexe: Optional[str] = None
    date_naissance: Optional[Date] = None
    lieu_naissance: Optional[str] = None


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
    jour: Date = Field(index=True)
    type: TypeAbsence = TypeAbsence.absence
    trimestre: int = Field(ge=1, le=3)
    justifiee: bool = False
    motif: Optional[str] = None
    saisi_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    annee_scolaire: Optional[str] = Field(default=None, index=True)


class Appel(SQLModel):
    """Appel d'une classe pour un jour : la liste des absents et des retardataires."""
    classe_id: int
    jour: Date
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
    jour: Date
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
    utilisateur_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")   # vide : envoi automatique
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
    type: Literal["impaye", "bulletin", "acces", "relance", "recu"]
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
    date_limite: Date
    pourcentage: float                       # part des frais annuels de la classe


class TrancheSaisie(SQLModel):
    nom: str = Field(min_length=1, max_length=40)
    date_limite: Date
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
    jour: Date = Field(default_factory=Date.today)
    categorie: CategorieDepense
    libelle: str = Field(min_length=2, max_length=120)
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    beneficiaire: Optional[str] = Field(default=None, max_length=80)


class Depense(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    annee_scolaire: str = Field(index=True)
    jour: Date = Field(index=True)
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
    jour: Date
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
    total_recettes: int = 0                  # dont autres entrées (dons, ventes…)
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
    jour: Date
    type: TypeAbsence
    justifiee: bool
    motif: Optional[str]


class PaiementParent(SQLModel):
    frais: str = "Scolarité"
    id: int
    numero_recu: Optional[str]
    date_paiement: Date
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
    bulletins: list[Bulletin]                # seulement les périodes publiées par l'école
    absences: list[AbsenceParent]
    periode: str = "Trimestre"
    nb_periodes: int = 3
    bareme: int = 20
    cahier: list["LigneCahier"] = []         # cours récents et travail à faire
    bulletins_retenus: bool = False          # l'école garde les bulletins tant que la scolarité n'est pas à jour


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
    debut: Date
    fin: Date
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
    fin: Optional[Date] = None
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


# ---------- Autres frais (inscription, cantine, transport…) ----------
class TypeFrais(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    nom: str
    montant: int = Field(ge=0)
    obligatoire: bool = True                 # True : tous les élèves ; False : seulement ceux inscrits
    actif: bool = True
    ordre: int = 0
    code: Optional[str] = None               # « inscription » : montant fixé par classe
    mensuel: bool = False                    # montant par mois (cantine, transport) au lieu d'un montant annuel


class TypeFraisSaisie(SQLModel):
    nom: str = Field(min_length=2, max_length=40)
    montant: int = Field(ge=0)
    obligatoire: bool = True
    mensuel: bool = False


class FraisNiveau(SQLModel, table=True):
    """Prix d'un frais pour un niveau (ex : tenue 12 000 au primaire, 15 000 au lycée). Sans ligne : prix par défaut."""
    id: Optional[int] = Field(default=None, primary_key=True)
    type_frais_id: int = Field(foreign_key="typefrais.id", index=True)
    cycle_id: int = Field(foreign_key="cycle.id", index=True)
    montant: int = Field(ge=0)


class PrixNiveau(SQLModel):
    cycle_id: int
    montant: Optional[int] = Field(default=None, ge=0)     # vide : prix par défaut


class SouscriptionFrais(SQLModel, table=True):
    """Élève inscrit à un frais facultatif (cantine, transport…) pour une année."""
    id: Optional[int] = Field(default=None, primary_key=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    type_frais_id: int = Field(foreign_key="typefrais.id", index=True)
    annee_scolaire: str = Field(index=True)
    debut: Optional[Date] = None             # frais au mois : à partir de quel mois (inscription en cours d'année)


class FraisEleve(SQLModel):
    type_frais_id: int
    nom: str
    montant: int
    obligatoire: bool
    souscrit: bool
    mensuel: bool = False


# ---------- Logo et cachet de l'école ----------
class ImageEcole(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    type: str                                # logo / cachet
    image: bytes = Field(sa_column=Column(LargeBinary, nullable=False))   # PNG


# ---------- Bulletin : appréciations et conseil de classe ----------
class AppreciationMatiere(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    matiere: str
    trimestre: int
    annee_scolaire: str = Field(index=True)
    texte: str
    utilisateur_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")


class AvisConseil(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    trimestre: int
    annee_scolaire: str = Field(index=True)
    texte: str
    utilisateur_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")


class AvisSaisie(SQLModel):
    texte: str = Field(default="", max_length=300)


# ---------- Emploi du temps ----------
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]


class Creneau(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    classe_id: int = Field(foreign_key="classe.id", index=True)
    jour: int = Field(ge=1, le=6)            # 1 = lundi … 6 = samedi
    debut: str                               # « 08:00 »
    fin: str                                 # « 09:00 »
    matiere_id: int = Field(foreign_key="matiere.id")
    enseignant_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id", index=True)
    salle: Optional[str] = None


class CreneauSaisie(SQLModel):
    classe_id: int
    jour: int = Field(ge=1, le=6)
    debut: str = Field(min_length=4, max_length=5)
    fin: str = Field(min_length=4, max_length=5)
    matiere_id: int
    enseignant_id: Optional[int] = None
    salle: Optional[str] = Field(default=None, max_length=30)


class CreneauDetail(SQLModel):
    id: int
    classe_id: int
    classe: str
    jour: int
    debut: str
    fin: str
    matiere_id: int
    matiere: str
    enseignant_id: Optional[int]
    enseignant: Optional[str]
    salle: Optional[str]


# ---------- Paie du personnel ----------
class Poste(SQLModel, table=True):
    """Poste de travail et son salaire, fixé par le directeur (ex : Enseignant, 3 000 / heure)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    nom: str
    type: str = "mensuel"                    # mensuel (salaire fixe) / horaire (taux par heure de cours)
    montant: int = Field(default=0, ge=0)
    role: Optional[str] = None               # poste attribué par défaut aux comptes de ce rôle


class PosteSaisie(SQLModel):
    nom: str = Field(min_length=2, max_length=40)
    type: Literal["mensuel", "horaire"]
    montant: int = Field(ge=0)


class PosteDetail(SQLModel):
    id: int
    nom: str
    type: str
    montant: int
    role: Optional[str]
    effectif: int


class ChoixPoste(SQLModel):
    poste_id: int


class Avance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    utilisateur_id: int = Field(foreign_key="utilisateur.id", index=True)
    jour: Date
    montant: int = Field(gt=0)
    motif: Optional[str] = None
    deduite_sur: Optional[int] = Field(default=None, foreign_key="fichepaie.id")   # fiche qui l'a remboursée
    depense_id: Optional[int] = Field(default=None, foreign_key="depense.id")


class AvanceSaisie(SQLModel):
    utilisateur_id: int
    montant: int = Field(gt=0)
    motif: Optional[str] = Field(default=None, max_length=120)
    jour: Optional[Date] = None
    mode: ModePaiement = ModePaiement.especes


class FichePaie(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    utilisateur_id: int = Field(foreign_key="utilisateur.id", index=True)
    mois: str = Field(index=True)            # 2026-11
    type: str
    heures: Optional[float] = None
    taux: int                                # salaire mensuel ou taux horaire
    brut: int
    primes: int = 0
    retenues: int = 0
    avances_deduites: int = 0
    net: int
    note: Optional[str] = None
    payee: bool = False
    paye_le: Optional[Date] = None
    depense_id: Optional[int] = Field(default=None, foreign_key="depense.id")


class FicheSaisie(SQLModel):
    utilisateur_id: int
    mois: str = Field(min_length=7, max_length=7)
    heures: Optional[float] = Field(default=None, ge=0, le=400)
    primes: int = Field(default=0, ge=0)
    retenues: Optional[int] = Field(default=None, ge=0)   # vide : retenue proposée pour les absences
    note: Optional[str] = Field(default=None, max_length=120)


class PaiementFiche(SQLModel):
    mode: ModePaiement = ModePaiement.especes


class FicheDetail(SQLModel):
    id: int
    utilisateur_id: int
    nom: str
    role: str
    mois: str
    type: str
    heures: Optional[float]
    taux: int
    brut: int
    primes: int
    retenues: int
    avances_deduites: int
    net: int
    note: Optional[str]
    payee: bool
    paye_le: Optional[Date]


class LignePersonnel(SQLModel):
    utilisateur_id: int
    nom: str
    role: str
    poste_id: Optional[int]
    poste: Optional[str]
    contrat_type: Optional[str]
    contrat_montant: Optional[int]
    heures_prevues: Optional[float]          # d'après l'emploi du temps, pour le mois choisi
    avances_en_cours: int
    sans_compte: bool = False                # gardien, cuisinière… (pas d'accès au logiciel)
    telephone: Optional[str] = None
    jours_absence: float = 0                 # absences non justifiées du mois (en jours)
    heures_absence: float = 0                # heures de cours manquées (non justifiées)
    retenue_proposee: int = 0
    fiche: Optional[FicheDetail]


# ---------- Mot de passe oublié ----------
class CodeReinitialisation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    utilisateur_id: int = Field(foreign_key="utilisateur.id", index=True)
    code_hash: str
    expire_le: datetime
    essais: int = 0
    utilise: bool = False
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DemandeCode(SQLModel):
    email: str


class Reinitialisation(SQLModel):
    email: str
    code: str = Field(min_length=6, max_length=6)
    nouveau: str = Field(min_length=8)


class ProfilSaisie(SQLModel):
    telephone: Optional[str] = Field(default=None, max_length=30)


# ---------- Envoi groupé ----------
class EnvoiGroupe(SQLModel):
    type: Literal["impaye", "bulletin", "acces"]
    classe_id: Optional[int] = None
    trimestre: Optional[int] = None
    en_retard: bool = False
    eleve_ids: Optional[list[int]] = None    # vide : tous les messages de la liste


class ResultatEnvoi(SQLModel):
    envoyes: int
    sans_numero: int
    echecs: list[str]


# ---------- Cycles d'études (Préscolaire, Primaire, Collège, Lycée) ----------
class Cycle(SQLModel, table=True):
    """Règles d'un cycle : nombre de périodes (3 trimestres / 2 semestres), notes sur 10 ou 20, moyenne de passage.
    Une classe appartient au cycle dont le nom est son « niveau »."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    nom: str                                 # Primaire, Collège…
    code: Optional[str] = None               # prescolaire / primaire / college / lycee
    ordre: int = 0
    periodes: int = 3                        # 3 trimestres ou 2 semestres
    bareme: int = 20                         # notes sur 10 ou sur 20
    seuil: float = 10                        # moyenne annuelle pour passer
    enseignement: str = "matiere"            # « classe » : un maître par classe (toutes les matières) ; « matiere » : profs spécialisés
    max_matieres: Optional[int] = None       # matières différentes par professeur (collège 2, lycée 1)


class CycleSaisie(SQLModel):
    nom: str = Field(min_length=2, max_length=40)
    periodes: Literal[2, 3]
    bareme: Literal[10, 20]
    seuil: float = Field(ge=0, le=20)
    enseignement: Optional[Literal["classe", "matiere"]] = None
    max_matieres: Optional[int] = Field(default=None, ge=1, le=10)


class CycleCreation(CycleSaisie):
    """Nouveau niveau (ex : Maternelle, Formation professionnelle) avec ses classes."""
    classes: list[str] = Field(default=[], max_length=30)   # noms des classes à créer tout de suite
    avant_cycle_id: Optional[int] = None     # place dans le parcours (vide : à la fin)


class MatiereCycle(SQLModel, table=True):
    """Matière enseignée dans un cycle, avec son coefficient. Une matière sans ligne ici est enseignée partout."""
    id: Optional[int] = Field(default=None, primary_key=True)
    matiere_id: int = Field(foreign_key="matiere.id", index=True)
    cycle_id: int = Field(foreign_key="cycle.id", index=True)
    coefficient: int = Field(default=1, ge=1, le=20)


class MatiereCycleSaisie(SQLModel):
    cycle_id: int
    coefficient: Optional[int] = Field(default=None, ge=1, le=20)   # None : matière pas enseignée dans ce cycle


class ClasseType(SQLModel):
    nom: str
    niveau: str
    existe: bool


class TarifClasse(SQLModel):
    classe_id: int
    mensualite: Optional[int] = Field(default=None, ge=0)
    frais_annuels: Optional[int] = Field(default=None, ge=0)
    frais_inscription: Optional[int] = Field(default=None, ge=0)


# ---------- Publication des résultats ----------
class Publication(SQLModel, table=True):
    """Les parents ne voient les notes d'une période qu'une fois publiées par le directeur."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    classe_id: int = Field(foreign_key="classe.id", index=True)
    trimestre: int
    annee_scolaire: str = Field(index=True)
    publie_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    publie_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")


class EtatPublication(SQLModel):
    classe_id: int
    classe: str
    trimestre: int
    publie: bool
    publie_le: Optional[datetime] = None
    notes_saisies: int
    eleves: int


# ---------- Saisie des notes par matière (toute la classe) ----------
class LigneNoteClasse(SQLModel):
    eleve_id: int
    eleve: str
    devoirs: list[Optional[float]] = []
    composition: Optional[float] = None
    moyenne: Optional[float] = None
    appreciation: Optional[str] = None


class NotesClasse(SQLModel):
    classe_id: int
    matiere_id: int
    matiere: str
    coefficient: int
    trimestre: int
    bareme: int
    nb_devoirs: int
    modifiable: bool
    lignes: list[LigneNoteClasse]


class SaisieNoteClasse(SQLModel):
    eleve_id: int
    devoirs: Optional[list[Optional[float]]] = None
    composition: Optional[float] = Field(default=None, ge=0, le=20)
    effacer_composition: bool = False
    appreciation: Optional[str] = Field(default=None, max_length=120)


# ---------- Autres entrées d'argent ----------
class Recette(SQLModel, table=True):
    """Argent reçu en dehors des frais des élèves : dons, ventes, location de salle…"""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    annee_scolaire: str = Field(index=True)
    jour: Date = Field(index=True)
    categorie: str                           # don / vente / location / subvention / autre
    libelle: str
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    saisi_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")


class RecetteSaisie(SQLModel):
    categorie: Literal["don", "vente", "location", "subvention", "autre"]
    libelle: str = Field(min_length=2, max_length=120)
    montant: int = Field(gt=0)
    mode: ModePaiement = ModePaiement.especes
    jour: Optional[Date] = None


class LigneCaisseJour(SQLModel):
    heure: Optional[datetime] = None
    type: str                                # paiement / recette / depense
    libelle: str
    mode: str
    montant: int                             # négatif pour une sortie
    par: Optional[str] = None
    numero: Optional[str] = None


class CaisseJour(SQLModel):
    jour: Date
    entrees: int
    sorties: int
    solde: int
    par_mode: dict[str, int]                 # net par mode (espèces, Orange Money…)
    par_caissier: dict[str, int]             # encaissé par chaque personne
    lignes: list[LigneCaisseJour]


# ---------- Personnel : absences ----------
class AbsencePersonnel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    utilisateur_id: int = Field(foreign_key="utilisateur.id", index=True)
    jour: Date = Field(index=True)
    heures: Optional[float] = None           # heures de cours manquées (vide : la journée)
    motif: Optional[str] = None
    justifiee: bool = False


class AbsencePersonnelSaisie(SQLModel):
    utilisateur_id: int
    jour: Date
    heures: Optional[float] = Field(default=None, gt=0, le=24)
    motif: Optional[str] = Field(default=None, max_length=120)
    justifiee: bool = False


class AgentSaisie(SQLModel):
    nom: str = Field(min_length=2, max_length=80)
    telephone: Optional[str] = Field(default=None, max_length=30)
    poste_id: Optional[int] = None


# ---------- Cahier de textes ----------
class CahierTexte(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    classe_id: int = Field(foreign_key="classe.id", index=True)
    matiere_id: int = Field(foreign_key="matiere.id")
    enseignant_id: int = Field(foreign_key="utilisateur.id")
    jour: Date = Field(index=True)
    contenu: str                             # ce qui a été fait en cours
    travail: Optional[str] = None            # travail à faire
    pour_le: Optional[Date] = None
    annee_scolaire: str = Field(index=True)


class CahierSaisie(SQLModel):
    classe_id: int
    matiere_id: int
    jour: Date
    contenu: str = Field(min_length=3, max_length=2000)
    travail: Optional[str] = Field(default=None, max_length=1000)
    pour_le: Optional[Date] = None


class LigneCahier(SQLModel):
    id: int
    classe_id: int
    classe: str
    matiere: str
    enseignant: str
    jour: Date
    contenu: str
    travail: Optional[str]
    pour_le: Optional[Date]
    modifiable: bool


EspaceParent.model_rebuild()


# ---------- Paiements : tiers, guichet, contrôle ----------
class Tiers(SQLModel, table=True):
    """Organisme qui prend en charge une part de la scolarité (ONG, entreprise, mairie…)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    nom: str
    contact: Optional[str] = None
    telephone: Optional[str] = None


class TiersSaisie(SQLModel):
    nom: str = Field(min_length=2, max_length=80)
    contact: Optional[str] = Field(default=None, max_length=80)
    telephone: Optional[str] = Field(default=None, max_length=30)


class PriseEnCharge(SQLModel):
    tiers_id: Optional[int] = None           # vide : plus de prise en charge
    pourcentage: int = Field(default=0, ge=0, le=100)


class MoratoireSaisie(SQLModel):
    jusquau: Optional[Date] = None           # vide : annuler le délai
    motif: Optional[str] = Field(default=None, max_length=120)


class LignePanier(SQLModel):
    eleve_id: int
    type_frais_id: Optional[int] = None      # vide : scolarité
    tiers_id: Optional[int] = None           # part d'un tiers
    montant: int = Field(gt=0)


class PaiementGroupe(SQLModel):
    lignes: list[LignePanier] = Field(min_length=1, max_length=40)
    mode: ModePaiement = ModePaiement.especes
    reference: Optional[str] = Field(default=None, max_length=60)
    date_paiement: Optional[Date] = None
    cle_client: Optional[str] = Field(default=None, max_length=64)


class ResultatGroupe(SQLModel):
    groupe: str
    total: int
    paiements: list[Paiement]
    message_parent: Optional[str] = None     # texte prêt pour WhatsApp / SMS
    lien_whatsapp: Optional[str] = None


class ClotureCaisse(SQLModel, table=True):
    """Fin de journée : l'argent liquide compté par le caissier, comparé à ce que le logiciel attend."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    jour: Date = Field(index=True)
    attendu: int                             # espèces attendues dans la caisse
    compte: int                              # espèces comptées
    ecart: int
    verse_banque: int = 0                    # argent déposé à la banque ce jour-là
    commentaire: Optional[str] = None
    par_id: int = Field(foreign_key="utilisateur.id")
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valide_par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    valide_le: Optional[datetime] = None


class ClotureSaisie(SQLModel):
    jour: Optional[Date] = None
    compte: int = Field(ge=0)
    verse_banque: int = Field(default=0, ge=0)
    commentaire: Optional[str] = Field(default=None, max_length=200)


class ClotureDetail(SQLModel):
    id: int
    jour: Date
    attendu: int
    compte: int
    ecart: int
    verse_banque: int
    commentaire: Optional[str]
    par: str
    cree_le: datetime
    valide_par: Optional[str]
    valide_le: Optional[datetime]


class Alerte(SQLModel, table=True):
    """Événement à signaler au directeur (annulation, réduction, écart de caisse…)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    type: str
    message: str
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    lue: bool = False


class Promesse(SQLModel, table=True):
    """Le parent promet de payer à une date : on le relance ce jour-là."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ecole_id: int = Field(foreign_key="ecole.id", index=True)
    eleve_id: int = Field(foreign_key="eleve.id", index=True)
    date_promise: Date = Field(index=True)
    montant: Optional[int] = None
    note: Optional[str] = None
    par_id: Optional[int] = Field(default=None, foreign_key="utilisateur.id")
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    soldee: bool = False


class PromesseSaisie(SQLModel):
    eleve_id: int
    date_promise: Date
    montant: Optional[int] = Field(default=None, gt=0)
    note: Optional[str] = Field(default=None, max_length=200)


class ARelancer(SQLModel):
    eleve_id: int
    eleve: str
    classe: str
    telephone: Optional[str]
    raison: str                              # avant_echeance / retard / promesse
    detail: str
    montant: int
    texte: str
    lien_whatsapp: Optional[str]
    deja_relance: bool = False


class MoisPrevision(SQLModel):
    mois: str
    attendu: int                             # échéances des élèves ce mois-là
    salaires: int
    autres_depenses: int                     # moyenne des mois passés (hors salaires)
    solde_prevu: int                         # trésorerie en fin de mois


class LigneRapprochement(SQLModel):
    statut: str                              # trouve / absent_logiciel / absent_releve / doublon / montant_different
    reference: Optional[str]
    date: Optional[Date]
    montant_releve: Optional[int]
    montant_logiciel: Optional[int]
    eleve: Optional[str] = None
    paiement_id: Optional[int] = None


class Rapprochement(SQLModel):
    lignes: list[LigneRapprochement]
    trouves: int
    absents_logiciel: int
    absents_releve: int
    differences: int
