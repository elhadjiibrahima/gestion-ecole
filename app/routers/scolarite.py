"""Cahier de textes et statistiques de l'établissement."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from openpyxl import Workbook
from pydantic import BaseModel
from sqlmodel import Session, select

from app.cursus import regles_classe
from app.database import get_session
from app.models import (
    Absence, CahierSaisie, CahierTexte, Classe, Ecole, Eleve, LigneCahier, Matiere, Role, Utilisateur,
)
from app.security import PEDAGOGIE, TOUS, affectations_de, exiger_role
from app.services import annee_courante, bulletins_classe, get_classe, matieres_classe, trier_classes

router = APIRouter(tags=["Cahier de textes et statistiques"])


# ---------- Cahier de textes ----------
def _lignes(session: Session, entrees: list[CahierTexte], user: Utilisateur | None) -> list[LigneCahier]:
    if not entrees:
        return []
    classes = {c.id: c.nom for c in session.exec(select(Classe).where(Classe.id.in_({e.classe_id for e in entrees}))).all()}
    matieres = {m.id: m.nom for m in session.exec(select(Matiere).where(Matiere.id.in_({e.matiere_id for e in entrees}))).all()}
    profs = {u.id: u.nom for u in session.exec(select(Utilisateur).where(
        Utilisateur.id.in_({e.enseignant_id for e in entrees}))).all()}
    return [LigneCahier(id=e.id, classe_id=e.classe_id, classe=classes.get(e.classe_id, "?"),
                        matiere=matieres.get(e.matiere_id, "?"), enseignant=profs.get(e.enseignant_id, "?"),
                        jour=e.jour, contenu=e.contenu, travail=e.travail, pour_le=e.pour_le,
                        modifiable=bool(user) and (user.role == Role.directeur or user.id == e.enseignant_id))
            for e in entrees]


def _verifier(session: Session, user: Utilisateur, classe_id: int, matiere_id: int) -> None:
    classe = get_classe(session, classe_id, user.ecole_id, code=400)
    if matiere_id not in {m.id for m, _ in matieres_classe(session, classe)}:
        raise HTTPException(400, "Cette matière n'est pas enseignée dans cette classe")
    if user.role == Role.enseignant and (matiere_id, classe_id) not in affectations_de(session, user):
        raise HTTPException(403, "Cette matière ou cette classe ne t'est pas attribuée")


@router.post("/cahier", response_model=LigneCahier, status_code=201)
def ecrire(data: CahierSaisie, user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
           session: Session = Depends(get_session)):
    """L'enseignant note ce qui a été fait en cours et le travail à faire."""
    _verifier(session, user, data.classe_id, data.matiere_id)
    e = CahierTexte(ecole_id=user.ecole_id, classe_id=data.classe_id, matiere_id=data.matiere_id, enseignant_id=user.id,
                    jour=data.jour, contenu=data.contenu.strip(), travail=(data.travail or "").strip() or None,
                    pour_le=data.pour_le, annee_scolaire=annee_courante(session, user.ecole_id))
    session.add(e)
    session.commit()
    session.refresh(e)
    return _lignes(session, [e], user)[0]


@router.get("/cahier", response_model=list[LigneCahier])
def lire(classe_id: int | None = None, matiere_id: int | None = None, depuis: date | None = None,
         user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    stmt = select(CahierTexte).where(CahierTexte.ecole_id == user.ecole_id,
                                     CahierTexte.annee_scolaire == annee_courante(session, user.ecole_id))
    if classe_id:
        get_classe(session, classe_id, user.ecole_id)
        stmt = stmt.where(CahierTexte.classe_id == classe_id)
    elif user.role == Role.enseignant:
        stmt = stmt.where(CahierTexte.enseignant_id == user.id)
    if matiere_id:
        stmt = stmt.where(CahierTexte.matiere_id == matiere_id)
    if depuis:
        stmt = stmt.where(CahierTexte.jour >= depuis)
    return _lignes(session, session.exec(stmt.order_by(CahierTexte.jour.desc(), CahierTexte.id.desc()).limit(300)).all(), user)


@router.put("/cahier/{entree_id}", response_model=LigneCahier)
def corriger(entree_id: int, data: CahierSaisie, user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)),
             session: Session = Depends(get_session)):
    e = session.get(CahierTexte, entree_id)
    if not e or e.ecole_id != user.ecole_id:
        raise HTTPException(404, "Entrée introuvable")
    if user.role != Role.directeur and e.enseignant_id != user.id:
        raise HTTPException(403, "Seul l'auteur (ou le directeur) peut corriger cette entrée")
    _verifier(session, user, data.classe_id, data.matiere_id)
    e.classe_id, e.matiere_id, e.jour = data.classe_id, data.matiere_id, data.jour
    e.contenu, e.travail, e.pour_le = data.contenu.strip(), (data.travail or "").strip() or None, data.pour_le
    session.add(e)
    session.commit()
    session.refresh(e)
    return _lignes(session, [e], user)[0]


