"""Aide au comptable et au directeur : guichet (familles, plusieurs frais en un paiement), tiers payeurs,
délais de paiement, promesses, relances, clôture de caisse, alertes, prévision de trésorerie,
rapprochement avec les relevés Wave / Orange Money."""
import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    Alerte, ARelancer, Classe, ClotureCaisse, ClotureDetail, ClotureSaisie, Depense, Ecole, Eleve, MessageEnvoye,
    MoisPrevision, MoratoireSaisie, Paiement, PaiementGroupe, PriseEnCharge, Promesse, PromesseSaisie, Rapprochement,
    LigneRapprochement, ResultatGroupe, Role, Solde, Tiers, TiersSaisie, Utilisateur,
)
from app.notifications import envoyer_sms, sms_disponible
from app.pdf import en_tete_pdf, pdf_recu_groupe, pdf_releve_tiers
from app.security import GESTION, exiger_role
from app.services import (
    calculer_solde, chiffres_telephone, creer_paiement, freres_et_soeurs, get_eleve, mensualites,
    soldes_ecole, tranches_annee,
)

router = APIRouter(tags=["Guichet et contrôle"])


def argent(n: int, devise: str) -> str:
    return f"{n:,}".replace(",", " ") + f" {devise}"


# ---------- Alertes au directeur ----------
def alerter(session: Session, ecole_id: int, type_: str, message: str) -> None:
    """Note un événement sensible pour le directeur (et lui envoie un SMS si c'est possible). Sans commit."""
    session.add(Alerte(ecole_id=ecole_id, type=type_, message=message))
    if sms_disponible():
        from app.routers.messages import numero_international
        ecole = session.get(Ecole, ecole_id)
        for d in session.exec(select(Utilisateur).where(Utilisateur.ecole_id == ecole_id, Utilisateur.role == Role.directeur,
                                                        Utilisateur.actif == True)).all():  # noqa: E712
            num = numero_international(d.telephone, ecole.indicatif_tel)
            if num:
                envoyer_sms(num, f"{ecole.nom} : {message}")


@router.get("/alertes", response_model=list[Alerte])
def lister_alertes(toutes: bool = False, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                   session: Session = Depends(get_session)):
    stmt = select(Alerte).where(Alerte.ecole_id == user.ecole_id)
    if not toutes:
        stmt = stmt.where(Alerte.lue == False)  # noqa: E712
    return session.exec(stmt.order_by(Alerte.cree_le.desc()).limit(200)).all()


