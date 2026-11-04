"""Paie du personnel : salaire par poste (fixe ou à l'heure), avances, fiches de paie, versement en caisse."""
import re
import threading
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    AbsencePersonnel, AbsencePersonnelSaisie, AgentSaisie, Avance, AvanceSaisie, CategorieDepense, ChoixPoste, Depense, Ecole, FicheDetail, FichePaie,
    FicheSaisie, LignePersonnel, PaiementFiche, Poste, PosteDetail, PosteSaisie, Role, SalaireNiveau, SalaireNiveauSaisie,
    Utilisateur,
)
from app.pdf import en_tete_pdf, pdf_fiche_paie
from app.routers.emploi import heures_du_jour, heures_prevues
from app.security import GESTION, exiger_role

GESTION_ET_SURVEILLANCE = (*GESTION, Role.surveillant)
from app.services import ajouter_postes_defaut, annee_courante

router = APIRouter(prefix="/paie", tags=["Paie"])
ROLES = {"directeur": "Directeur", "comptable": "Comptable", "enseignant": "Enseignant", "surveillant": "Surveillant",
         "agent": "Sans accès au logiciel"}


def _mois(mois: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mois or ""):
        raise HTTPException(400, "Mois invalide (format 2026-11)")
    return mois


def _membre(session: Session, utilisateur_id: int, ecole_id: int) -> Utilisateur:
    u = session.get(Utilisateur, utilisateur_id)
    if not u or u.ecole_id != ecole_id:
        raise HTTPException(404, "Membre du personnel introuvable")
    return u


def _detail(session: Session, f: FichePaie) -> FicheDetail:
    u = session.get(Utilisateur, f.utilisateur_id)
    return FicheDetail(**f.model_dump(include=set(FicheDetail.model_fields) - {"nom", "role"}),
                       nom=u.nom, role=ROLES.get(u.role.value, u.role.value))


def _depense(session: Session, ecole_id: int, user: Utilisateur, libelle: str, montant: int, mode, beneficiaire: str) -> Depense:
    d = Depense(ecole_id=ecole_id, annee_scolaire=annee_courante(session, ecole_id), jour=date.today(),
                categorie=CategorieDepense.salaires, libelle=libelle, montant=montant, mode=mode,
                beneficiaire=beneficiaire, saisi_par_id=user.id)
    session.add(d)
    session.flush()
    return d


def _avances_en_cours(session: Session, utilisateur_id: int) -> list[Avance]:
    return session.exec(select(Avance).where(Avance.utilisateur_id == utilisateur_id, Avance.deduite_sur == None)  # noqa: E711
                        .order_by(Avance.jour)).all()


# ---------- Postes et salaires (fixés par le directeur) ----------
_VERROU_POSTES = threading.Lock()


def postes_ecole(session: Session, ecole_id: int) -> list[Poste]:
    """Les postes de l'école (créés à la première utilisation pour les écoles d'avant la paie par poste)."""
    requete = select(Poste).where(Poste.ecole_id == ecole_id).order_by(Poste.id)
    postes = session.exec(requete).all()
    if not postes:
        with _VERROU_POSTES:                       # deux écrans ouverts en même temps : une seule création
            session.expire_all()
            if not session.exec(requete).first():
                ajouter_postes_defaut(session, ecole_id)
                session.commit()
        postes = session.exec(requete).all()
    return postes


def poste_de(u: Utilisateur, postes: list[Poste]) -> Poste | None:
    """Le poste choisi pour la personne, sinon le poste par défaut de son rôle."""
    return next((p for p in postes if p.id == u.poste_id), None) or \
        next((p for p in postes if p.role == u.role.value), None)


def niveau_personne(session: Session, u: Utilisateur):
    """Niveau (cycle) où enseigne la personne, d'après ses classes (un enseignant reste dans un niveau)."""
    from app.cursus import cycle_de
    from app.models import Affectation, Classe
    c = session.exec(select(Classe).join(Affectation, Affectation.classe_id == Classe.id)
                     .where(Affectation.utilisateur_id == u.id)).first()
    return cycle_de(session, c) if c else None


