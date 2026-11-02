"""Paie du personnel : contrats (fixe ou à l'heure), avances, fiches de paie, versement en caisse."""
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    Avance, AvanceSaisie, CategorieDepense, ContratPaie, ContratSaisie, Depense, Ecole, FicheDetail, FichePaie,
    FicheSaisie, LignePersonnel, PaiementFiche, Role, Utilisateur,
)
from app.pdf import en_tete_pdf, pdf_fiche_paie
from app.routers.emploi import heures_prevues
from app.security import GESTION, exiger_role
from app.services import annee_courante

router = APIRouter(prefix="/paie", tags=["Paie"])
ROLES = {"directeur": "Directeur", "comptable": "Comptable", "enseignant": "Enseignant", "surveillant": "Surveillant"}


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


# ---------- Contrats ----------
@router.put("/contrats/{utilisateur_id}", response_model=ContratPaie)
def definir_contrat(utilisateur_id: int, data: ContratSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                    session: Session = Depends(get_session)):
    """Salaire fixe par mois, ou taux par heure de cours."""
    _membre(session, utilisateur_id, user.ecole_id)
    c = session.exec(select(ContratPaie).where(ContratPaie.utilisateur_id == utilisateur_id)).first() \
        or ContratPaie(ecole_id=user.ecole_id, utilisateur_id=utilisateur_id, montant=0)
    c.type, c.montant = data.type, data.montant
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@router.get("/personnel", response_model=list[LignePersonnel])
def personnel(mois: str, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Tout le personnel actif, avec son contrat, ses heures prévues, ses avances et sa fiche du mois."""
    _mois(mois)
    lignes = []
    for u in session.exec(select(Utilisateur).where(Utilisateur.ecole_id == user.ecole_id, Utilisateur.actif == True)  # noqa: E712
                          .order_by(Utilisateur.nom)).all():
        c = session.exec(select(ContratPaie).where(ContratPaie.utilisateur_id == u.id)).first()
        f = session.exec(select(FichePaie).where(FichePaie.utilisateur_id == u.id, FichePaie.mois == mois)).first()
        lignes.append(LignePersonnel(
            utilisateur_id=u.id, nom=u.nom, role=ROLES.get(u.role.value, u.role.value),
            contrat_type=c.type if c else None, contrat_montant=c.montant if c else None,
            heures_prevues=heures_prevues(session, user.ecole_id, u.id, mois) if c and c.type == "horaire" else None,
            avances_en_cours=sum(a.montant for a in _avances_en_cours(session, u.id)),
            fiche=_detail(session, f) if f else None,
        ))
    return lignes


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
    c = session.exec(select(ContratPaie).where(ContratPaie.utilisateur_id == u.id)).first()
    if not c:
        raise HTTPException(400, f"Définis d'abord le salaire de {u.nom}")
    if session.exec(select(FichePaie).where(FichePaie.utilisateur_id == u.id, FichePaie.mois == mois)).first():
        raise HTTPException(400, f"La fiche de {mois} de {u.nom} existe déjà")
    if c.type == "horaire":
        heures = data.heures if data.heures is not None else heures_prevues(session, user.ecole_id, u.id, mois)
        brut = round(heures * c.montant)
    else:
        heures, brut = None, c.montant
    avant_avances = max(brut + data.primes - data.retenues, 0)
    avances = _avances_en_cours(session, u.id)
    deduites, a_deduire = [], 0
    for a in avances:   # on rembourse les avances dans l'ordre, sans dépasser le salaire
        if a_deduire + a.montant <= avant_avances:
            deduites.append(a)
            a_deduire += a.montant
    f = FichePaie(ecole_id=user.ecole_id, utilisateur_id=u.id, mois=mois, type=c.type, heures=heures, taux=c.montant,
                  brut=brut, primes=data.primes, retenues=data.retenues, avances_deduites=a_deduire,
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
