from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    CaseNote, Classe, Eleve, LigneHistorique, Note, NoteCreate, NoteHistorique, Role, SaisieNote,
    Utilisateur,
)
from app.security import PEDAGOGIE, TOUS, exiger_role
from app.services import annee_courante, get_eleve, matieres_classe

router = APIRouter(prefix="/notes", tags=["Notes"])


def _tracer(session: Session, user: Utilisateur, eleve: Eleve, matiere: str, trimestre: int,
            ancienne: float | None, nouvelle: float | None) -> None:
    """Garde une trace de chaque changement de note."""
    if ancienne == nouvelle:
        return
    session.add(NoteHistorique(
        ecole_id=eleve.ecole_id, eleve_id=eleve.id, utilisateur_id=user.id, matiere=matiere,
        trimestre=trimestre, ancienne_valeur=ancienne, nouvelle_valeur=nouvelle,
        annee_scolaire=annee_courante(session, eleve.ecole_id),
    ))


@router.post("", response_model=Note, status_code=201)
def ajouter_note(
    data: NoteCreate,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    eleve = get_eleve(session, data.eleve_id, user.ecole_id, code=400)
    note = Note.model_validate(data, update={"annee_scolaire": annee_courante(session, user.ecole_id)})
    session.add(note)
    _tracer(session, user, eleve, note.matiere, note.trimestre, None, note.valeur)
    session.commit()
    session.refresh(note)
    return note


@router.get("", response_model=list[Note])
def notes_eleve(
    eleve_id: int,
    trimestre: int | None = None,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    get_eleve(session, eleve_id, user.ecole_id)
    stmt = select(Note).where(Note.eleve_id == eleve_id, Note.annee_scolaire == annee_courante(session, user.ecole_id))
    if trimestre:
        stmt = stmt.where(Note.trimestre == trimestre)
    return session.exec(stmt.order_by(Note.matiere, Note.id)).all()


def _verifier_trimestre(trimestre: int) -> None:
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")


def _grille(session: Session, eleve: Eleve, trimestre: int) -> list[CaseNote]:
    notes = session.exec(
        select(Note).where(Note.eleve_id == eleve.id, Note.trimestre == trimestre,
                           Note.annee_scolaire == annee_courante(session, eleve.ecole_id)).order_by(Note.id)
    ).all()
    par_matiere: dict[str, list[Note]] = {}
    for n in notes:
        par_matiere.setdefault(n.matiere.lower(), []).append(n)

    def valeur(ns):  # plusieurs anciennes notes dans la même matière : on affiche leur moyenne
        return round(sum(n.valeur for n in ns) / len(ns), 2) if ns else None

    # Dernière modification de chaque case (qui, quand)
    traces = session.exec(
        select(NoteHistorique, Utilisateur.nom)
        .join(Utilisateur, NoteHistorique.utilisateur_id == Utilisateur.id)
        .where(NoteHistorique.eleve_id == eleve.id, NoteHistorique.trimestre == trimestre,
               NoteHistorique.annee_scolaire == annee_courante(session, eleve.ecole_id))
        .order_by(NoteHistorique.date, NoteHistorique.id)
    ).all()
    derniere = {h.matiere.lower(): (nom, h.date) for h, nom in traces}

    def case(**kw):
        par, le = derniere.get(kw["matiere"].lower(), (None, None))
        if kw.get("valeur") is None:
            par, le = None, None
        return CaseNote(**kw, saisi_par=par, saisi_le=le)

    cases = []
    for m, coef in matieres_classe(session, session.get(Classe, eleve.classe_id)):
        cases.append(case(matiere_id=m.id, matiere=m.nom, coefficient=coef,
                          valeur=valeur(par_matiere.pop(m.nom.lower(), []))))
    for ns in par_matiere.values():  # notes d'une matière qui n'est plus dans la liste
        cases.append(case(matiere=ns[0].matiere, coefficient=ns[0].coefficient, valeur=valeur(ns)))
    return cases


@router.get("/grille", response_model=list[CaseNote])
def grille(
    eleve_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Une case par matière, avec la note de l'élève pour le trimestre (ou vide)."""
    _verifier_trimestre(trimestre)
    return _grille(session, get_eleve(session, eleve_id, user.ecole_id), trimestre)


@router.put("/grille", response_model=list[CaseNote])
def enregistrer_grille(
    eleve_id: int,
    trimestre: int,
    saisies: list[SaisieNote],
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    """Enregistre la grille : une note par matière. Une case vide efface la note."""
    _verifier_trimestre(trimestre)
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    annee = annee_courante(session, user.ecole_id)
    matieres = {m.id: (m, coef) for m, coef in matieres_classe(session, session.get(Classe, eleve.classe_id))}
    for s in saisies:
        if s.matiere_id not in matieres:
            raise HTTPException(400, "Matière introuvable")
        m, coef = matieres[s.matiere_id]
        anciennes = session.exec(
            select(Note).where(Note.eleve_id == eleve.id, Note.trimestre == trimestre, Note.matiere == m.nom,
                               Note.annee_scolaire == annee)
        ).all()
        ancienne = round(sum(n.valeur for n in anciennes) / len(anciennes), 2) if anciennes else None
        if len(anciennes) <= 1 and ancienne == s.valeur:
            continue  # case inchangée : on ne touche à rien
        for n in anciennes:
            session.delete(n)
        if s.valeur is not None:
            session.add(Note(eleve_id=eleve.id, matiere=m.nom, coefficient=coef,
                             valeur=s.valeur, trimestre=trimestre, annee_scolaire=annee))
        _tracer(session, user, eleve, m.nom, trimestre, ancienne, s.valeur)
    session.commit()
    return _grille(session, eleve, trimestre)


@router.delete("/{note_id}", status_code=204)
def supprimer_note(
    note_id: int,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    note = session.get(Note, note_id)
    eleve = session.get(Eleve, note.eleve_id) if note else None
    if not eleve or eleve.ecole_id != user.ecole_id:
        raise HTTPException(404, "Note introuvable")
    _tracer(session, user, eleve, note.matiere, note.trimestre, note.valeur, None)
    session.delete(note)
    session.commit()


@router.get("/historique", response_model=list[LigneHistorique])
def historique(
    classe_id: int | None = None,
    eleve_id: int | None = None,
    utilisateur_id: int | None = None,
    limite: int = 200,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    """Qui a saisi quoi. Le directeur voit tout ; un enseignant voit ses propres saisies."""
    if user.role == Role.enseignant:
        utilisateur_id = user.id
    stmt = (
        select(NoteHistorique, Utilisateur, Eleve, Classe)
        .join(Utilisateur, NoteHistorique.utilisateur_id == Utilisateur.id)
        .join(Eleve, NoteHistorique.eleve_id == Eleve.id)
        .join(Classe, Eleve.classe_id == Classe.id)
        .where(NoteHistorique.ecole_id == user.ecole_id,
               NoteHistorique.annee_scolaire == annee_courante(session, user.ecole_id))
    )
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    if eleve_id:
        stmt = stmt.where(NoteHistorique.eleve_id == eleve_id)
    if utilisateur_id:
        stmt = stmt.where(NoteHistorique.utilisateur_id == utilisateur_id)
    lignes = session.exec(
        stmt.order_by(NoteHistorique.date.desc(), NoteHistorique.id.desc()).limit(min(max(limite, 1), 1000))
    ).all()
    return [
        LigneHistorique(
            id=h.id, date=h.date, utilisateur=u.nom, role=u.role.value, eleve_id=e.id,
            eleve=f"{e.prenom} {e.nom}", classe=c.nom, matiere=h.matiere, trimestre=h.trimestre,
            ancienne_valeur=h.ancienne_valeur, nouvelle_valeur=h.nouvelle_valeur,
            action="ajout" if h.ancienne_valeur is None else "suppression" if h.nouvelle_valeur is None else "modification",
        )
        for h, u, e, c in lignes
    ]
