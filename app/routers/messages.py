"""Messages aux parents : rappels d'impayés et bulletins, à envoyer par WhatsApp ou SMS.

Le logiciel prépare le texte et un lien qui ouvre WhatsApp (ou l'appli SMS) avec le message
déjà rempli : il suffit d'appuyer sur « Envoyer ». Aucun abonnement SMS n'est nécessaire.
"""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
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
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Un rappel pour chaque élève qui doit encore de l'argent."""
    if classe_id:
        get_classe(session, classe_id, user.ecole_id)
    ecole = session.get(Ecole, user.ecole_id)
    derniers = _derniers_envois(session, user.ecole_id, "impaye")
    messages = []
    for s in soldes_ecole(session, user.ecole_id, classe_id):
        if s.reste_a_payer <= 0:
            continue
        eleve = session.get(Eleve, s.eleve_id)
        texte = (f"Bonjour, {ecole.nom} vous rappelle qu'il reste {argent(s.reste_a_payer, ecole.devise)} "
                 f"à payer pour la scolarité de {eleve.prenom} {eleve.nom} ({s.classe}), "
                 f"sur {argent(s.frais_annuels, ecole.devise)} de frais annuels. "
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
