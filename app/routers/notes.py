from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    AppreciationMatiere, CaseNote, Classe, Ecole, Eleve, LigneHistorique, Note, NoteCreate, NoteHistorique, Role, SaisieNote,
    Utilisateur,
)
from app.security import PEDAGOGIE, TOUS, affectations_de, exiger_role
from app.services import annee_courante, get_eleve, matieres_classe, matieres_ecole, moyenne_matiere

router = APIRouter(prefix="/notes", tags=["Notes"])


def _tracer(session: Session, user: Utilisateur, eleve: Eleve, matiere: str, trimestre: int,
            ancienne: float | None, nouvelle: float | None, type_note: str | None = None) -> None:
    """Garde une trace de chaque changement de note."""
    if ancienne == nouvelle:
        return
    session.add(NoteHistorique(
        ecole_id=eleve.ecole_id, eleve_id=eleve.id, utilisateur_id=user.id, matiere=matiere,
        trimestre=trimestre, ancienne_valeur=ancienne, nouvelle_valeur=nouvelle, type_note=type_note,
        annee_scolaire=annee_courante(session, eleve.ecole_id),
    ))


def _libelle(type_: str, numero: int) -> str:
    return "composition" if type_ == "composition" else f"devoir {numero}"


def _peut_saisir(session: Session, user: Utilisateur, matiere_id: int | None, classe_id: int,
                 affectations: set | None = None) -> bool:
    """Le directeur saisit tout ; l'enseignant seulement ses matières dans ses classes."""
    if user.role == Role.directeur:
        return True
    if user.role != Role.enseignant or matiere_id is None:
        return False
    if affectations is None:
        affectations = affectations_de(session, user)
    return (matiere_id, classe_id) in affectations


def _matiere_par_nom(session: Session, ecole_id: int, nom: str):
    return next((m for m in matieres_ecole(session, ecole_id) if m.nom.lower() == nom.lower()), None)


