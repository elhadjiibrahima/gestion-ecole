"""Super admin : gestion de toutes les écoles et de leurs abonnements.

Le compte super admin ne se crée pas depuis le site : uniquement en ligne de commande
sur le serveur (`python -m app.cli creer-superadmin`).
"""
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, func, select

from app.database import get_session
from app.models import (
    ChangementMotDePasse, CorrectionAbonnement, Ecole, EcoleAdmin, Eleve, InscriptionEcole, NoteHistorique,
    Paiement, PaiementAbonnement, ProlongationAbonnement, ReinitialisationMotDePasse, Role, StatsPlateforme,
    SuperAdmin, Suspension, Token, Utilisateur,
)
from app.security import (
    creer_token_superadmin, hacher_mot_de_passe, superadmin_courant, verifier_mot_de_passe,
)
from app.services import etat_abonnement

router = APIRouter(prefix="/admin", tags=["Super admin"])

_ECHECS: dict[str, list[float]] = {}
MAX_ECHECS, FENETRE = 5, 15 * 60


class ConnexionAdmin(SQLModel):
    email: str
    mot_de_passe: str


@router.post("/connexion", response_model=Token)
def connexion(data: ConnexionAdmin, session: Session = Depends(get_session)):
    email = data.email.strip().lower()
    maintenant = time.time()
    _ECHECS[email] = [t for t in _ECHECS.get(email, []) if maintenant - t < FENETRE]
    if len(_ECHECS[email]) >= MAX_ECHECS:
        raise HTTPException(429, "Trop d'essais : réessaie dans 15 minutes")
    admin = session.exec(select(SuperAdmin).where(SuperAdmin.email == email)).first()
    if not admin or not verifier_mot_de_passe(data.mot_de_passe, admin.mot_de_passe_hash):
        _ECHECS[email].append(maintenant)
        raise HTTPException(401, "E-mail ou mot de passe incorrect")
    _ECHECS.pop(email, None)
    return Token(access_token=creer_token_superadmin(admin))


@router.post("/mot-de-passe", status_code=204)
def changer_mot_de_passe(data: ChangementMotDePasse, admin: SuperAdmin = Depends(superadmin_courant),
                         session: Session = Depends(get_session)):
    if not verifier_mot_de_passe(data.ancien, admin.mot_de_passe_hash):
        raise HTTPException(400, "Ancien mot de passe incorrect")
    admin.mot_de_passe_hash = hacher_mot_de_passe(data.nouveau)
    session.add(admin)
    session.commit()


def _vue(session: Session, e: Ecole) -> EcoleAdmin:
    directeur = session.exec(select(Utilisateur).where(Utilisateur.ecole_id == e.id, Utilisateur.role == Role.directeur)
                             .order_by(Utilisateur.id)).first()
    nb_eleves = session.exec(select(func.count()).select_from(Eleve)
                             .where(Eleve.ecole_id == e.id, Eleve.actif == True)).one()  # noqa: E712
    nb_users = session.exec(select(func.count()).select_from(Utilisateur).where(Utilisateur.ecole_id == e.id)).one()
    total = session.exec(select(func.coalesce(func.sum(PaiementAbonnement.montant), 0))
                         .where(PaiementAbonnement.ecole_id == e.id)).one()
    derniers = [d for d in (
        session.exec(select(func.max(NoteHistorique.date)).where(NoteHistorique.ecole_id == e.id)).one(),
        session.exec(select(func.max(Paiement.date_paiement)).join(Eleve, Paiement.eleve_id == Eleve.id)
                     .where(Eleve.ecole_id == e.id)).one(),
    ) if d]
    derniere = max((d if isinstance(d, datetime) else datetime(d.year, d.month, d.day) for d in derniers),
                   key=lambda d: d.replace(tzinfo=None), default=None)
    return EcoleAdmin(
        id=e.id, nom=e.nom, telephone=e.telephone, adresse=e.adresse, cree_le=e.cree_le,
        annee_scolaire=e.annee_scolaire, directeur=directeur.nom if directeur else None,
        email_directeur=directeur.email if directeur else None, nb_eleves=nb_eleves, nb_utilisateurs=nb_users,
        abonnement=etat_abonnement(e), plan=e.plan, motif_suspension=e.motif_suspension,
        total_abonnements=total, derniere_activite=derniere,
    )


def _ecole(session: Session, ecole_id: int) -> Ecole:
    e = session.get(Ecole, ecole_id)
    if not e:
        raise HTTPException(404, "École introuvable")
    return e


@router.get("/stats", response_model=StatsPlateforme)
def stats(admin: SuperAdmin = Depends(superadmin_courant), session: Session = Depends(get_session)):
    ecoles = session.exec(select(Ecole)).all()
    etats = [etat_abonnement(e) for e in ecoles]
    debut_mois = date.today().replace(day=1)
    paiements = session.exec(select(PaiementAbonnement)).all()
    return StatsPlateforme(
        nb_ecoles=len(ecoles),
        actives=sum(e.statut == "actif" for e in etats),
        en_essai=sum(e.statut == "essai" for e in etats),
        expirees=sum(e.statut == "expire" for e in etats),
        suspendues=sum(e.statut == "suspendu" for e in etats),
        expirent_bientot=sum(e.statut in ("actif", "essai") and e.jours_restants is not None
                             and e.jours_restants <= 7 for e in etats),
        nb_eleves=session.exec(select(func.count()).select_from(Eleve).where(Eleve.actif == True)).one(),  # noqa: E712
        revenus_total=sum(p.montant for p in paiements),
        revenus_mois=sum(p.montant for p in paiements if p.date.date() >= debut_mois),
    )


