from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from app.database import get_session
from app.models import (
    Absence, AbsenceDetail, AbsenceUpdate, Appel, Classe, Eleve, ResumeAbsences, TypeAbsence, Utilisateur,
)
from app.security import APPEL, JUSTIFICATION, classes_autorisees, exiger_role
from app.services import annee_courante, get_classe

router = APIRouter(prefix="/absences", tags=["Absences"])


class EtatAppel(SQLModel):
    absents: list[int]
    retards: list[int]


def _verifier_classe(session: Session, user: Utilisateur, classe_id: int) -> None:
    autorisees = classes_autorisees(session, user)
    if autorisees is not None and classe_id not in autorisees:
        raise HTTPException(403, "Cette classe ne t'est pas attribuée")


def _eleves_classe(session: Session, classe: Classe) -> list[Eleve]:
    return session.exec(
        select(Eleve).where(Eleve.classe_id == classe.id, Eleve.actif == True)  # noqa: E712
        .order_by(Eleve.nom, Eleve.prenom)
    ).all()


def _absences_du_jour(session: Session, ids: list[int], jour: date) -> dict[int, Absence]:
    if not ids:
        return {}
    return {a.eleve_id: a for a in session.exec(
        select(Absence).where(Absence.eleve_id.in_(ids), Absence.jour == jour)).all()}


@router.get("/appel", response_model=EtatAppel)
def etat_appel(
    classe_id: int,
    jour: date,
    user: Utilisateur = Depends(exiger_role(*APPEL)),
    session: Session = Depends(get_session),
):
    """Ce qui a déjà été saisi pour cette classe ce jour-là (pour reprendre l'appel)."""
    classe = get_classe(session, classe_id, user.ecole_id)
    _verifier_classe(session, user, classe.id)
    jour_abs = _absences_du_jour(session, [e.id for e in _eleves_classe(session, classe)], jour)
    return EtatAppel(
        absents=[i for i, a in jour_abs.items() if a.type == TypeAbsence.absence],
        retards=[i for i, a in jour_abs.items() if a.type == TypeAbsence.retard],
    )


@router.post("/appel", response_model=EtatAppel)
def faire_appel(
    data: Appel,
    user: Utilisateur = Depends(exiger_role(*APPEL)),
    session: Session = Depends(get_session),
):
    """Enregistre l'appel d'une classe pour un jour. Refaire l'appel du même jour le remplace
    (une absence déjà justifiée garde sa justification)."""
    if data.jour > date.today():
        raise HTTPException(400, "On ne peut pas faire l'appel d'un jour futur")
    classe = get_classe(session, data.classe_id, user.ecole_id, code=400)
    _verifier_classe(session, user, classe.id)
    ids = {e.id for e in _eleves_classe(session, classe)}
    inconnus = (set(data.absents) | set(data.retards)) - ids
    if inconnus:
        raise HTTPException(400, "Certains élèves ne sont pas dans cette classe")
    voulu = {i: TypeAbsence.retard for i in data.retards}
    voulu.update({i: TypeAbsence.absence for i in data.absents})   # absent l'emporte sur retard
    annee = annee_courante(session, user.ecole_id)
    existantes = _absences_du_jour(session, list(ids), data.jour)
    for eleve_id in ids:
        a, type_ = existantes.get(eleve_id), voulu.get(eleve_id)
        if a and type_ is None:
            session.delete(a)
        elif a and a.type != type_:
            a.type, a.justifiee, a.motif, a.trimestre, a.saisi_par_id = type_, False, None, data.trimestre, user.id
            session.add(a)
        elif a:
            a.trimestre = data.trimestre
            session.add(a)
        elif type_:
            session.add(Absence(ecole_id=user.ecole_id, eleve_id=eleve_id, jour=data.jour, type=type_,
                                trimestre=data.trimestre, saisi_par_id=user.id, annee_scolaire=annee))
    session.commit()
    return etat_appel(classe.id, data.jour, user, session)