@router.post("", response_model=Note, status_code=201)
def ajouter_note(
    data: NoteCreate,
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    eleve = get_eleve(session, data.eleve_id, user.ecole_id, code=400)
    m = _matiere_par_nom(session, user.ecole_id, data.matiere)
    if user.role == Role.enseignant and not _peut_saisir(session, user, m.id if m else None, eleve.classe_id):
        raise HTTPException(403, "Cette matière ou cette classe ne t'est pas attribuée")
    if data.type not in ("devoir", "composition"):
        raise HTTPException(400, "Type de note inconnu (devoir ou composition)")
    note = Note.model_validate(data, update={"annee_scolaire": annee_courante(session, user.ecole_id)})
    session.add(note)
    _tracer(session, user, eleve, note.matiere, note.trimestre, None, note.valeur, _libelle(note.type, note.numero))
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


def _notes_trimestre(session: Session, eleve: Eleve, trimestre: int) -> list[Note]:
    return session.exec(
        select(Note).where(Note.eleve_id == eleve.id, Note.trimestre == trimestre,
                           Note.annee_scolaire == annee_courante(session, eleve.ecole_id)).order_by(Note.id)
    ).all()


def _grille(session: Session, eleve: Eleve, trimestre: int, user: Utilisateur) -> list[CaseNote]:
    ecole = session.get(Ecole, eleve.ecole_id)
    nb = ecole.nb_devoirs
    par_matiere: dict[str, list[Note]] = {}
    for n in _notes_trimestre(session, eleve, trimestre):
        par_matiere.setdefault(n.matiere.lower(), []).append(n)

    # Dernière modification de chaque matière (qui, quand)
    traces = session.exec(
        select(NoteHistorique, Utilisateur.nom)
        .join(Utilisateur, NoteHistorique.utilisateur_id == Utilisateur.id)
        .where(NoteHistorique.eleve_id == eleve.id, NoteHistorique.trimestre == trimestre,
               NoteHistorique.annee_scolaire == annee_courante(session, eleve.ecole_id))
        .order_by(NoteHistorique.date, NoteHistorique.id)
    ).all()
    derniere = {h.matiere.lower(): (nom, h.date) for h, nom in traces}
    affectations = affectations_de(session, user) if user.role == Role.enseignant else None
    appreciations = {a.matiere.lower(): a.texte for a in session.exec(
        select(AppreciationMatiere).where(AppreciationMatiere.eleve_id == eleve.id, AppreciationMatiere.trimestre == trimestre,
                                          AppreciationMatiere.annee_scolaire == ecole.annee_scolaire)).all()}

    def case(matiere_id, nom, coef, ns):
        devoirs: list = [None] * nb
        compos = [n.valeur for n in ns if n.type == "composition"]
        for n in ns:
            if n.type != "composition":
                if 1 <= n.numero <= nb and devoirs[n.numero - 1] is None:
                    devoirs[n.numero - 1] = n.valeur
                elif nb:   # devoir en trop (ancienne saisie) : on le range dans la première case libre
                    libre = next((i for i, v in enumerate(devoirs) if v is None), None)
                    if libre is not None:
                        devoirs[libre] = n.valeur
        moy = moyenne_matiere(ns, ecole.poids_composition)
        par, le = derniere.get(nom.lower(), (None, None)) if ns else (None, None)
        return CaseNote(matiere_id=matiere_id, matiere=nom, coefficient=coef, devoirs=devoirs,
                        composition=round(sum(compos) / len(compos), 2) if compos else None,
                        valeur=round(moy, 2) if moy is not None else None,
                        modifiable=_peut_saisir(session, user, matiere_id, eleve.classe_id, affectations),
                        saisi_par=par, saisi_le=le, appreciation=appreciations.get(nom.lower()))

    cases = []
    for m, coef in matieres_classe(session, session.get(Classe, eleve.classe_id)):
        cases.append(case(m.id, m.nom, coef, par_matiere.pop(m.nom.lower(), [])))
    for ns in par_matiere.values():  # notes d'une matière qui n'est plus dans la liste
        cases.append(case(None, ns[0].matiere, ns[0].coefficient, ns))
    return cases


@router.get("/grille", response_model=list[CaseNote])
def grille(
    eleve_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Une ligne par matière : devoirs, composition, moyenne (et si l'utilisateur peut la modifier)."""
    _verifier_trimestre(trimestre)
    return _grille(session, get_eleve(session, eleve_id, user.ecole_id), trimestre, user)


@router.put("/grille", response_model=list[CaseNote])
def enregistrer_grille(
    eleve_id: int,
    trimestre: int,
    saisies: list[SaisieNote],
    user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
    session: Session = Depends(get_session),
):
    """Enregistre la grille. Une case vide efface la note. Un enseignant ne peut toucher
    qu'aux matières qui lui sont attribuées dans la classe de l'élève."""
    _verifier_trimestre(trimestre)
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    ecole = session.get(Ecole, user.ecole_id)
    annee = ecole.annee_scolaire
    matieres = {m.id: (m, coef) for m, coef in matieres_classe(session, session.get(Classe, eleve.classe_id))}
    affectations = affectations_de(session, user) if user.role == Role.enseignant else None
    for s in saisies:
        if s.matiere_id not in matieres:
            raise HTTPException(400, "Matière introuvable")
        m, coef = matieres[s.matiere_id]
        if not _peut_saisir(session, user, m.id, eleve.classe_id, affectations):
            raise HTTPException(403, f"« {m.nom} » dans cette classe ne t'est pas attribuée")
        if s.devoirs is not None:
            if len(s.devoirs) > ecole.nb_devoirs:
                raise HTTPException(400, f"Au plus {ecole.nb_devoirs} devoir(s) par matière")
            if any(v is not None and not 0 <= v <= 20 for v in s.devoirs):
                raise HTTPException(422, "Une note doit être entre 0 et 20")
        # Cases demandées : {(type, numéro): nouvelle valeur}
        voulu: dict[tuple[str, int], float | None] = {}
        if s.devoirs is not None:
            for i in range(ecole.nb_devoirs):
                voulu[("devoir", i + 1)] = s.devoirs[i] if i < len(s.devoirs) else None
        if s.composition is not None or s.effacer_composition:
            voulu[("composition", 1)] = s.composition
        elif s.valeur is not None or (s.devoirs is None and "valeur" in s.model_fields_set):
            voulu[("composition", 1)] = s.valeur          # ancienne saisie à une seule note
        existantes = [n for n in _notes_trimestre(session, eleve, trimestre) if n.matiere == m.nom]
        for (type_, numero), valeur in voulu.items():
            if type_ == "composition":
                actuelles = [n for n in existantes if n.type == "composition"]
            else:
                actuelles = [n for n in existantes if n.type != "composition" and n.numero == numero]
            ancienne = round(sum(n.valeur for n in actuelles) / len(actuelles), 2) if actuelles else None
            if len(actuelles) <= 1 and ancienne == valeur:
                continue   # case inchangée
            for n in actuelles:
                session.delete(n)
            if valeur is not None:
                session.add(Note(eleve_id=eleve.id, matiere=m.nom, coefficient=coef, valeur=valeur,
                                 trimestre=trimestre, annee_scolaire=annee, type=type_, numero=numero))
            _tracer(session, user, eleve, m.nom, trimestre, ancienne, valeur, _libelle(type_, numero))
        if s.appreciation is not None:   # appréciation de l'enseignant pour cette matière
            ancienne_app = session.exec(select(AppreciationMatiere).where(
                AppreciationMatiere.eleve_id == eleve.id, AppreciationMatiere.matiere == m.nom,
                AppreciationMatiere.trimestre == trimestre, AppreciationMatiere.annee_scolaire == annee)).first()
            texte = s.appreciation.strip()
            if ancienne_app and not texte:
                session.delete(ancienne_app)
            elif ancienne_app and ancienne_app.texte != texte:
                ancienne_app.texte, ancienne_app.utilisateur_id = texte, user.id
                session.add(ancienne_app)
            elif texte and not ancienne_app:
                session.add(AppreciationMatiere(eleve_id=eleve.id, matiere=m.nom, trimestre=trimestre,
                                                annee_scolaire=annee, texte=texte, utilisateur_id=user.id))
        # le coefficient suit celui de la classe
        for n in existantes:
            if n.coefficient != coef:
                n.coefficient = coef
                session.add(n)
    session.commit()
    return _grille(session, eleve, trimestre, user)


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
    if user.role == Role.enseignant:
        m = _matiere_par_nom(session, user.ecole_id, note.matiere)
        if not _peut_saisir(session, user, m.id if m else None, eleve.classe_id):
            raise HTTPException(403, "Cette matière ou cette classe ne t'est pas attribuée")
    _tracer(session, user, eleve, note.matiere, note.trimestre, note.valeur, None, _libelle(note.type, note.numero))
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
            type_note=h.type_note,
        )
        for h, u, e, c in lignes
    ]