@router.post("/alertes/lues", status_code=204)
def marquer_lues(user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    for a in session.exec(select(Alerte).where(Alerte.ecole_id == user.ecole_id, Alerte.lue == False)).all():  # noqa: E712
        a.lue = True
        session.add(a)
    session.commit()


# ---------- Guichet : trouver un élève et sa famille ----------
@router.get("/guichet/recherche", response_model=list[Solde])
def rechercher(q: str, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Nom, prénom, matricule ou téléphone du parent (les 20 premiers)."""
    q = q.strip().lower()
    if len(q) < 2:
        return []
    chiffres = re.sub(r"\D", "", q)
    tel = chiffres[-8:] if len(chiffres) >= 6 else ""
    trouves = []
    for e in session.exec(select(Eleve).where(Eleve.ecole_id == user.ecole_id, Eleve.actif == True)  # noqa: E712
                          .order_by(Eleve.nom, Eleve.prenom)).all():
        texte = f"{e.nom} {e.prenom} {e.prenom} {e.nom} {e.matricule or ''}".lower()
        if all(m in texte for m in q.split()) or (tel and tel in re.sub(r"\D", "", e.telephone_parent or "")):
            trouves.append(e)
        if len(trouves) >= 20:
            break
    tranches = tranches_annee(session, user.ecole_id)
    return [calculer_solde(session, e, tranches) for e in trouves]


@router.get("/eleves/{eleve_id}/famille", response_model=list[Solde])
def famille(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """L'élève et ses frères et sœurs inscrits (même téléphone du parent) : l'élève demandé en premier."""
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    tranches = tranches_annee(session, user.ecole_id)
    membres = [eleve] + [e for e in freres_et_soeurs(session, eleve) if e.id != eleve.id]
    return [calculer_solde(session, e, tranches) for e in membres]


# ---------- Plusieurs paiements en une fois (un seul reçu) ----------
@router.post("/paiements/groupe", response_model=ResultatGroupe, status_code=201)
def payer_groupe(data: PaiementGroupe, user: Utilisateur = Depends(exiger_role(*GESTION)),
                 session: Session = Depends(get_session)):
    """Ex : novembre et la tenue d'Awa + la cantine de Modou. Tout ou rien ; un reçu pour l'ensemble."""
    if data.cle_client:   # renvoi après une coupure réseau : on ne paie pas deux fois
        deja = session.exec(select(Paiement).where(Paiement.cle_client == data.cle_client)).all()
        if deja and get_eleve(session, deja[0].eleve_id, user.ecole_id):
            return _resultat(session, deja[0].groupe or "", deja, user)
    groupe = uuid.uuid4().hex[:12]
    crees = []
    for l in data.lignes:
        eleve = get_eleve(session, l.eleve_id, user.ecole_id, code=400)
        if not eleve.actif:
            raise HTTPException(400, f"{eleve.prenom} {eleve.nom} ne fait plus partie de l'école")
        p = creer_paiement(session, eleve, l.montant, data.mode, data.reference, data.date_paiement, user.id,
                           l.type_frais_id, l.tiers_id, groupe)
        p.cle_client = data.cle_client
        crees.append(p)
    session.commit()
    for p in crees:
        session.refresh(p)
    return _resultat(session, groupe, crees, user)


def _resultat(session: Session, groupe: str, paiements: list[Paiement], user: Utilisateur) -> ResultatGroupe:
    from app.routers.messages import numero_international
    from app.routers.paiements import nom_frais
    ecole = session.get(Ecole, user.ecole_id)
    total = sum(p.montant for p in paiements)
    eleves = {p.eleve_id: session.get(Eleve, p.eleve_id) for p in paiements}
    details = "; ".join(f"{eleves[p.eleve_id].prenom} – {nom_frais(session, p.type_frais_id)} : {argent(p.montant, ecole.devise)}"
                        for p in paiements)
    premier = next(iter(eleves.values()))
    restes = [(e.prenom, calculer_solde(session, e).reste_a_payer) for e in eleves.values()]
    texte = (f"{ecole.nom} : nous avons bien reçu {argent(total, ecole.devise)} le {paiements[0].date_paiement:%d/%m/%Y} "
             f"({details}). Reçu n° {paiements[0].numero_recu}. Reste à payer : "
             + ", ".join(f"{n} {argent(r, ecole.devise)}" for n, r in restes) + ". Merci !")
    num = numero_international(premier.telephone_parent, ecole.indicatif_tel)
    return ResultatGroupe(groupe=groupe, total=total, paiements=paiements, message_parent=texte,
                          lien_whatsapp=f"https://wa.me/{num}?text={quote(texte)}" if num else None)


@router.get("/paiements/groupe/{groupe}/recu.pdf", response_class=Response)
def recu_groupe(groupe: str, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    from app.routers.paiements import nom_frais
    paiements = session.exec(select(Paiement).where(Paiement.groupe == groupe).order_by(Paiement.id)).all()
    if not paiements:
        raise HTTPException(404, "Reçu introuvable")
    lignes = []
    for p in paiements:
        e = get_eleve(session, p.eleve_id, user.ecole_id)
        lignes.append((p, e, session.get(Classe, e.classe_id),
                       nom_frais(session, p.type_frais_id) + (" (part tiers)" if p.tiers_id else ""), calculer_solde(session, e)))
    caissier = session.get(Utilisateur, paiements[0].enregistre_par_id) if paiements[0].enregistre_par_id else None
    pdf = pdf_recu_groupe(session.get(Ecole, user.ecole_id), lignes, caissier.nom if caissier else None)
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"recu_{paiements[0].numero_recu}.pdf"))


# ---------- Tiers payeurs (ONG, entreprise, mairie…) ----------
@router.get("/tiers", response_model=list[Tiers])
def lister_tiers(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    return session.exec(select(Tiers).where(Tiers.ecole_id == user.ecole_id).order_by(Tiers.nom)).all()


@router.post("/tiers", response_model=Tiers, status_code=201)
def creer_tiers(data: TiersSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                session: Session = Depends(get_session)):
    t = Tiers(ecole_id=user.ecole_id, **data.model_dump())
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _tiers(session: Session, tiers_id: int, ecole_id: int) -> Tiers:
    t = session.get(Tiers, tiers_id)
    if not t or t.ecole_id != ecole_id:
        raise HTTPException(404, "Organisme introuvable")
    return t


@router.put("/eleves/{eleve_id}/prise-en-charge", response_model=Solde)
def prise_en_charge(eleve_id: int, data: PriseEnCharge, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                    session: Session = Depends(get_session)):
    """Une part de la scolarité est payée par un organisme : elle n'est plus demandée au parent."""
    e = get_eleve(session, eleve_id, user.ecole_id)
    if data.tiers_id and data.pourcentage:
        t = _tiers(session, data.tiers_id, user.ecole_id)
        e.tiers_id, e.tiers_pct = t.id, data.pourcentage
        alerter(session, user.ecole_id, "prise_en_charge",
                f"{data.pourcentage} % de la scolarité de {e.prenom} {e.nom} pris en charge par {t.nom} ({user.nom})")
    else:
        e.tiers_id, e.tiers_pct = None, 0
    session.add(e)
    session.commit()
    return calculer_solde(session, e)


def _releve(session: Session, t: Tiers) -> list:
    lignes = []
    for e in session.exec(select(Eleve).where(Eleve.tiers_id == t.id, Eleve.actif == True)  # noqa: E712
                          .order_by(Eleve.nom, Eleve.prenom)).all():
        s = calculer_solde(session, e)
        l = next((x for x in s.lignes if x.tiers_id), None)
        if l:
            lignes.append((f"{e.prenom} {e.nom}", s.classe, l.du, l.paye))
    return lignes


@router.get("/tiers/{tiers_id}/releve")
def releve_tiers(tiers_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    t = _tiers(session, tiers_id, user.ecole_id)
    lignes = _releve(session, t)
    return {"tiers": t.nom, "eleves": [{"eleve": e, "classe": c, "du": du, "paye": p} for e, c, du, p in lignes],
            "du": sum(x[2] for x in lignes), "paye": sum(x[3] for x in lignes)}


@router.get("/tiers/{tiers_id}/releve.pdf", response_class=Response)
def releve_tiers_pdf(tiers_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                     session: Session = Depends(get_session)):
    t = _tiers(session, tiers_id, user.ecole_id)
    pdf = pdf_releve_tiers(session.get(Ecole, user.ecole_id), t, _releve(session, t))
    return Response(pdf, media_type="application/pdf", headers=en_tete_pdf(f"releve_{t.nom}.pdf"))


# ---------- Délai de paiement accordé (moratoire) ----------
@router.put("/eleves/{eleve_id}/moratoire", response_model=Solde)
def moratoire(eleve_id: int, data: MoratoireSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
              session: Session = Depends(get_session)):
    """La famille a jusqu'à cette date pour régulariser : elle n'apparaît pas « en retard » et n'est pas relancée."""
    e = get_eleve(session, eleve_id, user.ecole_id)
    e.moratoire_jusquau = data.jusquau
    e.moratoire_motif = (data.motif or "").strip() or None if data.jusquau else None
    if data.jusquau:
        if data.jusquau < date.today():
            raise HTTPException(400, "La date doit être dans le futur")
        alerter(session, user.ecole_id, "moratoire",
                f"Délai accordé à {e.prenom} {e.nom} jusqu'au {data.jusquau:%d/%m/%Y} ({user.nom})")
    session.add(e)
    session.commit()
    return calculer_solde(session, e)


# ---------- Promesses de paiement ----------
@router.post("/promesses", response_model=Promesse, status_code=201)
def promettre(data: PromesseSaisie, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    e = get_eleve(session, data.eleve_id, user.ecole_id, code=400)
    p = Promesse(ecole_id=user.ecole_id, eleve_id=e.id, date_promise=data.date_promise, montant=data.montant,
                 note=(data.note or "").strip() or None, par_id=user.id)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.get("/promesses", response_model=list[Promesse])
def lister_promesses(eleve_id: int | None = None, user: Utilisateur = Depends(exiger_role(*GESTION)),
                     session: Session = Depends(get_session)):
    stmt = select(Promesse).where(Promesse.ecole_id == user.ecole_id, Promesse.soldee == False)  # noqa: E712
    if eleve_id:
        stmt = stmt.where(Promesse.eleve_id == eleve_id)
    return session.exec(stmt.order_by(Promesse.date_promise)).all()


@router.delete("/promesses/{promesse_id}", status_code=204)
def supprimer_promesse(promesse_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                       session: Session = Depends(get_session)):
    p = session.get(Promesse, promesse_id)
    if not p or p.ecole_id != user.ecole_id:
        raise HTTPException(404, "Promesse introuvable")
    p.soldee = True
    session.add(p)
    session.commit()


# ---------- Qui relancer aujourd'hui ----------
def a_relancer(session: Session, ecole_id: int, jour: date | None = None) -> list[ARelancer]:
    """Rappel quelques jours avant l'échéance, rappel après une échéance non payée, promesses arrivées à terme.
    Un seul message par famille (même téléphone du parent)."""
    from app.routers.messages import numero_international
    jour = jour or date.today()
    ecole = session.get(Ecole, ecole_id)
    d = lambda n: argent(n, ecole.devise)  # noqa: E731
    recents = {eid for (eid,) in session.exec(select(MessageEnvoye.eleve_id).where(
        MessageEnvoye.ecole_id == ecole_id, MessageEnvoye.type == "relance",
        MessageEnvoye.date >= datetime.now(timezone.utc) - timedelta(days=6))).all()}
    promesses = {}
    for p in session.exec(select(Promesse).where(Promesse.ecole_id == ecole_id, Promesse.soldee == False,  # noqa: E712
                                                 Promesse.date_promise <= jour)).all():
        promesses[p.eleve_id] = p
    familles: dict[str, list] = {}
    for s in soldes_ecole(session, ecole_id):
        if s.moratoire_jusquau or s.reste_a_payer <= 0:
            continue
        raison, detail, montant = None, "", 0
        p = promesses.get(s.eleve_id)
        if p and s.retard > 0:
            raison, montant = "promesse", p.montant or s.retard
            detail = f"vous aviez prévu de payer {d(montant)} le {p.date_promise:%d/%m/%Y}"
        elif s.retard > 0:
            echues = [e for e in s.echeances if e.statut == "en_retard"]
            premiere = echues[0].date_limite if echues else None
            if premiere is None or (jour - premiere).days >= ecole.relance_jours_apres:
                raison, montant = "retard", s.retard
                detail = ("non payé : " + ", ".join(e.nom for e in echues) if echues else "frais non payés") + f" ({d(s.retard)})"
        elif s.prochaine_echeance and 0 <= (s.prochaine_echeance - jour).days <= ecole.relance_jours_avant:
            raison, montant = "avant_echeance", s.prochain_montant
            prochaine = next(e for e in s.echeances if e.date_limite == s.prochaine_echeance)
            detail = f"{prochaine.nom} ({d(s.prochain_montant)}) à payer avant le {s.prochaine_echeance:%d/%m/%Y}"
        if raison:
            cle = chiffres_telephone(s.telephone_parent) or f"eleve-{s.eleve_id}"
            familles.setdefault(cle, []).append((s, raison, detail, montant))
    sortie = []
    for membres in familles.values():
        s0 = membres[0][0]
        raison = next((r for _, r, _, _ in membres if r == "promesse"), None) or \
            next((r for _, r, _, _ in membres if r == "retard"), None) or "avant_echeance"
        total = sum(m for *_, m in membres)
        noms = " et ".join(f"{s.nom_complet.split(' ')[0]} ({s.classe})" for s, *_ in membres)
        corps = " ; ".join(f"{s.nom_complet.split(' ')[0]} : {det}" for s, _, det, _ in membres)
        intro = {"avant_echeance": "petit rappel", "retard": "rappel de paiement", "promesse": "rappel"}[raison]
        texte = (f"Bonjour, {intro} de {ecole.nom} pour {noms} — {corps}. Total : {d(total)}. "
                 f"Merci. Contact : {ecole.telephone or 'l’école'}.")
        num = numero_international(s0.telephone_parent, ecole.indicatif_tel)
        sortie.append(ARelancer(eleve_id=s0.eleve_id, eleve=noms, classe=s0.classe, telephone=s0.telephone_parent,
                                raison=raison, detail=corps, montant=total, texte=texte,
                                lien_whatsapp=f"https://wa.me/{num}?text={quote(texte)}" if num else None,
                                deja_relance=any(s.eleve_id in recents for s, *_ in membres)))
    ordre = {"promesse": 0, "retard": 1, "avant_echeance": 2}
    return sorted(sortie, key=lambda r: (r.deja_relance, ordre[r.raison], -r.montant))


@router.get("/relances", response_model=list[ARelancer])
def relances(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    return a_relancer(session, user.ecole_id)


def envoyer_relances(session: Session, ecole_id: int, utilisateur_id: int) -> tuple[int, int]:
    """Envoie par SMS les relances du jour pas encore faites. Renvoie (envoyés, échecs)."""
    from app.routers.messages import numero_international
    ecole = session.get(Ecole, ecole_id)
    ok = ko = 0
    for r in a_relancer(session, ecole_id):
        if r.deja_relance:
            continue
        num = numero_international(r.telephone, ecole.indicatif_tel)
        if not num:
            continue
        if envoyer_sms(num, r.texte)[0]:
            ok += 1
            session.add(MessageEnvoye(ecole_id=ecole_id, eleve_id=r.eleve_id, type="relance", canal="sms",
                                      utilisateur_id=utilisateur_id))
        else:
            ko += 1
    session.commit()
    return ok, ko


@router.post("/relances/envoyer")
def envoyer(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    if not sms_disponible():
        raise HTTPException(400, "Aucun fournisseur de SMS configuré : utilise les boutons WhatsApp")
    ok, ko = envoyer_relances(session, user.ecole_id, user.id)
    return {"envoyes": ok, "echecs": ko}


def relances_automatiques(session: Session) -> int:
    """Chaque jour : les écoles qui l'ont demandé envoient seules leurs relances par SMS."""
    if not sms_disponible():
        return 0
    total = 0
    for ecole in session.exec(select(Ecole).where(Ecole.relances_auto == True)).all():  # noqa: E712
        d = session.exec(select(Utilisateur).where(Utilisateur.ecole_id == ecole.id, Utilisateur.role == Role.directeur)).first()
        if d:
            total += envoyer_relances(session, ecole.id, d.id)[0]
    return total


# ---------- Clôture de caisse (contrôle du soir) ----------
def _especes_du_jour(session: Session, ecole_id: int, jour: date) -> int:
    from app.routers.caisse import caisse_du_jour
    class _U:   # appel interne de la fonction de la route
        pass
    u = _U()
    u.ecole_id = ecole_id
    return caisse_du_jour(jour, u, session).par_mode.get("especes", 0)


def _details_clotures(session: Session, clotures: list[ClotureCaisse]) -> list[ClotureDetail]:
    noms = {u.id: u.nom for u in session.exec(select(Utilisateur).where(Utilisateur.id.in_(
        {c.par_id for c in clotures} | {c.valide_par_id for c in clotures if c.valide_par_id})))} if clotures else {}
    return [ClotureDetail(**c.model_dump(exclude={"par_id", "valide_par_id", "ecole_id"}), par=noms.get(c.par_id, "?"),
                          valide_par=noms.get(c.valide_par_id)) for c in clotures]


@router.post("/caisse/cloture", response_model=ClotureDetail, status_code=201)
def cloturer(data: ClotureSaisie, user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Le caissier saisit l'argent liquide compté. L'écart avec ce que le logiciel attend est noté
    (et signalé au directeur s'il n'est pas nul)."""
    jour = data.jour or date.today()
    if session.exec(select(ClotureCaisse).where(ClotureCaisse.ecole_id == user.ecole_id, ClotureCaisse.jour == jour)).first():
        raise HTTPException(400, "La caisse de ce jour est déjà clôturée")
    attendu = _especes_du_jour(session, user.ecole_id, jour) - data.verse_banque
    c = ClotureCaisse(ecole_id=user.ecole_id, jour=jour, attendu=attendu, compte=data.compte, ecart=data.compte - attendu,
                      verse_banque=data.verse_banque, commentaire=(data.commentaire or "").strip() or None, par_id=user.id)
    session.add(c)
    if c.ecart:
        ecole = session.get(Ecole, user.ecole_id)
        alerter(session, user.ecole_id, "ecart_caisse",
                f"Caisse du {jour:%d/%m/%Y} : écart de {'+' if c.ecart > 0 else ''}{argent(c.ecart, ecole.devise)} ({user.nom})")
    session.commit()
    session.refresh(c)
    return _details_clotures(session, [c])[0]


@router.get("/caisse/clotures", response_model=list[ClotureDetail])
def lister_clotures(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    return _details_clotures(session, session.exec(select(ClotureCaisse).where(ClotureCaisse.ecole_id == user.ecole_id)
                                                   .order_by(ClotureCaisse.jour.desc()).limit(60)).all())


@router.post("/caisse/clotures/{cloture_id}/valider", response_model=ClotureDetail)
def valider_cloture(cloture_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                    session: Session = Depends(get_session)):
    c = session.get(ClotureCaisse, cloture_id)
    if not c or c.ecole_id != user.ecole_id:
        raise HTTPException(404, "Clôture introuvable")
    c.valide_par_id, c.valide_le = user.id, datetime.now(timezone.utc)
    session.add(c)
    session.commit()
    session.refresh(c)
    return _details_clotures(session, [c])[0]


# ---------- Prévision de trésorerie ----------
@router.get("/caisse/prevision", response_model=list[MoisPrevision])
def prevision(user: Utilisateur = Depends(exiger_role(*GESTION)), session: Session = Depends(get_session)):
    """Pour chaque mois restant de l'année : argent attendu des familles, salaires, autres dépenses, trésorerie prévue."""
    from app.routers.caisse import bilan
    from app.routers.emploi import heures_prevues
    from app.routers.paie import _personnel, poste_de, postes_ecole
    ecole = session.get(Ecole, user.ecole_id)
    mois = [m.date_limite.strftime("%Y-%m") for m in mensualites(ecole)]
    courant = date.today().strftime("%Y-%m")
    mois = [m for m in mois if m >= courant]
    attendu = {m: 0 for m in mois}
    for s in soldes_ecole(session, user.ecole_id):
        for e in s.echeances:
            k = e.date_limite.strftime("%Y-%m")
            if e.reste and k in attendu:
                attendu[k] += e.reste
        retard = s.retard_scolarite + s.retard_autres     # le retard est espéré le mois en cours
        if mois:
            attendu[mois[0]] += retard
        for l in s.lignes:
            if l.mensuel and l.type_frais_id is not None:
                for k in mois[1:]:
                    attendu[k] += l.montant_mois
    postes = postes_ecole(session, user.ecole_id)
    gens = _personnel(session, user.ecole_id)
    def salaires(m: str) -> int:
        total = 0
        for u in gens:
            p = poste_de(u, postes)
            if p and p.montant:
                total += p.montant if p.type == "mensuel" else round(p.montant * heures_prevues(session, user.ecole_id, u.id, m))
        return total
    b = bilan(session, user.ecole_id)
    passes = [x for x in b.mois if x.mois < courant]
    autres = session.exec(select(Depense).where(Depense.ecole_id == user.ecole_id, Depense.annule == False,  # noqa: E712
                                                Depense.annee_scolaire == ecole.annee_scolaire)).all()
    hors_salaires = sum(d.montant for d in autres if d.categorie.value != "salaires")
    moyenne = round(hors_salaires / max(len(passes) + 1, 1))
    tresorerie, sortie = b.solde, []
    for m in mois:
        sal = salaires(m)
        tresorerie += attendu[m] - sal - moyenne
        sortie.append(MoisPrevision(mois=m, attendu=attendu[m], salaires=sal, autres_depenses=moyenne, solde_prevu=tresorerie))
    return sortie


# ---------- Rapprochement avec le relevé Wave / Orange Money ----------
_COLS = {"montant": ("montant", "amount", "somme", "valeur", "credit", "crédit"),
         "reference": ("reference", "référence", "transaction", "id", "ref", "numéro", "numero"),
         "date": ("date", "jour", "heure")}


def _lire_releve(contenu: bytes, nom: str) -> list[dict]:
    if nom.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True).active
        rangees = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        texte = contenu.decode("utf-8-sig", errors="replace")
        sep = ";" if texte.count(";") > texte.count(",") else ","
        rangees = list(csv.reader(io.StringIO(texte), delimiter=sep))
    entete_i = next((i for i, r in enumerate(rangees[:15]) if r and any(
        str(c or "").strip().lower().startswith(_COLS["montant"]) for c in r)), None)
    if entete_i is None:
        raise HTTPException(400, "Colonne « Montant » introuvable dans le relevé")
    entete = [str(c or "").strip().lower() for c in rangees[entete_i]]
    def col(cle):
        return next((i for i, h in enumerate(entete) if h.startswith(_COLS[cle]) or any(k in h for k in _COLS[cle][:3])), None)
    im, ir, idt = col("montant"), col("reference"), col("date")
    lignes = []
    for r in rangees[entete_i + 1:]:
        if not r or im is None or im >= len(r) or r[im] in (None, ""):
            continue
        brut = str(r[im]).replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            montant = round(abs(float(re.sub(r"[^0-9.\-]", "", brut))))
        except ValueError:
            continue
        if not montant:
            continue
        jour = None
        if idt is not None and idt < len(r) and r[idt]:
            v = r[idt]
            if isinstance(v, datetime):
                jour = v.date()
            elif isinstance(v, date):
                jour = v
            else:
                m = re.search(r"(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})", str(v))
                if m:
                    a, b, c = (int(x) for x in m.groups())
                    try:
                        jour = date(a, b, c) if a > 31 else date(c if c > 99 else 2000 + c, b, a)
                    except ValueError:
                        jour = None
        ref = str(r[ir]).strip() if ir is not None and ir < len(r) and r[ir] not in (None, "") else None
        lignes.append({"montant": montant, "reference": ref, "date": jour})
    return lignes


@router.post("/caisse/rapprochement", response_model=Rapprochement)
async def rapprocher(fichier: UploadFile = File(...), mode: str = Form("wave"), du: date | None = Form(None),
                     au: date | None = Form(None), user: Utilisateur = Depends(exiger_role(*GESTION)),
                     session: Session = Depends(get_session)):
    """Compare le relevé du compte marchand (Excel ou CSV) aux paiements saisis dans le logiciel avec ce mode :
    paiements absents du logiciel (pas saisis), absents du relevé (saisis mais pas reçus), montants différents."""
    contenu = await fichier.read()
    if len(contenu) > 5 * 1024 * 1024:
        raise HTTPException(400, "Fichier trop lourd (5 Mo maximum)")
    releve = _lire_releve(contenu, fichier.filename or "")
    jours = [l["date"] for l in releve if l["date"]]
    du = du or (min(jours) if jours else date.today() - timedelta(days=31))
    au = au or (max(jours) if jours else date.today())
    saisis = session.exec(select(Paiement, Eleve).join(Eleve, Paiement.eleve_id == Eleve.id).where(
        Eleve.ecole_id == user.ecole_id, Paiement.annule == False, Paiement.mode == mode,  # noqa: E712
        Paiement.date_paiement >= du, Paiement.date_paiement <= au)).all()
    norm = lambda r: re.sub(r"[^a-z0-9]", "", (r or "").lower())  # noqa: E731
    # Les paiements faits ensemble (même référence) comptent pour un seul mouvement
    par_ref: dict[str, list] = {}
    sans_ref = []
    for p, e in saisis:
        (par_ref.setdefault(norm(p.reference), []) if norm(p.reference) else sans_ref).append((p, e))
    sortie, vus = [], set()
    for l in releve:
        k = norm(l["reference"])
        groupe = par_ref.get(k) if k else None
        if groupe and k not in vus:
            vus.add(k)
            total = sum(p.montant for p, _ in groupe)
            noms = ", ".join(sorted({f"{e.prenom} {e.nom}" for _, e in groupe}))
            sortie.append(LigneRapprochement(statut="trouve" if total == l["montant"] else "montant_different",
                                             reference=l["reference"], date=l["date"], montant_releve=l["montant"],
                                             montant_logiciel=total, eleve=noms, paiement_id=groupe[0][0].id))
            continue
        if groupe and k in vus:
            sortie.append(LigneRapprochement(statut="doublon", reference=l["reference"], date=l["date"],
                                             montant_releve=l["montant"], montant_logiciel=None))
            continue
        # sans référence commune : même montant et même jour
        cand = next(((p, e) for p, e in sans_ref if p.montant == l["montant"] and p.date_paiement == l["date"]), None)
        if cand:
            sans_ref.remove(cand)
            sortie.append(LigneRapprochement(statut="trouve", reference=l["reference"], date=l["date"],
                                             montant_releve=l["montant"], montant_logiciel=cand[0].montant,
                                             eleve=f"{cand[1].prenom} {cand[1].nom}", paiement_id=cand[0].id))
        else:
            sortie.append(LigneRapprochement(statut="absent_logiciel", reference=l["reference"], date=l["date"],
                                             montant_releve=l["montant"], montant_logiciel=None))
    for k, groupe in par_ref.items():
        if k not in vus:
            p, e = groupe[0]
            sortie.append(LigneRapprochement(statut="absent_releve", reference=p.reference, date=p.date_paiement,
                                             montant_releve=None, montant_logiciel=sum(x.montant for x, _ in groupe),
                                             eleve=", ".join(sorted({f"{x.prenom} {x.nom}" for _, x in groupe})),
                                             paiement_id=p.id))
    for p, e in sans_ref:
        sortie.append(LigneRapprochement(statut="absent_releve", reference=p.reference, date=p.date_paiement,
                                         montant_releve=None, montant_logiciel=p.montant, eleve=f"{e.prenom} {e.nom}",
                                         paiement_id=p.id))
    ordre = {"absent_logiciel": 0, "montant_different": 1, "doublon": 2, "absent_releve": 3, "trouve": 4}
    sortie.sort(key=lambda l: (ordre[l.statut], l.date or date.min))
    return Rapprochement(lignes=sortie, trouves=sum(1 for l in sortie if l.statut == "trouve"),
                         absents_logiciel=sum(1 for l in sortie if l.statut == "absent_logiciel"),
                         absents_releve=sum(1 for l in sortie if l.statut == "absent_releve"),
                         differences=sum(1 for l in sortie if l.statut in ("montant_different", "doublon")))
