"""Organisation d'un complexe scolaire : cycles, classes types, matières, passage d'une classe à la suivante.

Modèle sénégalais par défaut (préscolaire, élémentaire CI→CM2, moyen 6e→3e, secondaire 2nde→Terminale),
et variante guinéenne (CP1→CM2, 7e→10e, 11e→Terminale).
"""
import re
import unicodedata
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import Classe, Cycle, Ecole, Matiere, MatiereCycle

PAYS = {
    "senegal": {"devise": "FCFA", "indicatif": "221",
                "republique": "RÉPUBLIQUE DU SÉNÉGAL", "devise_nationale": "Un Peuple – Un But – Une Foi",
                "ministere": "Ministère de l'Éducation nationale"},
    "guinee": {"devise": "GNF", "indicatif": "224",
               "republique": "RÉPUBLIQUE DE GUINÉE", "devise_nationale": "Travail – Justice – Solidarité",
               "ministere": "Ministère de l'Enseignement Pré-Universitaire et de l'Alphabétisation"},
    "autre": {"devise": "FCFA", "indicatif": "221", "republique": "", "devise_nationale": "", "ministere": ""},
}

# Cycles : nom affiché, périodes (2 = semestres), barème, moyenne de passage
# Organisation des enseignants : un maître par classe au préscolaire et au primaire ; au collège un professeur
# enseigne au plus 2 matières (maths-PC, français-HG…) ; au lycée une seule.
ENSEIGNEMENT = {"prescolaire": ("classe", None), "primaire": ("classe", None), "college": ("matiere", 2),
                "lycee": ("matiere", 1)}
CYCLES = {
    "senegal": [("prescolaire", "Préscolaire", 3, 10, 5), ("primaire", "Primaire", 3, 10, 5),
                ("college", "Collège", 2, 20, 10), ("lycee", "Lycée", 2, 20, 10)],
    "guinee": [("prescolaire", "Préscolaire", 3, 10, 5), ("primaire", "Primaire", 3, 10, 5),
               ("college", "Collège", 3, 20, 10), ("lycee", "Lycée", 3, 20, 10)],
}
CYCLES["autre"] = CYCLES["senegal"]

CLASSES = {
    "senegal": {
        "prescolaire": ["Petite section", "Moyenne section", "Grande section"],
        "primaire": ["CI", "CP", "CE1", "CE2", "CM1", "CM2"],
        "college": ["6ème", "5ème", "4ème", "3ème"],
        "lycee": ["2nde L", "2nde S", "1ère L", "1ère S", "Terminale L", "Terminale S"],
    },
    "guinee": {
        "prescolaire": ["Petite section", "Moyenne section", "Grande section"],
        "primaire": ["CP1", "CP2", "CE1", "CE2", "CM1", "CM2"],
        "college": ["7ème", "8ème", "9ème", "10ème"],
        "lycee": ["11ème", "12ème SM", "12ème SE", "12ème SS", "Terminale SM", "Terminale SE", "Terminale SS"],
    },
}
CLASSES["autre"] = CLASSES["senegal"]

# Matières (nom, coefficient) par cycle. Les coefficients sont un point de départ, modifiables.
MATIERES = {
    "prescolaire": [("Langage", 1), ("Pré-lecture", 1), ("Pré-mathématiques", 1), ("Éveil", 1),
                    ("Activités artistiques", 1)],
    "primaire": [("Lecture", 1), ("Expression écrite", 1), ("Dictée", 1), ("Grammaire-Conjugaison", 1),
                 ("Mathématiques", 1), ("Histoire-Géographie", 1), ("Sciences d'observation", 1),
                 ("Éducation civique", 1), ("Anglais", 1), ("Arabe", 1), ("EPS", 1)],
    "college": [("Français", 4), ("Mathématiques", 4), ("Anglais", 2), ("Espagnol / Arabe (LV2)", 2),
                ("Histoire-Géographie", 2), ("SVT", 2), ("Physique-Chimie", 2), ("Éducation civique", 1),
                ("EPS", 1)],
    "lycee": [("Français", 3), ("Philosophie", 2), ("Mathématiques", 4), ("Physique-Chimie", 4), ("SVT", 3),
              ("Anglais", 2), ("Espagnol / Arabe (LV2)", 2), ("Histoire-Géographie", 2), ("EPS", 1)],
}
# Coefficients propres aux séries du lycée (séries littéraires / scientifiques)
SERIES = {
    "litteraire": {"Français": 5, "Philosophie": 4, "Histoire-Géographie": 4, "Anglais": 3,
                   "Espagnol / Arabe (LV2)": 3, "Mathématiques": 2, "Physique-Chimie": 2, "SVT": 2},
    "scientifique": {"Mathématiques": 5, "Physique-Chimie": 5, "SVT": 4, "Français": 3, "Philosophie": 2},
}


