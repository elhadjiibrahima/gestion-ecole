from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from app.database import get_session
from app.models import (
    Bulletin, Classe, Eleve, EleveCreate, EleveUpdate, LigneBulletin,
    Note, Paiement, Solde,
)

router = APIRouter(prefix="/eleves", tags=["Élèves"])


def _get_eleve(session: Session, eleve_id: int) -> Eleve:
    eleve = session.get(Eleve, eleve_id)
    if not eleve:
        raise HTTPException(404, "Élève introuvable")
    return eleve


def _generer_matricule(session: Session) -> str:
    annee = date.today().year
    n = session.exec(select(func.count()).select_from(Eleve)).one() + 1
    return f"{annee}-{n:04d}"


@router.post("", response_model=Eleve, status_code=201)
def inscrire_eleve(data: EleveCreate, session: Session = Depends(get_session)):
    if not session.get(Classe, data.classe_id):
        raise HTTPException(400, "Classe introuvable")
    eleve = Eleve.model_validate(data, update={"matricule": _generer_matricule(session)})
    session.add(eleve)
    session.commit()
    session.refresh(eleve)
    return eleve


@router.get("", response_model=list[Eleve])
def lister_eleves(q: str | None = None, session: Session = Depends(get_session)):
    """Liste les élèves. `q` filtre par nom, prénom ou matricule."""
    stmt = select(Eleve)
    if q:
        motif = f"%{q}%"
        stmt = stmt.where(
            Eleve.nom.ilike(motif) | Eleve.prenom.ilike(motif) | Eleve.matricule.ilike(motif)
        )
    return session.exec(stmt.order_by(Eleve.nom)).all()


@router.get("/{eleve_id}", response_model=Eleve)
def voir_eleve(eleve_id: int, session: Session = Depends(get_session)):
    return _get_eleve(session, eleve_id)


@router.patch("/{eleve_id}", response_model=Eleve)
def modifier_eleve(eleve_id: int, data: EleveUpdate, session: Session = Depends(get_session)):
    eleve = _get_eleve(session, eleve_id)
    changes = data.model_dump(exclude_unset=True)
    if "classe_id" in changes and not session.get(Classe, changes["classe_id"]):
        raise HTTPException(400, "Classe introuvable")
    eleve.sqlmodel_update(changes)
    session.add(eleve)
    session.commit()
    session.refresh(eleve)
    return eleve


@router.get("/{eleve_id}/solde", response_model=Solde)
def solde_eleve(eleve_id: int, session: Session = Depends(get_session)):
    eleve = _get_eleve(session, eleve_id)
    return calculer_solde(session, eleve)


def calculer_solde(session: Session, eleve: Eleve) -> Solde:
    classe = session.get(Classe, eleve.classe_id)
    total = session.exec(
        select(func.coalesce(func.sum(Paiement.montant), 0)).where(Paiement.eleve_id == eleve.id)
    ).one()
    return Solde(
        eleve_id=eleve.id,
        nom_complet=f"{eleve.prenom} {eleve.nom}",
        classe=classe.nom,
        frais_annuels=classe.frais_annuels,
        total_paye=total,
        reste_a_payer=max(classe.frais_annuels - total, 0),
    )


@router.get("/{eleve_id}/bulletin", response_model=Bulletin)
def bulletin(eleve_id: int, trimestre: int, session: Session = Depends(get_session)):
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    eleve = _get_eleve(session, eleve_id)
    classe = session.get(Classe, eleve.classe_id)
    notes = session.exec(
        select(Note).where(Note.eleve_id == eleve_id, Note.trimestre == trimestre)
    ).all()

    # Moyenne par matière, puis moyenne générale pondérée par coefficient
    par_matiere: dict[str, list[Note]] = {}
    for n in notes:
        par_matiere.setdefault(n.matiere, []).append(n)

    lignes = []
    for matiere, ns in sorted(par_matiere.items()):
        moy = sum(n.valeur for n in ns) / len(ns)
        coef = max(n.coefficient for n in ns)
        lignes.append(LigneBulletin(matiere=matiere, moyenne=round(moy, 2), coefficient=coef))

    total_coef = sum(l.coefficient for l in lignes)
    moyenne_generale = (
        round(sum(l.moyenne * l.coefficient for l in lignes) / total_coef, 2) if total_coef else None
    )
    return Bulletin(
        eleve_id=eleve.id,
        nom_complet=f"{eleve.prenom} {eleve.nom}",
        classe=classe.nom,
        trimestre=trimestre,
        matieres=lignes,
        moyenne_generale=moyenne_generale,
    )
