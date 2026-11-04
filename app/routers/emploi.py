"""Emploi du temps : créneaux par classe, vue par enseignant, impression PDF."""
import calendar
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    JOURS, Classe, Creneau, CreneauDetail, CreneauSaisie, Ecole, Matiere, Role, Utilisateur,
)
from app.pdf import en_tete_pdf, pdf_emploi_du_temps
from app.security import TOUS, exiger_role
from app.services import get_classe

router = APIRouter(tags=["Emploi du temps"])


def _minutes(h: str) -> int:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", h.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise HTTPException(400, f"Heure « {h} » invalide (format 08:30)")
    return int(m.group(1)) * 60 + int(m.group(2))


def _normaliser(h: str) -> str:
    m = _minutes(h)
    return f"{m // 60:02d}:{m % 60:02d}"


def _details(session: Session, creneaux: list[Creneau]) -> list[CreneauDetail]:
    if not creneaux:
        return []
    eid = creneaux[0].ecole_id
    classes = {c.id: c.nom for c in session.exec(select(Classe).where(Classe.ecole_id == eid))}
    matieres = {m.id: m.nom for m in session.exec(select(Matiere).where(Matiere.ecole_id == eid))}
    profs = {u.id: u.nom for u in session.exec(select(Utilisateur).where(Utilisateur.ecole_id == eid))}
    return [CreneauDetail(id=c.id, classe_id=c.classe_id, classe=classes.get(c.classe_id, "?"), jour=c.jour,
                          debut=c.debut, fin=c.fin, matiere_id=c.matiere_id, matiere=matieres.get(c.matiere_id, "?"),
                          enseignant_id=c.enseignant_id, enseignant=profs.get(c.enseignant_id), salle=c.salle)
            for c in sorted(creneaux, key=lambda c: (c.jour, c.debut))]


def creneaux_de(session: Session, ecole_id: int, classe_id: int | None = None,
                enseignant_id: int | None = None) -> list[Creneau]:
    stmt = select(Creneau).where(Creneau.ecole_id == ecole_id)
    if classe_id:
        stmt = stmt.where(Creneau.classe_id == classe_id)
    if enseignant_id:
        stmt = stmt.where(Creneau.enseignant_id == enseignant_id)
    return session.exec(stmt).all()


def _verifier(session: Session, ecole_id: int, data: CreneauSaisie, sauf_id: int | None = None) -> tuple[str, str]:
    get_classe(session, data.classe_id, ecole_id, code=400)
    m = session.get(Matiere, data.matiere_id)
    if not m or m.ecole_id != ecole_id:
        raise HTTPException(400, "Matière introuvable")
    if data.enseignant_id is not None:
        u = session.get(Utilisateur, data.enseignant_id)
        if not u or u.ecole_id != ecole_id or u.role not in (Role.enseignant, Role.directeur):
            raise HTTPException(400, "Enseignant introuvable")
    debut, fin = _normaliser(data.debut), _normaliser(data.fin)
    if _minutes(fin) <= _minutes(debut):
        raise HTTPException(400, "L'heure de fin doit être après l'heure de début")
    # Conflits : même classe, même enseignant ou même salle sur le même horaire
    for c in creneaux_de(session, ecole_id):
        if c.id == sauf_id or c.jour != data.jour:
            continue
        if _minutes(c.debut) < _minutes(fin) and _minutes(debut) < _minutes(c.fin):
            quand = f"le {JOURS[c.jour - 1].lower()} de {c.debut} à {c.fin}"
            if c.classe_id == data.classe_id:
                raise HTTPException(400, f"La classe a déjà cours {quand}")
            if data.enseignant_id and c.enseignant_id == data.enseignant_id:
                classe = session.get(Classe, c.classe_id)
                raise HTTPException(400, f"Cet enseignant est déjà en {classe.nom} {quand}")
            if data.salle and c.salle and c.salle.strip().lower() == data.salle.strip().lower():
                raise HTTPException(400, f"La salle {c.salle} est déjà occupée {quand}")
    return debut, fin