def serie_de(nom: str) -> str | None:
    s = normaliser(nom).split()
    if not s:
        return None
    fin = s[-1]
    if fin in ("l", "l1", "l2", "ss", "a"):
        return "litteraire"
    if fin in ("s", "s1", "s2", "s3", "sm", "se", "c", "d"):
        return "scientifique"
    return None


def normaliser(texte: str) -> str:
    t = unicodedata.normalize("NFKD", (texte or "").lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


# ---------- Règles d'une classe ----------
@dataclass
class Regles:
    periodes: int = 3
    bareme: int = 20
    seuil: float = 10
    cycle: Cycle | None = None

    @property
    def nom_periode(self) -> str:
        return "Semestre" if self.periodes == 2 else "Trimestre"


def cycles_ecole(session: Session, ecole_id: int) -> list[Cycle]:
    return session.exec(select(Cycle).where(Cycle.ecole_id == ecole_id).order_by(Cycle.ordre, Cycle.id)).all()


def cycle_de(session: Session, classe: Classe, cycles: list[Cycle] | None = None) -> Cycle | None:
    cycles = cycles if cycles is not None else cycles_ecole(session, classe.ecole_id)
    n = normaliser(classe.niveau)
    return next((c for c in cycles if normaliser(c.nom) == n or (c.code and c.code == n)), None)


def cle_niveau(session: Session, ecole_id: int):
    """Clé de tri des classes : place de leur niveau dans le parcours, puis ordre naturel des classes."""
    from app.services import cle_classe
    rangs = {}
    for c in cycles_ecole(session, ecole_id):
        rangs[normaliser(c.nom)] = c.ordre
        if c.code:
            rangs[c.code] = c.ordre
    return lambda classe: (rangs.get(normaliser(classe.niveau), 999), cle_classe(classe.nom))


def regles_classe(session: Session, classe: Classe) -> Regles:
    c = cycle_de(session, classe)
    if c:
        return Regles(periodes=c.periodes, bareme=c.bareme, seuil=c.seuil, cycle=c)
    ecole = session.get(Ecole, classe.ecole_id)
    return Regles(seuil=ecole.seuil_admission)


# ---------- Création d'un complexe ----------
def creer_cursus(session: Session, ecole: Ecole, codes: list[str], classes: bool = True) -> int:
    """Crée les cycles demandés, leurs matières (avec coefficients) et leurs classes. Sans commit.
    Renvoie le nombre de classes créées. Ce qui existe déjà n'est pas recréé."""
    pays = ecole.pays if ecole.pays in CLASSES else "senegal"
    existants = {c.code: c for c in cycles_ecole(session, ecole.id) if c.code}
    matieres = {normaliser(m.nom): m for m in session.exec(select(Matiere).where(Matiere.ecole_id == ecole.id)).all()}
    noms_classes = {normaliser(c.nom) for c in session.exec(select(Classe).where(Classe.ecole_id == ecole.id)).all()}
    crees = 0
    for ordre, (code, nom, periodes, bareme, seuil) in enumerate(CYCLES[pays]):
        if code not in codes:
            continue
        cycle = existants.get(code)
        if cycle is None:
            cycle = Cycle(ecole_id=ecole.id, nom=nom, code=code, ordre=ordre, periodes=periodes, bareme=bareme, seuil=seuil,
                          enseignement=ENSEIGNEMENT[code][0], max_matieres=ENSEIGNEMENT[code][1])
            session.add(cycle)
            session.flush()
            for i, (mnom, coef) in enumerate(MATIERES[code]):
                m = matieres.get(normaliser(mnom))
                if m is None:
                    m = Matiere(ecole_id=ecole.id, nom=mnom, coefficient=coef, ordre=len(matieres) + i)
                    session.add(m)
                    session.flush()
                    matieres[normaliser(mnom)] = m
                session.add(MatiereCycle(matiere_id=m.id, cycle_id=cycle.id, coefficient=coef))
        if classes:
            for cnom in CLASSES[pays][code]:
                if normaliser(cnom) not in noms_classes:
                    session.add(Classe(ecole_id=ecole.id, nom=cnom, niveau=cycle.nom))
                    noms_classes.add(normaliser(cnom))
                    crees += 1
    session.flush()
    _coefficients_series(session, ecole.id)
    return crees


def _coefficients_series(session: Session, ecole_id: int) -> None:
    """Classes de lycée L / S : coefficients de la série (si la classe n'en a pas déjà)."""
    from app.models import CoefficientClasse
    matieres = {m.nom: m for m in session.exec(select(Matiere).where(Matiere.ecole_id == ecole_id)).all()}
    for c in session.exec(select(Classe).where(Classe.ecole_id == ecole_id)).all():
        serie = serie_de(c.nom) if normaliser(c.niveau) in ("lycee",) else None
        if not serie:
            continue
        deja = {x.matiere_id for x in session.exec(select(CoefficientClasse).where(CoefficientClasse.classe_id == c.id)).all()}
        for mnom, coef in SERIES[serie].items():
            m = matieres.get(mnom)
            if m and m.id not in deja:
                session.add(CoefficientClasse(matiere_id=m.id, classe_id=c.id, coefficient=coef))


def classes_types(session: Session, ecole: Ecole) -> list[tuple[str, str, bool]]:
    """(nom, niveau, existe déjà) des classes types de chaque cycle de l'école."""
    pays = ecole.pays if ecole.pays in CLASSES else "senegal"
    cycles = {c.code: c for c in cycles_ecole(session, ecole.id) if c.code}
    noms = {normaliser(c.nom) for c in session.exec(select(Classe).where(Classe.ecole_id == ecole.id)).all()}
    sortie = []
    for code, nom, *_ in CYCLES[pays]:
        niveau = cycles[code].nom if code in cycles else nom
        for cnom in CLASSES[pays][code]:
            t = normaliser(cnom)       # « 6ème » existe aussi si l'école a « 6ème A », « 6ème B »…
            sortie.append((cnom, niveau, any(n == t or re.fullmatch(re.escape(t) + r" [a-z0-9]{1,2}", n) for n in noms)))
    return sortie


# ---------- Passage dans la classe suivante ----------
_ORDRE_PRIMAIRE = [("petite section", 0), ("moyenne section", 1), ("grande section", 2), ("ci", 3),
                   ("cp1", 4), ("cp2", 5), ("cp", 4.5), ("ce1", 6), ("ce2", 7), ("cm1", 8), ("cm2", 9)]


def rang_classe(nom: str) -> tuple[float, str] | None:
    """Place de la classe dans le parcours, et ce qui la distingue (lettre, série)."""
    n = normaliser(nom)
    for motif, rang in _ORDRE_PRIMAIRE:
        if n == motif or n.startswith(motif + " "):
            return rang, n[len(motif):].strip()
    m = re.match(r"(terminale|tle)\b(.*)", n)
    if m:
        return 16, m.group(2).strip()
    m = re.match(r"(2nde|2de|seconde)\b(.*)", n)
    if m:
        return 14, m.group(2).strip()
    m = re.match(r"(1ere|1re|premiere)\b(.*)", n)
    if m:
        return 15, m.group(2).strip()
    m = re.match(r"(\d+)\s*(eme|e|de|nde)?\b(.*)", n)
    if m:
        k = int(m.group(1))
        reste = m.group(3).strip()
        if k in (11, 12):
            return 14 + (k - 11), reste
        if 3 <= k <= 6:                       # collège français / sénégalais : 6ème → 3ème
            return 10 + (6 - k), reste
        if 7 <= k <= 10:                      # collège guinéen : 7ème → 10ème
            return 10 + (k - 7), reste
    return None


def proposer_suite(classes: list[Classe]) -> dict[int, tuple[int | None, bool]]:
    """Classe suivante de chaque classe : (id, à vérifier). On monte vers le niveau existant le plus proche
    (CM2 → 6ème dans un complexe) ; parmi plusieurs classes de ce niveau, on garde la même lettre ou série."""
    rangs = {c.id: rang_classe(c.nom) for c in classes}
    suite = {}
    for c in classes:
        r = rangs[c.id]
        if r is None:
            suite[c.id] = (None, True)
            continue
        plus_haut = sorted({x[0] for x in rangs.values() if x and x[0] > r[0]})
        if not plus_haut:
            suite[c.id] = (None, False)       # dernière classe : fin de cycle
            continue
        cibles = [x for x in classes if rangs[x.id] and rangs[x.id][0] == plus_haut[0]]
        meme = [x for x in cibles if rangs[x.id][1] == r[1]]
        if meme:
            suite[c.id] = (meme[0].id, False)
        else:
            suite[c.id] = (sorted(cibles, key=lambda x: normaliser(x.nom))[0].id, len(cibles) > 1)
    return suite