@router.get("", response_model=list[AbsenceDetail])
def lister(
    classe_id: int | None = None,
    eleve_id: int | None = None,
    trimestre: int | None = None,
    non_justifiees: bool = False,
    limite: int = 300,
    user: Utilisateur = Depends(exiger_role(*APPEL)),
    session: Session = Depends(get_session),
):
    stmt = (select(Absence, Eleve, Classe)
            .join(Eleve, Absence.eleve_id == Eleve.id).join(Classe, Eleve.classe_id == Classe.id)
            .where(Absence.ecole_id == user.ecole_id,
                   Absence.annee_scolaire == annee_courante(session, user.ecole_id)))
    if classe_id:
        _verifier_classe(session, user, classe_id)
        stmt = stmt.where(Eleve.classe_id == classe_id)
    autorisees = classes_autorisees(session, user)
    if autorisees is not None:          # enseignant : seulement ses classes
        stmt = stmt.where(Eleve.classe_id.in_(autorisees or {-1}))
    if eleve_id:
        stmt = stmt.where(Absence.eleve_id == eleve_id)
    if trimestre:
        stmt = stmt.where(Absence.trimestre == trimestre)
    if non_justifiees:
        stmt = stmt.where(Absence.justifiee == False)  # noqa: E712
    lignes = session.exec(stmt.order_by(Absence.jour.desc(), Eleve.nom).limit(min(max(limite, 1), 2000))).all()
    ids = {a.saisi_par_id for a, _, _ in lignes} - {None}
    noms = {u.id: u.nom for u in session.exec(select(Utilisateur).where(Utilisateur.id.in_(ids)))} if ids else {}
    return [
        AbsenceDetail(id=a.id, eleve_id=e.id, eleve=f"{e.prenom} {e.nom}", classe=c.nom, jour=a.jour,
                      type=a.type, trimestre=a.trimestre, justifiee=a.justifiee, motif=a.motif,
                      saisi_par=noms.get(a.saisi_par_id))
        for a, e, c in lignes
    ]


@router.get("/resume", response_model=list[ResumeAbsences])
def resume(
    classe_id: int,
    trimestre: int | None = None,
    user: Utilisateur = Depends(exiger_role(*APPEL)),
    session: Session = Depends(get_session),
):
    """Total par élève de la classe : absences (dont justifiées) et retards."""
    classe = get_classe(session, classe_id, user.ecole_id)
    _verifier_classe(session, user, classe.id)
    eleves = _eleves_classe(session, classe)
    stmt = select(Absence).where(Absence.eleve_id.in_([e.id for e in eleves]),
                                 Absence.annee_scolaire == annee_courante(session, user.ecole_id))
    if trimestre:
        stmt = stmt.where(Absence.trimestre == trimestre)
    absences = session.exec(stmt).all() if eleves else []
    res = []
    for e in eleves:
        a_e = [a for a in absences if a.eleve_id == e.id]
        res.append(ResumeAbsences(
            eleve_id=e.id, eleve=f"{e.prenom} {e.nom}",
            absences=sum(a.type == TypeAbsence.absence for a in a_e),
            absences_justifiees=sum(a.type == TypeAbsence.absence and a.justifiee for a in a_e),
            retards=sum(a.type == TypeAbsence.retard for a in a_e),
        ))
    return res


def _get(session: Session, absence_id: int, ecole_id: int) -> Absence:
    a = session.get(Absence, absence_id)
    if not a or a.ecole_id != ecole_id:
        raise HTTPException(404, "Absence introuvable")
    return a


@router.patch("/{absence_id}", response_model=Absence)
def justifier(
    absence_id: int,
    data: AbsenceUpdate,
    user: Utilisateur = Depends(exiger_role(*JUSTIFICATION)),
    session: Session = Depends(get_session),
):
    a = _get(session, absence_id, user.ecole_id)
    a.sqlmodel_update(data.model_dump(exclude_unset=True))
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@router.delete("/{absence_id}", status_code=204)
def supprimer(
    absence_id: int,
    user: Utilisateur = Depends(exiger_role(*JUSTIFICATION)),
    session: Session = Depends(get_session),
):
    session.delete(_get(session, absence_id, user.ecole_id))
    session.commit()
