"""Passage à une nouvelle année scolaire.

Les élèves montent en classe supérieure (sauf redoublants), les élèves de fin de cycle quittent
l'école. Les frais repartent de zéro car seuls les paiements de l'année en cours comptent ;
paiements, notes et absences des années passées restent enregistrés.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from app.database import get_session
from app.models import Classe, Ecole, Eleve, NouvelleAnnee, ResultatNouvelleAnnee, Role, Utilisateur
from app.security import exiger_role
from app.services import get_classe

router = APIRouter(prefix="/annee", tags=["Année scolaire"])


class PropositionPassage(SQLModel):
    classe_id: int
    classe: str
    nb_eleves: int
    vers_classe_id: int | None


class Proposition(SQLModel):
    annee_actuelle: str
    annee_suivante: str
    passages: list[PropositionPassage]


def _annee_suivante(annee: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{4})", annee or "")
    return f"{int(m.group(1)) + 1}-{int(m.group(2)) + 1}" if m else ""


def _decouper(nom: str) -> tuple[int, str] | None:
    """« 6eme A » → (6, "eme a") ; sert à trouver la classe suivante."""
    m = re.search(r"\d+", nom)
    if not m:
        return None
    reste = (nom[:m.start()] + "#" + nom[m.end():]).lower().replace("è", "e").replace("ème", "eme").strip()
    return int(m.group()), re.sub(r"\s+", " ", reste)


def proposer_passages(classes: list[Classe]) -> dict[int, int | None]:
    """Devine la classe suivante de chaque classe d'après son nom.
    Collège français (6e→3e) : on descend ; sinon (7e→10e, CP1→CM2…) : on monte."""
    decoupe = {c.id: _decouper(c.nom) for c in classes}
    nombres = {d[0] for d in decoupe.values() if d}
    descendant = bool(nombres) and min(nombres) >= 3 and max(nombres) <= 6
    index = {d: cid for cid, d in decoupe.items() if d}
    suite = {}
    for c in classes:
        d = decoupe[c.id]
        suite[c.id] = index.get((d[0] - 1 if descendant else d[0] + 1, d[1])) if d else None
    return suite


@router.get("/proposition", response_model=Proposition)
def proposition(user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    ecole = session.get(Ecole, user.ecole_id)
    classes = session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id).order_by(Classe.nom)).all()
    suite = proposer_passages(classes)
    effectifs = {c.id: len(session.exec(select(Eleve.id).where(
        Eleve.classe_id == c.id, Eleve.actif == True)).all()) for c in classes}  # noqa: E712
    return Proposition(
        annee_actuelle=ecole.annee_scolaire, annee_suivante=_annee_suivante(ecole.annee_scolaire),
        passages=[PropositionPassage(classe_id=c.id, classe=c.nom, nb_eleves=effectifs[c.id],
                                     vers_classe_id=suite[c.id]) for c in classes],
    )


@router.post("/nouvelle", response_model=ResultatNouvelleAnnee)
def nouvelle_annee(
    data: NouvelleAnnee,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    ecole = session.get(Ecole, user.ecole_id)
    m = re.fullmatch(r"(\d{4})-(\d{4})", data.nouvelle_annee.strip())
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        raise HTTPException(400, "Année scolaire invalide (format attendu : 2027-2028)")
    if data.nouvelle_annee == ecole.annee_scolaire:
        raise HTTPException(400, "C'est déjà l'année en cours")
    if not data.passages:
        raise HTTPException(400, "Aucune classe indiquée")
    vues = set()
    # 1) On prépare tout à partir de la situation actuelle (pas de « double montée »)
    mouvements: list[tuple[Eleve, int | None]] = []
    redoublants = 0
    for p in data.passages:
        if p.classe_id in vues:
            raise HTTPException(400, "Une classe apparaît deux fois")
        vues.add(p.classe_id)
        get_classe(session, p.classe_id, user.ecole_id, code=400)
        if p.vers_classe_id is not None:
            get_classe(session, p.vers_classe_id, user.ecole_id, code=400)
        eleves = session.exec(select(Eleve).where(Eleve.classe_id == p.classe_id, Eleve.actif == True)).all()  # noqa: E712
        ids = {e.id for e in eleves}
        if set(p.redoublants) - ids:
            raise HTTPException(400, "Un redoublant indiqué n'est pas dans sa classe")
        for e in eleves:
            if e.id in p.redoublants:
                redoublants += 1
            else:
                mouvements.append((e, p.vers_classe_id))
    # 2) On applique
    passes = sortants = 0
    for e, cible in mouvements:
        if cible is None:
            e.actif = False
            e.annee_sortie = ecole.annee_scolaire
            sortants += 1
        else:
            e.classe_id = cible
            passes += 1
        session.add(e)
    ecole.annee_scolaire = data.nouvelle_annee
    session.add(ecole)
    session.commit()
    return ResultatNouvelleAnnee(annee_scolaire=data.nouvelle_annee, passes=passes,
                                 redoublants=redoublants, sortants=sortants)
