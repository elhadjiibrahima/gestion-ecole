from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from app.database import get_session
from app.models import (
    MATIERES_PAR_DEFAUT, CoefficientClasse, CoefficientsClasses, Eleve, LigneCoefficient, Matiere,
    MatiereCreate, MatiereUpdate, Note, Role, Utilisateur,
)
from app.security import TOUS, exiger_role
from app.services import (
    annee_courante, coefficients_specifiques, get_classe, matieres_classe, matieres_ecole,
)

router = APIRouter(prefix="/matieres", tags=["Matières"])


def ajouter_matieres_defaut(session: Session, ecole_id: int) -> int:
    """Ajoute les matières par défaut qui n'existent pas encore. Renvoie le nombre ajouté."""
    existantes = {m.nom.lower() for m in matieres_ecole(session, ecole_id)}
    ordre = len(existantes)
    ajoutees = 0
    for nom, coef in MATIERES_PAR_DEFAUT:
        if nom.lower() not in existantes:
            session.add(Matiere(nom=nom, coefficient=coef, ecole_id=ecole_id, ordre=ordre))
            ordre += 1
            ajoutees += 1
    return ajoutees


def _get(session: Session, matiere_id: int, ecole_id: int) -> Matiere:
    m = session.get(Matiere, matiere_id)
    if not m or m.ecole_id != ecole_id:
        raise HTTPException(404, "Matière introuvable")
    return m


def _nom_libre(session: Session, ecole_id: int, nom: str, sauf_id: int | None = None) -> str:
    nom = nom.strip()
    if not nom:
        raise HTTPException(400, "Le nom de la matière est vide")
    doublon = session.exec(
        select(Matiere).where(Matiere.ecole_id == ecole_id, func.lower(Matiere.nom) == nom.lower())
    ).first()
    if doublon and doublon.id != sauf_id:
        raise HTTPException(400, f"La matière « {nom} » existe déjà")
    return nom


def _notes_annee(session: Session, ecole_id: int, nom: str, classe_ids: set[int] | None = None,
                 sauf_classes: set[int] | None = None) -> list[Note]:
    """Notes de l'année en cours dans une matière (éventuellement limitées à certaines classes)."""
    stmt = (select(Note).join(Eleve, Note.eleve_id == Eleve.id)
            .where(Eleve.ecole_id == ecole_id, Note.matiere == nom,
                   Note.annee_scolaire == annee_courante(session, ecole_id)))
    if classe_ids is not None:
        stmt = stmt.where(Eleve.classe_id.in_(classe_ids))
    if sauf_classes:
        stmt = stmt.where(Eleve.classe_id.not_in(sauf_classes))
    return session.exec(stmt).all()


def _a_des_notes(session: Session, ecole_id: int, nom: str) -> bool:
    return session.exec(
        select(Note.id).join(Eleve, Note.eleve_id == Eleve.id)
        .where(Eleve.ecole_id == ecole_id, Note.matiere == nom).limit(1)
    ).first() is not None