def contrat_de(session: Session, u: Utilisateur, postes: list[Poste]) -> Poste | None:
    """Poste de la personne avec le salaire de son niveau s'il est fixé (copie non enregistrée), sinon le salaire du poste."""
    p = poste_de(u, postes)
    if not p:
        return None
    cy = niveau_personne(session, u)
    if cy:
        s = session.exec(select(SalaireNiveau).where(SalaireNiveau.poste_id == p.id, SalaireNiveau.cycle_id == cy.id)).first()
        if s:
            return Poste(id=p.id, ecole_id=p.ecole_id, nom=f"{p.nom} — {cy.nom}", type=s.type, montant=s.montant, role=p.role)
    return p


def _personnel(session: Session, ecole_id: int) -> list[Utilisateur]:
    return session.exec(select(Utilisateur).where(Utilisateur.ecole_id == ecole_id, Utilisateur.actif == True)  # noqa: E712
                        .order_by(Utilisateur.nom)).all()


def _poste(session: Session, poste_id: int, ecole_id: int) -> Poste:
    p = session.get(Poste, poste_id)
    if not p or p.ecole_id != ecole_id:
        raise HTTPException(404, "Poste introuvable")
    return p


@router.get("/postes", response_model=list[PosteDetail])
def lister_postes(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    postes = postes_ecole(session, user.ecole_id)
    gens = _personnel(session, user.ecole_id)
    niveaux = session.exec(select(SalaireNiveau).where(SalaireNiveau.poste_id.in_([p.id for p in postes]))).all() if postes else []
    return [PosteDetail(**p.model_dump(include={"id", "nom", "type", "montant", "role"}),
                        effectif=sum(1 for u in gens if (q := poste_de(u, postes)) and q.id == p.id),
                        niveaux=[SalaireNiveauSaisie(cycle_id=n.cycle_id, type=n.type, montant=n.montant)
                                 for n in niveaux if n.poste_id == p.id]) for p in postes]


@router.put("/postes/{poste_id}/niveaux", response_model=list[SalaireNiveauSaisie])
def salaires_par_niveau(poste_id: int, data: list[SalaireNiveauSaisie], user: Utilisateur = Depends(exiger_role(Role.directeur)),
                        session: Session = Depends(get_session)):
    """Salaire du poste selon le niveau où la personne enseigne (niveau absent : salaire du poste)."""
    from app.cursus import cycles_ecole
    p = _poste(session, poste_id, user.ecole_id)
    cycles = {c.id for c in cycles_ecole(session, user.ecole_id)}
    if any(n.cycle_id not in cycles for n in data):
        raise HTTPException(400, "Niveau inconnu")
    for x in session.exec(select(SalaireNiveau).where(SalaireNiveau.poste_id == p.id)).all():
        session.delete(x)
    session.flush()
    vus = set()
    for n in data:
        if n.cycle_id not in vus:
            vus.add(n.cycle_id)
            session.add(SalaireNiveau(poste_id=p.id, cycle_id=n.cycle_id, type=n.type, montant=n.montant))
    session.commit()
    return [n for n in data]


@router.post("/postes", response_model=Poste, status_code=201)
def creer_poste(data: PosteSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                session: Session = Depends(get_session)):
    """Ex : « Enseignant vacataire », 2 500 par heure ; « Gardien », 600 000 par mois."""
    postes = postes_ecole(session, user.ecole_id)
    if any(p.nom.lower() == data.nom.strip().lower() for p in postes):
        raise HTTPException(400, "Ce poste existe déjà")
    p = Poste(ecole_id=user.ecole_id, nom=data.nom.strip(), type=data.type, montant=data.montant)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.patch("/postes/{poste_id}", response_model=Poste)
def modifier_poste(poste_id: int, data: PosteSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                   session: Session = Depends(get_session)):
    """Change le salaire d'un poste : s'applique aux prochaines fiches (les fiches déjà faites ne bougent pas)."""
    p = _poste(session, poste_id, user.ecole_id)
    p.nom, p.type, p.montant = data.nom.strip(), data.type, data.montant
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.delete("/postes/{poste_id}", status_code=204)
def supprimer_poste(poste_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                    session: Session = Depends(get_session)):
    p = _poste(session, poste_id, user.ecole_id)
    if p.role:
        raise HTTPException(400, "Ce poste est celui par défaut d'un rôle : modifie son salaire au lieu de le supprimer")
    postes = postes_ecole(session, user.ecole_id)
    if any((q := poste_de(u, postes)) and q.id == p.id for u in _personnel(session, user.ecole_id)):
        raise HTTPException(400, "Des membres du personnel occupent ce poste : change d'abord leur poste")
    for x in session.exec(select(SalaireNiveau).where(SalaireNiveau.poste_id == p.id)).all():
        session.delete(x)
    for u in session.exec(select(Utilisateur).where(Utilisateur.poste_id == p.id)).all():   # comptes désactivés
        u.poste_id = None
        session.add(u)
    session.delete(p)
    session.commit()


@router.put("/personnel/{utilisateur_id}/poste", response_model=LignePersonnel)
def changer_poste(utilisateur_id: int, data: ChoixPoste, mois: str | None = None,
                  user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    u = _membre(session, utilisateur_id, user.ecole_id)
    u.poste_id = _poste(session, data.poste_id, user.ecole_id).id
    session.add(u)
    session.commit()
    mois = mois or date.today().strftime("%Y-%m")
    return next(l for l in personnel(mois, user, session) if l.utilisateur_id == u.id)


@router.get("/personnel", response_model=list[LignePersonnel])
def personnel(mois: str, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Tout le personnel actif, avec son poste et son salaire, ses heures prévues, ses avances et sa fiche du mois."""
    _mois(mois)
    postes = postes_ecole(session, user.ecole_id)
    lignes = []
    for u in _personnel(session, user.ecole_id):
        c = contrat_de(session, u, postes)
        cy = niveau_personne(session, u) if u.role == Role.enseignant else None
        f = session.exec(select(FichePaie).where(FichePaie.utilisateur_id == u.id, FichePaie.mois == mois)).first()
        jours, heures = _absences_mois(session, u, mois)
        prevues = heures_prevues(session, user.ecole_id, u.id, mois) if c and c.type == "horaire" else None
        lignes.append(LignePersonnel(
            utilisateur_id=u.id, nom=u.nom, role=ROLES.get(u.role.value, u.role.value),
            poste_id=c.id if c else None, poste=c.nom if c else None,
            contrat_type=c.type if c else None, contrat_montant=c.montant if c else None, niveau=cy.nom if cy else None,
            heures_prevues=prevues,
            avances_en_cours=sum(a.montant for a in _avances_en_cours(session, u.id)),
            fiche=_detail(session, f) if f else None,
            sans_compte=u.role == Role.agent, telephone=u.telephone,
            jours_absence=jours, heures_absence=heures, retenue_proposee=_retenue(c, jours, heures),
        ))
    return lignes


# ---------- Absences du personnel ----------
def _absences_mois(session: Session, u: Utilisateur, mois: str) -> tuple[float, float]:
    """(jours, heures de cours) d'absence non justifiée dans le mois."""
    jours, heures = 0.0, 0.0
    for a in session.exec(select(AbsencePersonnel).where(AbsencePersonnel.utilisateur_id == u.id,
                                                         AbsencePersonnel.justifiee == False)).all():  # noqa: E712
        if a.jour.strftime("%Y-%m") != mois:
            continue
        if a.heures:
            heures += a.heures
        else:
            jours += 1
            heures += heures_du_jour(session, u.ecole_id, u.id, a.jour)
    return jours, round(heures, 2)


def _retenue(poste: Poste | None, jours: float, heures: float) -> int:
    """Salaire mensuel : 1/26 par jour (8 h de cours = 1 jour). À l'heure : les heures manquées ne sont pas payées
    (elles sont retirées des heures, pas en retenue)."""
    if not poste or not poste.montant or poste.type == "horaire":
        return 0
    jours_equiv = jours if jours else heures / 8
    return round(poste.montant / 26 * jours_equiv)


@router.post("/absences", response_model=AbsencePersonnel, status_code=201)
def noter_absence(data: AbsencePersonnelSaisie, user: Utilisateur = Depends(exiger_role(*GESTION_ET_SURVEILLANCE)),
                  session: Session = Depends(get_session)):
    """Absence d'un membre du personnel : la journée, ou quelques heures de cours."""
    u = _membre(session, data.utilisateur_id, user.ecole_id)
    if session.exec(select(AbsencePersonnel).where(AbsencePersonnel.utilisateur_id == u.id,
                                                   AbsencePersonnel.jour == data.jour)).first():
        raise HTTPException(400, f"Une absence de {u.nom} est déjà notée ce jour-là")
    a = AbsencePersonnel(ecole_id=user.ecole_id, utilisateur_id=u.id, jour=data.jour, heures=data.heures,
                         motif=(data.motif or "").strip() or None, justifiee=data.justifiee)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@router.get("/absences", response_model=list[AbsencePersonnel])
def lister_absences(mois: str | None = None, user: Utilisateur = Depends(exiger_role(*GESTION_ET_SURVEILLANCE)),
                    session: Session = Depends(get_session)):
    liste = session.exec(select(AbsencePersonnel).where(AbsencePersonnel.ecole_id == user.ecole_id)
                         .order_by(AbsencePersonnel.jour.desc())).all()
    return [a for a in liste if not mois or a.jour.strftime("%Y-%m") == mois]


@router.patch("/absences/{absence_id}", response_model=AbsencePersonnel)
def justifier_absence(absence_id: int, justifiee: bool, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                      session: Session = Depends(get_session)):
    a = session.get(AbsencePersonnel, absence_id)
    if not a or a.ecole_id != user.ecole_id:
        raise HTTPException(404, "Absence introuvable")
    a.justifiee = justifiee
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@router.delete("/absences/{absence_id}", status_code=204)
def supprimer_absence(absence_id: int, user: Utilisateur = Depends(exiger_role(*GESTION_ET_SURVEILLANCE)),
                      session: Session = Depends(get_session)):
    a = session.get(AbsencePersonnel, absence_id)
    if not a or a.ecole_id != user.ecole_id:
        raise HTTPException(404, "Absence introuvable")
    session.delete(a)
    session.commit()


# ---------- Personnel sans compte (gardien, cuisinière, chauffeur…) ----------
@router.post("/agents", response_model=LignePersonnel, status_code=201)
def ajouter_agent(data: AgentSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                  session: Session = Depends(get_session)):
    """Ajoute une personne payée par l'école qui n'utilise pas le logiciel (pas d'e-mail, pas de mot de passe)."""
    postes = postes_ecole(session, user.ecole_id)
    if data.poste_id is not None:
        poste = _poste(session, data.poste_id, user.ecole_id)
    else:
        poste = next((p for p in postes if p.role == "agent"), None)
        if poste is None:
            poste = Poste(ecole_id=user.ecole_id, nom="Personnel d'appui", type="mensuel", montant=0, role="agent")
            session.add(poste)
            session.flush()
    u = Utilisateur(ecole_id=user.ecole_id, nom=data.nom.strip(), role=Role.agent, telephone=data.telephone,
                    email=f"agent-{uuid.uuid4().hex[:12]}@sans-compte.local", mot_de_passe_hash="!", poste_id=poste.id)
    session.add(u)
    session.commit()
    mois = date.today().strftime("%Y-%m")
    return next(l for l in personnel(mois, user, session) if l.utilisateur_id == u.id)


@router.delete("/agents/{utilisateur_id}", status_code=204)
def retirer_agent(utilisateur_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                  session: Session = Depends(get_session)):
    """Ne fait plus partie du personnel (l'historique de paie reste)."""
    u = _membre(session, utilisateur_id, user.ecole_id)
    if u.role != Role.agent:
        raise HTTPException(400, "Pour un compte du logiciel, désactive-le dans Équipe")
    u.actif = False
    session.add(u)
    session.commit()


# ---------- Avances ----------
@router.post("/avances", response_model=Avance, status_code=201)
def donner_avance(data: AvanceSaisie, user: Utilisateur = Depends(exiger_role(*GESTION)),
                  session: Session = Depends(get_session)):
    """Avance sur salaire : sort de la caisse tout de suite, déduite sur la prochaine fiche de paie."""
    u = _membre(session, data.utilisateur_id, user.ecole_id)
    d = _depense(session, user.ecole_id, user, f"Avance sur salaire — {u.nom}", data.montant, data.mode, u.nom)
    a = Avance(ecole_id=user.ecole_id, utilisateur_id=u.id, jour=data.jour or date.today(), montant=data.montant,
               motif=(data.motif or "").strip() or None, depense_id=d.id)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@router.get("/avances", response_model=list[Avance])
def lister_avances(utilisateur_id: int | None = None, user: Utilisateur = Depends(exiger_role(*GESTION)),
                   session: Session = Depends(get_session)):
    stmt = select(Avance).where(Avance.ecole_id == user.ecole_id)
    if utilisateur_id:
        stmt = stmt.where(Avance.utilisateur_id == utilisateur_id)
    return session.exec(stmt.order_by(Avance.jour.desc())).all()


# ---------- Fiches de paie ----------
@router.post("/fiches", response_model=FicheDetail, status_code=201)
def preparer_fiche(data: FicheSaisie, user: Utilisateur = Depends(exiger_role(*GESTION)),
                   session: Session = Depends(get_session)):
    """Calcule la fiche du mois : (salaire fixe ou heures × taux) + primes − retenues − avances en cours."""
    mois = _mois(data.mois)
    u = _membre(session, data.utilisateur_id, user.ecole_id)
    c = contrat_de(session, u, postes_ecole(session, user.ecole_id))
    if not c or not c.montant:
        raise HTTPException(400, f"Le directeur doit d'abord définir le salaire du poste « {c.nom if c else '?'} »")
    if session.exec(select(FichePaie).where(FichePaie.utilisateur_id == u.id, FichePaie.mois == mois)).first():
        raise HTTPException(400, f"La fiche de {mois} de {u.nom} existe déjà")
    jours_abs, heures_abs = _absences_mois(session, u, mois)
    if c.type == "horaire":   # heures faites = heures prévues − heures manquées (sauf si on les tape)
        heures = data.heures if data.heures is not None else \
            max(heures_prevues(session, user.ecole_id, u.id, mois) - heures_abs, 0)
        brut = round(heures * c.montant)
    else:
        heures, brut = None, c.montant
    retenues = data.retenues if data.retenues is not None else _retenue(c, jours_abs, heures_abs)
    avant_avances = max(brut + data.primes - retenues, 0)
    avances = _avances_en_cours(session, u.id)
    deduites, a_deduire = [], 0
    for a in avances:   # on rembourse les avances dans l'ordre, sans dépasser le salaire
        if a_deduire + a.montant <= avant_avances:
            deduites.append(a)
            a_deduire += a.montant
    f = FichePaie(ecole_id=user.ecole_id, utilisateur_id=u.id, mois=mois, type=c.type, heures=heures, taux=c.montant,
                  brut=brut, primes=data.primes, retenues=retenues, avances_deduites=a_deduire,
                  net=avant_avances - a_deduire, note=(data.note or "").strip() or None)
    session.add(f)
    session.flush()
    for a in deduites:
        a.deduite_sur = f.id
        session.add(a)
    session.commit()
    session.refresh(f)
    return _detail(session, f)


def _fiche(session: Session, fiche_id: int, ecole_id: int) -> FichePaie:
    f = session.get(FichePaie, fiche_id)
    if not f or f.ecole_id != ecole_id:
        raise HTTPException(404, "Fiche introuvable")
    return f


@router.post("/fiches/{fiche_id}/payer", response_model=FicheDetail)
def payer(fiche_id: int, data: PaiementFiche, user: Utilisateur = Depends(exiger_role(*GESTION)),
          session: Session = Depends(get_session)):
    """Verse le salaire : la somme nette est enregistrée comme dépense « Salaires » dans la caisse."""
    f = _fiche(session, fiche_id, user.ecole_id)
    if f.payee:
        raise HTTPException(400, "Ce salaire est déjà versé")
    u = session.get(Utilisateur, f.utilisateur_id)
    if f.net > 0:
        f.depense_id = _depense(session, user.ecole_id, user, f"Salaire {f.mois} — {u.nom}", f.net, data.mode, u.nom).id
    f.payee, f.paye_le = True, date.today()
    session.add(f)
    session.commit()
    session.refresh(f)
    return _detail(session, f)


@router.delete("/fiches/{fiche_id}", status_code=204)
def supprimer_fiche(fiche_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                    session: Session = Depends(get_session)):
    """Supprime une fiche pas encore payée (erreur d'heures…) ; ses avances redeviennent à déduire."""
    f = _fiche(session, fiche_id, user.ecole_id)
    if f.payee:
        raise HTTPException(400, "Impossible : ce salaire est déjà versé (annule la dépense dans la caisse si besoin)")
    for a in session.exec(select(Avance).where(Avance.deduite_sur == f.id)).all():
        a.deduite_sur = None
        session.add(a)
    session.delete(f)
    session.commit()


@router.get("/fiches/{fiche_id}.pdf", response_class=Response)
def fiche_pdf(fiche_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    f = _fiche(session, fiche_id, user.ecole_id)
    d = _detail(session, f)
    avances = session.exec(select(Avance).where(Avance.deduite_sur == f.id)).all()
    pdf = pdf_fiche_paie(session.get(Ecole, user.ecole_id), d, avances)
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"fiche_paie_{d.nom}_{d.mois}.pdf"))