@router.get("/emploi-du-temps", response_model=list[CreneauDetail])
def voir(classe_id: int | None = None, enseignant_id: int | None = None,
         user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    """Emploi du temps d'une classe ou d'un enseignant (un enseignant voit le sien par défaut)."""
    if not classe_id and not enseignant_id and user.role == Role.enseignant:
        enseignant_id = user.id
    if classe_id:
        get_classe(session, classe_id, user.ecole_id)
    return _details(session, creneaux_de(session, user.ecole_id, classe_id, enseignant_id))


@router.post("/creneaux", response_model=CreneauDetail, status_code=201)
def ajouter(data: CreneauSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
            session: Session = Depends(get_session)):
    debut, fin = _verifier(session, user.ecole_id, data)
    c = Creneau(**data.model_dump(exclude={"debut", "fin"}), debut=debut, fin=fin, ecole_id=user.ecole_id)
    c.salle = (c.salle or "").strip() or None
    session.add(c)
    session.commit()
    session.refresh(c)
    return _details(session, [c])[0]


def _get(session: Session, creneau_id: int, ecole_id: int) -> Creneau:
    c = session.get(Creneau, creneau_id)
    if not c or c.ecole_id != ecole_id:
        raise HTTPException(404, "Créneau introuvable")
    return c


@router.put("/creneaux/{creneau_id}", response_model=CreneauDetail)
def modifier(creneau_id: int, data: CreneauSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
             session: Session = Depends(get_session)):
    c = _get(session, creneau_id, user.ecole_id)
    debut, fin = _verifier(session, user.ecole_id, data, sauf_id=c.id)
    c.sqlmodel_update(data.model_dump(exclude={"debut", "fin"}))
    c.debut, c.fin, c.salle = debut, fin, (data.salle or "").strip() or None
    session.add(c)
    session.commit()
    return _details(session, [c])[0]


@router.delete("/creneaux/{creneau_id}", status_code=204)
def supprimer(creneau_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
              session: Session = Depends(get_session)):
    session.delete(_get(session, creneau_id, user.ecole_id))
    session.commit()


@router.get("/emploi-du-temps.pdf", response_class=Response)
def imprimer(classe_id: int | None = None, enseignant_id: int | None = None,
             user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    if not classe_id and not enseignant_id:
        if user.role != Role.enseignant:
            raise HTTPException(400, "Choisis une classe ou un enseignant")
        enseignant_id = user.id
    if classe_id:
        titre = f"Classe : {get_classe(session, classe_id, user.ecole_id).nom}"
    else:
        u = session.get(Utilisateur, enseignant_id)
        if not u or u.ecole_id != user.ecole_id:
            raise HTTPException(404, "Enseignant introuvable")
        titre = f"Enseignant : {u.nom}"
    creneaux = _details(session, creneaux_de(session, user.ecole_id, classe_id, enseignant_id))
    pdf = pdf_emploi_du_temps(session.get(Ecole, user.ecole_id), titre, creneaux, par_classe=bool(classe_id))
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"emploi_du_temps_{titre}.pdf"))


def heures_du_jour(session: Session, ecole_id: int, enseignant_id: int, jour: date) -> float:
    """Heures de cours de l'enseignant ce jour-là (d'après l'emploi du temps)."""
    return round(sum(_minutes(c.fin) - _minutes(c.debut) for c in creneaux_de(session, ecole_id, enseignant_id=enseignant_id)
                     if c.jour == jour.isoweekday()) / 60, 2)


def heures_prevues(session: Session, ecole_id: int, enseignant_id: int, mois: str) -> float:
    """Heures de cours d'un enseignant pour un mois, d'après l'emploi du temps
    (chaque créneau compte autant de fois que son jour apparaît dans le mois)."""
    annee, m = (int(x) for x in mois.split("-"))
    nb_jours = calendar.monthrange(annee, m)[1]
    occurrences = {j: 0 for j in range(1, 8)}
    for d in range(1, nb_jours + 1):
        occurrences[date(annee, m, d).isoweekday()] += 1
    total = sum((_minutes(c.fin) - _minutes(c.debut)) * occurrences[c.jour]
                for c in creneaux_de(session, ecole_id, enseignant_id=enseignant_id))
    return round(total / 60, 2)
