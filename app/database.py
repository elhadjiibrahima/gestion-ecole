from datetime import date, timedelta

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine

from app.config import DATABASE_URL, ESSAI_JOURS

# check_same_thread=False : nécessaire pour SQLite avec FastAPI
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)   # crée les tables qui n'existent pas
    migrer(engine)


def migrer(moteur) -> None:
    """Met à jour une base existante sans perdre les données.

    - ajoute les colonnes apparues dans une nouvelle version du logiciel ;
    - rattache les anciens paiements / notes à l'année scolaire en cours.
    """
    inspecteur = inspect(moteur)
    tables_existantes = set(inspecteur.get_table_names())
    ajoutees: set[tuple[str, str]] = set()
    with moteur.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in tables_existantes:
                continue
            colonnes = {c["name"] for c in inspecteur.get_columns(table.name)}
            for col in table.columns:
                if col.name in colonnes:
                    continue
                type_sql = col.type.compile(dialect=moteur.dialect)
                defaut = ""
                valeur = getattr(col.default, "arg", None)
                if valeur is not None and not callable(valeur):
                    if isinstance(valeur, bool):
                        valeur = int(valeur)
                    elif hasattr(valeur, "value"):      # Enum
                        valeur = valeur.value
                    defaut = f" DEFAULT {valeur!r}" if isinstance(valeur, str) else f" DEFAULT {valeur}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {type_sql}{defaut}'))
                ajoutees.add((table.name, col.name))

        # Cycles créés avant les règles d'enseignement : maître par classe au primaire, 2 / 1 matières au collège / lycée
        if ("cycle", "enseignement") in ajoutees:
            conn.execute(text("UPDATE cycle SET enseignement = 'classe' WHERE code IN ('prescolaire', 'primaire')"))
            conn.execute(text("UPDATE cycle SET max_matieres = 2 WHERE code = 'college'"))
            conn.execute(text("UPDATE cycle SET max_matieres = 1 WHERE code = 'lycee'"))
        # Écoles qui avaient déjà un échéancier par tranches : on le garde (les autres passent au paiement mensuel)
        if ("ecole", "mode_paiement") in ajoutees and "tranche" in tables_existantes:
            conn.execute(text("UPDATE ecole SET mode_paiement = 'tranches' "
                              "WHERE id IN (SELECT DISTINCT ecole_id FROM tranche)"))
        # Données d'avant l'année scolaire : on les rattache à l'année en cours de l'école
        conn.execute(text("""
            UPDATE paiement SET annee_scolaire = (
                SELECT ecole.annee_scolaire FROM eleve JOIN ecole ON ecole.id = eleve.ecole_id
                WHERE eleve.id = paiement.eleve_id)
            WHERE annee_scolaire IS NULL"""))
        conn.execute(text("""
            UPDATE note SET annee_scolaire = (
                SELECT ecole.annee_scolaire FROM eleve JOIN ecole ON ecole.id = eleve.ecole_id
                WHERE eleve.id = note.eleve_id)
            WHERE annee_scolaire IS NULL"""))
        # Écoles créées avant les abonnements : période d'essai à partir d'aujourd'hui
        conn.execute(text("UPDATE ecole SET abonnement_fin = :fin WHERE abonnement_fin IS NULL"),
                     {"fin": date.today() + timedelta(days=ESSAI_JOURS)})
        conn.execute(text("""
            UPDATE notehistorique SET annee_scolaire = (
                SELECT annee_scolaire FROM ecole WHERE ecole.id = notehistorique.ecole_id)
            WHERE annee_scolaire IS NULL"""))


def get_session():
    with Session(engine) as session:
        yield session