@router.delete("/cahier/{entree_id}", status_code=204)
def effacer(entree_id: int, user: Utilisateur = Depends(exiger_role(*PEDAGOGIE)), session: Session = Depends(get_session)):
    e = session.get(CahierTexte, entree_id)
    if not e or e.ecole_id != user.ecole_id:
        raise HTTPException(404, "Entrée introuvable")
    if user.role != Role.directeur and e.enseignant_id != user.id:
        raise HTTPException(403, "Seul l'auteur (ou le directeur) peut effacer cette entrée")
    session.delete(e)
    session.commit()


def travail_a_faire(session: Session, classe_id: int, annee: str) -> list[LigneCahier]:
    """Pour l'espace parents : cours des 15 derniers jours et travail à faire."""
    depuis = date.today() - timedelta(days=15)
    return _lignes(session, session.exec(select(CahierTexte).where(
        CahierTexte.classe_id == classe_id, CahierTexte.annee_scolaire == annee, CahierTexte.jour >= depuis)
        .order_by(CahierTexte.jour.desc())).all(), None)


# ---------- Statistiques ----------
class StatClasse(BaseModel):
    classe_id: int
    classe: str
    cycle: str
    effectif: int
    garcons: int
    filles: int
    non_renseigne: int
    moyenne_classe: float | None
    bareme: int
    notes: int                               # élèves ayant une moyenne
    reussite: int                            # moyenne ≥ la moitié du barème
    taux_reussite: float | None
    absences: int
    retards: int


class Statistiques(BaseModel):
    trimestre: int
    classes: list[StatClasse]
    effectif: int
    garcons: int
    filles: int
    taux_reussite: float | None


def calculer_statistiques(session: Session, ecole_id: int, trimestre: int) -> Statistiques:
    annee = annee_courante(session, ecole_id)
    lignes = []
    for c in trier_classes(session.exec(select(Classe).where(Classe.ecole_id == ecole_id)).all()):
        eleves = session.exec(select(Eleve).where(Eleve.classe_id == c.id, Eleve.actif == True)).all()  # noqa: E712
        regles = regles_classe(session, c)
        bulletins = bulletins_classe(session, c, trimestre) if eleves and trimestre <= regles.periodes else []
        moys = [b.moyenne_generale for b in bulletins if b.moyenne_generale is not None]
        ok = sum(1 for m in moys if m >= regles.bareme / 2)
        ids = [e.id for e in eleves]
        abs_ = session.exec(select(Absence).where(Absence.eleve_id.in_(ids), Absence.trimestre == trimestre,
                                                  Absence.annee_scolaire == annee)).all() if ids else []
        lignes.append(StatClasse(
            classe_id=c.id, classe=c.nom, cycle=c.niveau, effectif=len(eleves),
            garcons=sum(1 for e in eleves if e.sexe == "M"), filles=sum(1 for e in eleves if e.sexe == "F"),
            non_renseigne=sum(1 for e in eleves if e.sexe not in ("M", "F")),
            moyenne_classe=round(sum(moys) / len(moys), 2) if moys else None, bareme=regles.bareme,
            notes=len(moys), reussite=ok, taux_reussite=round(100 * ok / len(moys), 1) if moys else None,
            absences=sum(1 for a in abs_ if a.type == "absence"), retards=sum(1 for a in abs_ if a.type == "retard")))
    notes = sum(l.notes for l in lignes)
    return Statistiques(trimestre=trimestre, classes=lignes, effectif=sum(l.effectif for l in lignes),
                        garcons=sum(l.garcons for l in lignes), filles=sum(l.filles for l in lignes),
                        taux_reussite=round(100 * sum(l.reussite for l in lignes) / notes, 1) if notes else None)


@router.get("/statistiques", response_model=Statistiques)
def statistiques(trimestre: int = 1, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                 session: Session = Depends(get_session)):
    """Effectifs par classe et par sexe, moyenne et taux de réussite de la période, absences."""
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Période : 1, 2 ou 3")
    return calculer_statistiques(session, user.ecole_id, trimestre)


@router.get("/export/statistiques.xlsx", response_class=Response)
def export_statistiques(trimestre: int = 1, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                        session: Session = Depends(get_session)):
    from app.routers.exports import _feuille, _reponse, _suffixe
    st = statistiques(trimestre, user, session)
    wb = Workbook()
    _feuille(wb, f"Période {trimestre}", ["Classe", "Cycle", "Effectif", "Garçons", "Filles", "Sexe non renseigné",
                                          "Moyenne de la classe", "Barème", "Élèves notés", "Moyenne atteinte",
                                          "Taux de réussite (%)", "Absences", "Retards"],
             [[l.classe, l.cycle, l.effectif, l.garcons, l.filles, l.non_renseigne, l.moyenne_classe, l.bareme, l.notes,
               l.reussite, l.taux_reussite, l.absences, l.retards] for l in st.classes]
             + [["TOTAL", "", st.effectif, st.garcons, st.filles, None, None, None, None, None, st.taux_reussite, None, None]])
    return _reponse(wb, f"statistiques_P{trimestre}_{_suffixe(session.get(Ecole, user.ecole_id))}.xlsx")