@router.get("/ecoles", response_model=list[EcoleAdmin])
def lister(admin: SuperAdmin = Depends(superadmin_courant), session: Session = Depends(get_session)):
    vues = [_vue(session, e) for e in session.exec(select(Ecole)).all()]
    ordre = {"expire": 0, "suspendu": 1, "essai": 2, "actif": 3}
    return sorted(vues, key=lambda v: (ordre.get(v.abonnement.statut, 9), v.abonnement.jours_restants or 0, v.nom))


@router.post("/ecoles", response_model=EcoleAdmin, status_code=201)
def creer(data: InscriptionEcole, mois_offerts: int | None = None,
          admin: SuperAdmin = Depends(superadmin_courant), session: Session = Depends(get_session)):
    """Ouvre une école (période d'essai, ou `mois_offerts` d'abonnement actif)."""
    from app.routers.auth import creer_ecole_et_directeur
    if mois_offerts:
        creer_ecole_et_directeur(session, data, statut="actif", fin=_ajouter_mois(date.today(), mois_offerts))
    else:
        creer_ecole_et_directeur(session, data)
    e = session.exec(select(Ecole).order_by(Ecole.id.desc())).first()
    return _vue(session, e)


@router.get("/ecoles/{ecole_id}/abonnements", response_model=list[PaiementAbonnement])
def historique(ecole_id: int, admin: SuperAdmin = Depends(superadmin_courant), session: Session = Depends(get_session)):
    _ecole(session, ecole_id)
    return session.exec(select(PaiementAbonnement).where(PaiementAbonnement.ecole_id == ecole_id)
                        .order_by(PaiementAbonnement.date.desc())).all()


def _ajouter_mois(d: date, mois: int) -> date:
    m = d.month - 1 + mois
    annee, mois_ = d.year + m // 12, m % 12 + 1
    jours = [31, 29 if annee % 4 == 0 and (annee % 100 or annee % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(annee, mois_, min(d.day, jours[mois_ - 1]))


@router.post("/ecoles/{ecole_id}/abonnement", response_model=EcoleAdmin)
def prolonger(ecole_id: int, data: ProlongationAbonnement, admin: SuperAdmin = Depends(superadmin_courant),
              session: Session = Depends(get_session)):
    """Enregistre un paiement d'abonnement et prolonge l'accès (à partir de la fin actuelle si elle
    est dans le futur, sinon à partir d'aujourd'hui). Lève une éventuelle suspension."""
    e = _ecole(session, ecole_id)
    debut = max(date.today(), (e.abonnement_fin + timedelta(days=1)) if e.abonnement_fin else date.today())
    fin = _ajouter_mois(debut, data.mois) - timedelta(days=1)
    session.add(PaiementAbonnement(ecole_id=e.id, montant=data.montant, devise=data.devise, mois=data.mois,
                                   debut=debut, fin=fin, mode=data.mode, reference=data.reference,
                                   note=data.note, saisi_par_id=admin.id))
    e.abonnement_fin, e.abonnement_statut, e.motif_suspension = fin, "actif", None
    if data.plan:
        e.plan = data.plan
    session.add(e)
    session.commit()
    return _vue(session, e)


@router.patch("/ecoles/{ecole_id}", response_model=EcoleAdmin)
def corriger(ecole_id: int, data: CorrectionAbonnement, admin: SuperAdmin = Depends(superadmin_courant),
             session: Session = Depends(get_session)):
    """Corriger la date de fin, le statut (essai/actif) ou le plan, sans paiement."""
    e = _ecole(session, ecole_id)
    if data.fin is not None:
        e.abonnement_fin = data.fin
    if data.statut:
        e.abonnement_statut = data.statut
    if data.plan is not None:
        e.plan = data.plan or None
    session.add(e)
    session.commit()
    return _vue(session, e)


@router.post("/ecoles/{ecole_id}/suspendre", response_model=EcoleAdmin)
def suspendre(ecole_id: int, data: Suspension, admin: SuperAdmin = Depends(superadmin_courant),
              session: Session = Depends(get_session)):
    e = _ecole(session, ecole_id)
    e.abonnement_statut, e.motif_suspension = "suspendu", data.motif.strip()
    session.add(e)
    session.commit()
    return _vue(session, e)


@router.post("/ecoles/{ecole_id}/reactiver", response_model=EcoleAdmin)
def reactiver(ecole_id: int, admin: SuperAdmin = Depends(superadmin_courant), session: Session = Depends(get_session)):
    e = _ecole(session, ecole_id)
    a_paye = session.exec(select(PaiementAbonnement.id).where(PaiementAbonnement.ecole_id == e.id).limit(1)).first()
    e.abonnement_statut, e.motif_suspension = ("actif" if a_paye else "essai"), None
    session.add(e)
    session.commit()
    return _vue(session, e)


@router.post("/ecoles/{ecole_id}/directeur/mot-de-passe", status_code=204)
def reinitialiser_directeur(ecole_id: int, data: ReinitialisationMotDePasse,
                            admin: SuperAdmin = Depends(superadmin_courant), session: Session = Depends(get_session)):
    """Assistance : le directeur a oublié son mot de passe."""
    _ecole(session, ecole_id)
    directeur = session.exec(select(Utilisateur).where(Utilisateur.ecole_id == ecole_id,
                                                       Utilisateur.role == Role.directeur)
                             .order_by(Utilisateur.id)).first()
    if not directeur:
        raise HTTPException(404, "Aucun directeur dans cette école")
    directeur.mot_de_passe_hash = hacher_mot_de_passe(data.nouveau)
    directeur.actif = True
    session.add(directeur)
    session.commit()
