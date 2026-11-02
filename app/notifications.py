"""Envoi de SMS et d'e-mails.

Les identifiants viennent uniquement de l'environnement (.env) — jamais du code :

    SMS_FOURNISSEUR=orange            # orange / simulation / (vide = pas de SMS)
    ORANGE_CLIENT_ID=...              # compte développeur Orange (API « SMS »)
    ORANGE_CLIENT_SECRET=...
    ORANGE_EXPEDITEUR=+224620000000   # numéro d'envoi attribué par Orange
    ORANGE_NOM_EXPEDITEUR=MonEcole    # facultatif, si autorisé par Orange

    SMTP_HOTE=smtp.gmail.com          # e-mail (récupération de mot de passe)
    SMTP_PORT=587
    SMTP_UTILISATEUR=...
    SMTP_MOT_DE_PASSE=...             # mot de passe d'application, pas le mot de passe du compte
    SMTP_EXPEDITEUR=Mon École <ecole@exemple.com>

« simulation » n'envoie rien : les messages sont gardés en mémoire (démonstration, tests).
"""
import logging
import os
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import quote

import httpx

journal = logging.getLogger("gestion_ecole")
SIMULES: list[dict] = []          # messages « envoyés » en mode simulation
_JETON_ORANGE: dict = {"valeur": None, "expire": 0.0}


def _env(nom: str) -> str:
    return os.getenv(nom, "").strip()


def fournisseur_sms() -> str:
    f = _env("SMS_FOURNISSEUR").lower()
    if f == "orange" and _env("ORANGE_CLIENT_ID") and _env("ORANGE_CLIENT_SECRET") and _env("ORANGE_EXPEDITEUR"):
        return "orange"
    return "simulation" if f == "simulation" else ""


def sms_disponible() -> bool:
    return bool(fournisseur_sms())


def email_disponible() -> bool:
    return bool(_env("SMTP_HOTE") and _env("SMTP_EXPEDITEUR"))


def _jeton_orange(client: httpx.Client) -> str:
    if _JETON_ORANGE["valeur"] and time.time() < _JETON_ORANGE["expire"] - 60:
        return _JETON_ORANGE["valeur"]
    r = client.post("https://api.orange.com/oauth/v3/token", data={"grant_type": "client_credentials"},
                    auth=(_env("ORANGE_CLIENT_ID"), _env("ORANGE_CLIENT_SECRET")), timeout=20)
    r.raise_for_status()
    j = r.json()
    _JETON_ORANGE.update(valeur=j["access_token"], expire=time.time() + int(j.get("expires_in", 3600)))
    return j["access_token"]


def envoyer_sms(numero_international: str, texte: str, client: httpx.Client | None = None) -> tuple[bool, str | None]:
    """`numero_international` : chiffres seulement, avec indicatif (ex : 224620000000)."""
    f = fournisseur_sms()
    if not f:
        return False, "Aucun fournisseur de SMS configuré"
    if f == "simulation":
        SIMULES.append({"canal": "sms", "a": numero_international, "texte": texte})
        return True, None
    expediteur = "tel:+" + _env("ORANGE_EXPEDITEUR").lstrip("+")
    corps = {"outboundSMSMessageRequest": {
        "address": "tel:+" + numero_international, "senderAddress": expediteur,
        "outboundSMSTextMessage": {"message": texte[:640]}}}
    if _env("ORANGE_NOM_EXPEDITEUR"):
        corps["outboundSMSMessageRequest"]["senderName"] = _env("ORANGE_NOM_EXPEDITEUR")
    ferme = client is None
    client = client or httpx.Client()
    try:
        jeton = _jeton_orange(client)
        r = client.post(f"https://api.orange.com/smsmessaging/v1/outbound/{quote(expediteur, safe='')}/requests",
                        json=corps, headers={"Authorization": f"Bearer {jeton}"}, timeout=20)
        if r.status_code in (200, 201):
            return True, None
        return False, f"Orange a refusé l'envoi ({r.status_code})"
    except httpx.HTTPError as e:
        journal.warning("Envoi SMS impossible : %s", e)
        return False, "Le service SMS ne répond pas"
    finally:
        if ferme:
            client.close()


def envoyer_email(destinataire: str, sujet: str, texte: str) -> tuple[bool, str | None]:
    if not email_disponible():
        return False, "Aucune boîte d'envoi configurée"
    if _env("SMTP_HOTE") == "simulation":
        SIMULES.append({"canal": "email", "a": destinataire, "sujet": sujet, "texte": texte})
        return True, None
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = _env("SMTP_EXPEDITEUR"), destinataire, sujet
    msg.set_content(texte)
    try:
        with smtplib.SMTP(_env("SMTP_HOTE"), int(_env("SMTP_PORT") or 587), timeout=20) as s:
            s.starttls()
            if _env("SMTP_UTILISATEUR"):
                s.login(_env("SMTP_UTILISATEUR"), _env("SMTP_MOT_DE_PASSE"))
            s.send_message(msg)
        return True, None
    except (smtplib.SMTPException, OSError) as e:
        journal.warning("Envoi e-mail impossible : %s", e)
        return False, "La boîte d'envoi ne répond pas"
