"""Messages aux parents : rappels d'impayés et bulletins, à envoyer par WhatsApp ou SMS.

Le logiciel prépare le texte et un lien qui ouvre WhatsApp (ou l'appli SMS) avec le message
déjà rempli : il suffit d'appuyer sur « Envoyer ». Aucun abonnement SMS n'est nécessaire.
"""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, func, select

from app.database import get_session
from app.models import Ecole, Eleve, JournalMessage, MessageEnvoye, MessageParent, Utilisateur
from app.security import GESTION, exiger_role
from app.services import bulletins_classe, get_classe, get_eleve, soldes_ecole

router = APIRouter(prefix="/messages", tags=["Messages aux parents"])


def numero_international(telephone: str | None, indicatif: str) -> str | None:
    """« +224 620 00 00 00 », « 620000000 », « 00224… » → « 224620000000 »."""
    if not telephone:
        return None
    chiffres = re.sub(r"\D", "", telephone)
    if telephone.strip().startswith("+"):
        return chiffres or None
    if chiffres.startswith("00"):
        return chiffres[2:] or None
    indicatif = re.sub(r"\D", "", indicatif or "")
    if indicatif and chiffres.startswith(indicatif) and len(chiffres) > 9:
        return chiffres
    chiffres = chiffres.lstrip("0")
    return (indicatif + chiffres) if chiffres else None


def argent(n: int, devise: str) -> str:
    return f"{n:,}".replace(",", " ") + f" {devise}"


def _derniers_envois(session: Session, ecole_id: int, type_: str) -> dict:
    return dict(session.exec(
        select(MessageEnvoye.eleve_id, func.max(MessageEnvoye.date))
        .where(MessageEnvoye.ecole_id == ecole_id, MessageEnvoye.type == type_)
        .group_by(MessageEnvoye.eleve_id)
    ).all())


def _message(ecole: Ecole, eleve: Eleve, classe_nom: str, texte: str, dernier) -> MessageParent:
    inter = numero_international(eleve.telephone_parent, ecole.indicatif_tel)
    return MessageParent(
        eleve_id=eleve.id, eleve=f"{eleve.prenom} {eleve.nom}", classe=classe_nom,
        telephone=eleve.telephone_parent, telephone_international=inter, texte=texte,
        lien_whatsapp=f"https://wa.me/{inter}?text={quote(texte)}" if inter else None,
        lien_sms=f"sms:+{inter}?body={quote(texte)}" if inter else None,
        dernier_envoi=dernier,
    )


@router.get("/impayes", response_model=list[MessageParent])
def rappels_impayes(
    classe_id: int | None = None,
    en_retard: bool = False,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Un rappel pour chaque élève qui doit encore de l'argent
    (`en_retard=true` : seulement ceux qui ont une tranche échue non réglée)."""
    if classe_id:
        get_classe(session, classe_id, user.ecole_id)
    ecole = session.get(Ecole, user.ecole_id)
    derniers = _derniers_envois(session, user.ecole_id, "impaye")
    messages = []
    for s in soldes_ecole(session, user.ecole_id, classe_id):
        if s.reste_a_payer <= 0 or (en_retard and s.retard <= 0):
            continue
        eleve = session.get(Eleve, s.eleve_id)
        echues = [e for e in s.echeances if e.statut == "en_retard"]
        if echues:
            noms = ", ".join(f"{e.nom} (échue le {e.date_limite:%d/%m/%Y})" for e in echues)
            detail = (f"la scolarité de {eleve.prenom} {eleve.nom} ({s.classe}) a un retard de "
                      f"{argent(s.retard, ecole.devise)} : {noms}. Reste total de l'année : "
                      f"{argent(s.reste_a_payer, ecole.devise)}.")
        else:
            detail = (f"il reste {argent(s.reste_a_payer, ecole.devise)} à payer pour la scolarité de "
                      f"{eleve.prenom} {eleve.nom} ({s.classe}), sur {argent(s.frais_annuels, ecole.devise)} "
                      f"de frais annuels." + (f" Prochaine échéance : {s.prochaine_echeance:%d/%m/%Y} "
                                              f"({argent(s.prochain_montant, ecole.devise)})."
                                              if s.prochaine_echeance else ""))
        texte = (f"Bonjour, {ecole.nom} vous informe que {detail} "
                 f"Merci de passer régulariser à l'école. Pour toute question : {ecole.telephone or 'contactez l’école'}.")
        messages.append(_message(ecole, eleve, s.classe, texte, derniers.get(eleve.id)))
    return messages


@router.get("/bulletins", response_model=list[MessageParent])
def annonces_bulletins(
    classe_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Un message par élève de la classe avec sa moyenne et son rang."""
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    classe = get_classe(session, classe_id, user.ecole_id)
    ecole = session.get(Ecole, user.ecole_id)
    derniers = _derniers_envois(session, user.ecole_id, "bulletin")
    messages = []
    for b in bulletins_classe(session, classe, trimestre):
        eleve = session.get(Eleve, b.eleve_id)
        t = f"{trimestre}{'er' if trimestre == 1 else 'e'} trimestre"
        if b.moyenne_generale is None:
            resultat = "aucune note n'a encore été saisie."
        else:
            rang = f"{b.rang}{'er' if b.rang == 1 else 'e'} sur {b.effectif}"
            resultat = (f"moyenne générale {b.moyenne_generale:.2f}/20, rang {rang}. "
                        f"Appréciation : {b.appreciation}.")
        absences = f" Absences : {b.absences}, retards : {b.retards}." if (b.absences or b.retards) else ""
        texte = (f"Bonjour, le bulletin du {t} de {eleve.prenom} {eleve.nom} ({classe.nom}) est disponible "
                 f"à {ecole.nom} : {resultat}{absences}")
        messages.append(_message(ecole, eleve, classe.nom, texte, derniers.get(eleve.id)))
    return messages


@router.get("/acces", response_model=list[MessageParent])
def acces_parents(
    classe_id: int,
    request: Request,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Un message par élève avec l'adresse de l'espace parents, le matricule et le code."""
    from app.routers.parents import code_de
    classe = get_classe(session, classe_id, user.ecole_id)
    ecole = session.get(Ecole, user.ecole_id)
    derniers = _derniers_envois(session, user.ecole_id, "acces")
    adresse = str(request.base_url).rstrip("/") + "/espace-parents"
    eleves = session.exec(select(Eleve).where(Eleve.classe_id == classe.id, Eleve.actif == True)  # noqa: E712
                          .order_by(Eleve.nom, Eleve.prenom)).all()
    messages = []
    for eleve in eleves:
        code = code_de(session, eleve)
        texte = (f"Bonjour, {ecole.nom} ouvre l'espace parents : suivez les notes, absences et paiements de "
                 f"{eleve.prenom} {eleve.nom} ({classe.nom}) sur votre téléphone. Adresse : {adresse} — "
                 f"Matricule : {eleve.matricule} — Code : {code}. Ce code est personnel, ne le partagez pas.")
        messages.append(_message(ecole, eleve, classe.nom, texte, derniers.get(eleve.id)))
    return messages


@router.post("/journal", status_code=204)
def noter_envoi(
    data: JournalMessage,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Garde la date du dernier message envoyé à la famille (affichée dans la liste)."""
    get_eleve(session, data.eleve_id, user.ecole_id)
    session.add(MessageEnvoye(ecole_id=user.ecole_id, eleve_id=data.eleve_id, type=data.type,
                              canal=data.canal, utilisateur_id=user.id))
    session.commit()