@router.get("", response_model=list[Matiere])
def lister(user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    return matieres_ecole(session, user.ecole_id)


@router.post("", response_model=Matiere, status_code=201)
def creer(
    data: MatiereCreate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    nom = _nom_libre(session, user.ecole_id, data.nom)
    ordre = len(matieres_ecole(session, user.ecole_id))
    m = Matiere(nom=nom, coefficient=data.coefficient, ecole_id=user.ecole_id, ordre=ordre)
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@router.post("/defaut", response_model=list[Matiere])
def creer_defaut(user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    """Ajoute les matières usuelles du collège qui manquent."""
    ajouter_matieres_defaut(session, user.ecole_id)
    session.commit()
    return matieres_ecole(session, user.ecole_id)


# ---------- Coefficients par classe ----------
@router.get("/coefficients", response_model=list[LigneCoefficient])
def coefficients_classe(
    classe_id: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Coefficient de chaque matière pour une classe (spécifique ou par défaut)."""
    classe = get_classe(session, classe_id, user.ecole_id)
    specifiques = coefficients_specifiques(session, classe.id)
    return [
        LigneCoefficient(matiere_id=m.id, matiere=m.nom, coefficient_defaut=m.coefficient,
                         coefficient=coef, specifique=m.id in specifiques)
        for m, coef in matieres_classe(session, classe)
    ]


@router.put("/coefficients", response_model=list[LigneCoefficient])
def definir_coefficients(
    data: CoefficientsClasses,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Applique des coefficients à une ou plusieurs classes. `null` = revenir au coefficient par défaut."""
    classes = [get_classe(session, cid, user.ecole_id, code=400) for cid in data.classe_ids]
    matieres = {m.id: m for m in matieres_ecole(session, user.ecole_id)}
    for saisie in data.coefficients:
        m = matieres.get(saisie.matiere_id)
        if not m:
            raise HTTPException(400, "Matière introuvable")
        for classe in classes:
            existant = session.exec(select(CoefficientClasse).where(
                CoefficientClasse.classe_id == classe.id, CoefficientClasse.matiere_id == m.id)).first()
            nouveau = saisie.coefficient
            if nouveau is None or nouveau == m.coefficient:
                if existant:
                    session.delete(existant)
                coef = m.coefficient
            else:
                if existant:
                    existant.coefficient = nouveau
                else:
                    session.add(CoefficientClasse(matiere_id=m.id, classe_id=classe.id, coefficient=nouveau))
                coef = nouveau
            # Les notes déjà saisies de cette classe suivent
            for n in _notes_annee(session, user.ecole_id, m.nom, classe_ids={classe.id}):
                n.coefficient = coef
                session.add(n)
    session.commit()
    return coefficients_classe(classes[0].id, user, session)


@router.patch("/{matiere_id}", response_model=Matiere)
def modifier(
    matiere_id: int,
    data: MatiereUpdate,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    """Renommer ou changer le coefficient par défaut : les notes de l'année en cours suivent
    (sauf, pour le coefficient, dans les classes qui ont un coefficient spécifique)."""
    m = _get(session, matiere_id, user.ecole_id)
    ancien_nom = m.nom
    if data.nom is not None:
        m.nom = _nom_libre(session, user.ecole_id, data.nom, sauf_id=m.id)
        if m.nom != ancien_nom:   # renommage : toutes les notes (toutes années) gardent le lien
            notes = session.exec(select(Note).join(Eleve, Note.eleve_id == Eleve.id)
                                 .where(Eleve.ecole_id == user.ecole_id, Note.matiere == ancien_nom)).all()
            for n in notes:
                n.matiere = m.nom
                session.add(n)
    if data.coefficient is not None and data.coefficient != m.coefficient:
        m.coefficient = data.coefficient
        avec_specifique = {c.classe_id for c in session.exec(
            select(CoefficientClasse).where(CoefficientClasse.matiere_id == m.id)).all()}
        session.flush()
        for n in _notes_annee(session, user.ecole_id, m.nom, sauf_classes=avec_specifique):
            n.coefficient = m.coefficient
            session.add(n)
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@router.delete("/{matiere_id}", status_code=204)
def supprimer(
    matiere_id: int,
    user: Utilisateur = Depends(exiger_role(Role.directeur)),
    session: Session = Depends(get_session),
):
    m = _get(session, matiere_id, user.ecole_id)
    if _a_des_notes(session, user.ecole_id, m.nom):
        raise HTTPException(400, f"Impossible : des notes existent déjà en « {m.nom} ». Efface-les d'abord.")
    from app.models import Affectation, MatiereCycle
    for modele in (CoefficientClasse, MatiereCycle, Affectation):
        for c in session.exec(select(modele).where(modele.matiere_id == m.id)).all():
            session.delete(c)
    session.delete(m)
    session.commit()
