import io
from datetime import date

import httpx
from openpyxl import load_workbook
from PIL import Image

from tests.conftest import creer_compte, inscrire, toutes_matieres


def _png(couleur=(200, 0, 0, 255), taille=(300, 300)):
    b = io.BytesIO()
    Image.new("RGBA", taille, couleur).save(b, "PNG")
    return b.getvalue()


# ---------- Modifier, départ, classes ----------
def test_modifier_eleve_et_depart(client, dir_headers, setup):
    h = dir_headers
    classe, eleve = setup
    autre = client.post("/classes", headers=h, json={"nom": "5ème A", "niveau": "Collège", "frais_annuels": 300000}).json()
    e = client.patch(f"/eleves/{eleve['id']}", headers=h, json={"nom": "Diallo-Bah", "classe_id": autre["id"]}).json()
    assert e["nom"] == "Diallo-Bah" and e["classe_id"] == autre["id"]
    assert client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()["frais_annuels"] == 300000   # frais de la nouvelle classe

    assert client.post(f"/eleves/{eleve['id']}/depart", headers=h, json={"motif": "x"}).status_code == 422
    d = client.post(f"/eleves/{eleve['id']}/depart", headers=h, json={"motif": "Déménagement à Kankan"}).json()
    assert d["actif"] is False and d["motif_depart"] == "Déménagement à Kankan" and d["date_depart"] == str(date.today())
    assert client.get("/eleves", headers=h).json() == []
    assert client.get("/impayes", headers=h).json() == []
    assert client.get("/eleves?sortis=true", headers=h).json()[0]["motif_depart"] == "Déménagement à Kankan"
    assert client.post(f"/eleves/{eleve['id']}/depart", headers=h, json={"motif": "encore"}).status_code == 400
    assert client.post(f"/eleves/{eleve['id']}/retour", headers=h).json()["actif"] is True
    assert len(client.get("/eleves", headers=h).json()) == 1


def test_classes_modifier_supprimer(client, dir_headers, setup):
    h = dir_headers
    classe, _ = setup
    c = client.patch(f"/classes/{classe['id']}", headers=h, json={"nom": "6ème A", "niveau": "Collège", "frais_annuels": 450000}).json()
    assert c["frais_annuels"] == 450000
    assert client.delete(f"/classes/{classe['id']}", headers=h).status_code == 400          # a des élèves
    vide = client.post("/classes", headers=h, json={"nom": "Erreur", "niveau": "X", "frais_annuels": 1}).json()
    assert client.delete(f"/classes/{vide['id']}", headers=h).status_code == 204


# ---------- Types de frais ----------
def test_types_de_frais(client, dir_headers, setup):
    h = dir_headers
    classe, eleve = setup                        # scolarité 500 000, 50 000 payés
    ins = client.post("/frais", headers=h, json={"nom": "Inscription", "montant": 25000, "obligatoire": True}).json()
    can = client.post("/frais", headers=h, json={"nom": "Cantine", "montant": 90000, "obligatoire": False}).json()
    assert client.post("/frais", headers=h, json={"nom": "inscription", "montant": 1}).status_code == 400
    assert client.post("/frais", headers=h, json={"nom": "Scolarité", "montant": 1}).status_code == 400

    s = client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()
    assert [(l["nom"], l["du"], l["paye"]) for l in s["lignes"]] == [("Scolarité", 500000, 50000), ("Inscription", 25000, 0)]
    assert s["frais_annuels"] == 525000 and s["reste_a_payer"] == 475000

    # Cantine : seulement pour les élèves inscrits
    assert client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 1000,
                                                      "type_frais_id": can["id"]}).status_code == 400
    f = client.put(f"/eleves/{eleve['id']}/frais", headers=h, json=[can["id"]]).json()
    assert {x["nom"]: x["souscrit"] for x in f} == {"Inscription": True, "Cantine": True}
    assert client.put(f"/eleves/{eleve['id']}/frais", headers=h, json=[ins["id"]]).status_code == 400   # obligatoire
    client.put(f"/eleves/{eleve['id']}/frais", headers=h, json=[can["id"]])

    # Paiement par type, plafonné au reste de ce frais
    assert client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 25001,
                                                      "type_frais_id": ins["id"]}).status_code == 400
    p = client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 25000, "type_frais_id": ins["id"]}).json()
    client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 30000, "type_frais_id": can["id"]})
    s = client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()
    lignes = {l["nom"]: l for l in s["lignes"]}
    assert lignes["Inscription"]["reste"] == 0 and lignes["Cantine"]["reste"] == 60000
    assert s["total_paye"] == 105000 and s["reste_a_payer"] == 450000 + 60000
    hist = client.get(f"/eleves/{eleve['id']}/paiements", headers=h).json()
    assert [x["frais"] for x in hist] == ["Scolarité", "Inscription", "Cantine"]
    assert client.get(f"/paiements/{p['id']}/recu.pdf", headers=h).content.startswith(b"%PDF")
    ws = load_workbook(io.BytesIO(client.get("/export/paiements.xlsx", headers=h).content))["Paiements"]
    assert ws["G3"].value == "Inscription"

    # Inscription d'un élève en payant d'abord les frais d'inscription
    r = client.post("/eleves", headers=h, json={"nom": "Bah", "prenom": "Ali", "classe_id": classe["id"],
                                                "paiement": {"montant": 25000, "type_frais_id": ins["id"]}}).json()
    s2 = client.get(f"/eleves/{r['eleve']['id']}/solde", headers=h).json()
    assert {l["nom"]: l["reste"] for l in s2["lignes"]} == {"Scolarité": 500000, "Inscription": 0}

    # Retirer un frais déjà payé : il est désactivé, l'historique reste
    client.delete(f"/frais/{can['id']}", headers=h)
    assert [t["nom"] for t in client.get("/frais", headers=h).json()] == ["Inscription"]
    lignes = {l["nom"]: l for l in client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()["lignes"]}
    assert lignes["Cantine"]["paye"] == 30000 and lignes["Cantine"]["du"] == 0


# ---------- Logo, cachet, bulletin complet ----------
def test_logo_cachet_et_bulletin_complet(client, dir_headers, setup):
    h = dir_headers
    classe, a = setup
    b = inscrire(client, h, classe["id"], "Bah", "Ali")
    assert client.put("/ecole/logo", headers=h, files={"fichier": ("l.txt", b"rien", "text/plain")}).status_code == 400
    assert client.put("/ecole/logo", headers=h, files={"fichier": ("l.png", _png(), "image/png")}).status_code == 204
    assert client.put("/ecole/cachet", headers=h, files={"fichier": ("c.png", _png((0, 0, 200, 120)), "image/png")}).status_code == 204
    assert client.get("/ecole/logo", headers=h).content.startswith(b"\x89PNG")
    assert client.put("/ecole/autre", headers=h, files={"fichier": ("c.png", _png(), "image/png")}).status_code == 422

    m = {x["nom"]: x["id"] for x in client.get("/matieres", headers=h).json()}
    url = lambda e: f"/notes/grille?eleve_id={e['id']}&trimestre=1"
    client.put(url(a), headers=h, json=[{"matiere_id": m["Mathématiques"], "composition": 16, "appreciation": "Très bon trimestre"}])
    client.put(url(b), headers=h, json=[{"matiere_id": m["Mathématiques"], "composition": 10}])
    g = {c["matiere"]: c for c in client.get(url(a), headers=h).json()}
    assert g["Mathématiques"]["appreciation"] == "Très bon trimestre"

    r = client.put(f"/eleves/{a['id']}/avis-conseil?trimestre=1", headers=h, json={"texte": "Félicitations du conseil"}).json()
    maths = r["matieres"][0]
    assert (maths["moyenne_classe"], maths["min_classe"], maths["max_classe"], maths["rang_matiere"]) == (13, 10, 16, 1)
    assert maths["appreciation"] == "Très bon trimestre"
    assert r["avis_conseil"] == "Félicitations du conseil" and r["moyenne_classe"] == 13

    # Un enseignant écrit l'appréciation de sa matière, pas des autres
    prof = creer_compte(client, h, "enseignant", "p@test.gn", [{"matiere_id": m["Français"], "classe_id": classe["id"]}])
    assert client.put(url(a), headers=prof, json=[{"matiere_id": m["Français"], "appreciation": "Peut mieux faire"}]).status_code == 200
    assert client.put(url(a), headers=prof, json=[{"matiere_id": m["Mathématiques"], "appreciation": "x"}]).status_code == 403
    assert client.put(f"/eleves/{a['id']}/avis-conseil?trimestre=1", headers=prof, json={"texte": "x"}).status_code == 403
    # Effacer
    client.put(url(a), headers=h, json=[{"matiere_id": m["Mathématiques"], "appreciation": ""}])
    assert {c["matiere"]: c for c in client.get(url(a), headers=h).json()}["Mathématiques"]["appreciation"] is None

    # Tous les PDF passent avec logo et cachet
    p = client.get(f"/eleves/{a['id']}/paiements", headers=h).json()[0]
    for u in (f"/eleves/{a['id']}/bulletin.pdf?trimestre=1", f"/paiements/{p['id']}/recu.pdf",
              f"/classes/{classe['id']}/cartes.pdf", f"/classes/{classe['id']}/paiements.pdf"):
        r = client.get(u, headers=h)
        assert r.status_code == 200 and r.content.startswith(b"%PDF"), u
    assert client.delete("/ecole/logo", headers=h).status_code == 204


# ---------- Emploi du temps ----------
def test_emploi_du_temps(client, dir_headers, setup):
    h = dir_headers
    classe, _ = setup
    c2 = client.post("/classes", headers=h, json={"nom": "5ème A", "niveau": "Collège", "frais_annuels": 1}).json()
    m = {x["nom"]: x["id"] for x in client.get("/matieres", headers=h).json()}
    prof = creer_compte(client, h, "enseignant", "p@test.gn", [{"matiere_id": m["Mathématiques"], "classe_id": classe["id"]}])
    pid = next(u["id"] for u in client.get("/utilisateurs", headers=h).json() if u["email"] == "p@test.gn")
    cr = lambda **k: client.post("/creneaux", headers=h, json={"classe_id": classe["id"], "jour": 1, "debut": "08:00",
                                                               "fin": "10:00", "matiere_id": m["Mathématiques"], **k})
    assert cr(enseignant_id=pid, salle="A1").status_code == 201
    assert "déjà cours" in cr(debut="09:00", fin="11:00").json()["detail"]                              # même classe
    r = client.post("/creneaux", headers=h, json={"classe_id": c2["id"], "jour": 1, "debut": "09:30", "fin": "10:30",
                                                  "matiere_id": m["Français"], "enseignant_id": pid})
    assert r.status_code == 400 and "déjà en 6ème A" in r.json()["detail"]                              # même prof
    r = client.post("/creneaux", headers=h, json={"classe_id": c2["id"], "jour": 1, "debut": "08:30", "fin": "09:30",
                                                  "matiere_id": m["Français"], "salle": "a1"})
    assert r.status_code == 400 and "salle" in r.json()["detail"]                                       # même salle
    assert cr(jour=2, debut="10:00", fin="09:00").status_code == 400
    assert cr(jour=2, debut="8h00", fin="9h00").status_code == 400
    assert cr(jour=3, debut="8:00", fin="9:30", enseignant_id=pid).json()["debut"] == "08:00"           # heure normalisée

    edt = client.get(f"/emploi-du-temps?classe_id={classe['id']}", headers=h).json()
    assert [(c["jour"], c["debut"], c["matiere"]) for c in edt] == [(1, "08:00", "Mathématiques"), (3, "08:00", "Mathématiques")]
    assert len(client.get("/emploi-du-temps", headers=prof).json()) == 2          # l'enseignant voit le sien
    assert client.get(f"/emploi-du-temps.pdf?classe_id={classe['id']}", headers=h).content.startswith(b"%PDF")
    assert client.get("/emploi-du-temps.pdf", headers=prof).content.startswith(b"%PDF")
    assert client.post("/creneaux", headers=prof, json={"classe_id": classe["id"], "jour": 5, "debut": "08:00", "fin": "09:00",
                                                         "matiere_id": m["Mathématiques"]}).status_code == 403

    # Heures prévues d'un mois : novembre 2026 a 5 lundis et 4 mercredis → 5×2 h + 4×1,5 h = 16 h
    from app.routers.emploi import heures_prevues
    from app.database import get_session
    from app.main import app
    session = next(app.dependency_overrides[get_session]())
    assert heures_prevues(session, 1, pid, "2026-11") == 16


# ---------- Paie ----------
def test_paie(client, dir_headers, setup):
    h = dir_headers
    classe, _ = setup
    m = {x["nom"]: x["id"] for x in client.get("/matieres", headers=h).json()}
    creer_compte(client, h, "enseignant", "p@test.gn", [{"matiere_id": m["Mathématiques"], "classe_id": classe["id"]}])
    compta = creer_compte(client, h, "comptable", "c@test.gn")
    users = {u["email"]: u["id"] for u in client.get("/utilisateurs", headers=h).json()}
    prof, comp = users["p@test.gn"], users["c@test.gn"]
    client.post("/creneaux", headers=h, json={"classe_id": classe["id"], "jour": 1, "debut": "08:00", "fin": "10:00",
                                              "matiere_id": m["Mathématiques"], "enseignant_id": prof})

    assert client.post("/paie/fiches", headers=h, json={"utilisateur_id": prof, "mois": "2026-11"}).status_code == 400  # pas de contrat
    # Le directeur fixe un salaire par poste ; chacun prend le poste de son rôle
    postes = {p["nom"]: p for p in client.get("/paie/postes", headers=h).json()}
    assert set(postes) == {"Directeur", "Comptable", "Enseignant", "Surveillant"}
    assert postes["Enseignant"]["type"] == "horaire" and postes["Enseignant"]["effectif"] == 1
    client.patch(f"/paie/postes/{postes['Enseignant']['id']}", headers=h, json={"nom": "Enseignant", "type": "horaire", "montant": 5000})
    client.patch(f"/paie/postes/{postes['Comptable']['id']}", headers=h, json={"nom": "Comptable", "type": "mensuel", "montant": 1200000})
    assert client.patch(f"/paie/postes/{postes['Comptable']['id']}", headers=compta,
                        json={"nom": "Comptable", "type": "mensuel", "montant": 9}).status_code == 403
    assert client.delete(f"/paie/postes/{postes['Comptable']['id']}", headers=h).status_code == 400   # poste d'un rôle

    # Avance : sort de la caisse tout de suite
    client.post("/paie/avances", headers=compta, json={"utilisateur_id": comp, "montant": 200000, "motif": "Santé"})
    caisse = client.get("/caisse/bilan", headers=h).json()
    assert caisse["par_categorie"]["salaires"] == 200000

    pers = {p["nom"]: p for p in client.get("/paie/personnel?mois=2026-11", headers=h).json()}
    assert pers["Enseignant"]["heures_prevues"] == 10        # 5 lundis × 2 h
    assert pers["Comptable"]["avances_en_cours"] == 200000

    fp = client.post("/paie/fiches", headers=h, json={"utilisateur_id": prof, "mois": "2026-11"}).json()
    assert (fp["heures"], fp["brut"], fp["net"]) == (10, 50000, 50000)
    fp2 = client.post("/paie/fiches", headers=h, json={"utilisateur_id": prof, "mois": "2026-12", "heures": 12.5, "primes": 10000}).json()
    assert fp2["brut"] == 62500 and fp2["net"] == 72500
    fc = client.post("/paie/fiches", headers=h, json={"utilisateur_id": comp, "mois": "2026-11", "retenues": 50000}).json()
    assert (fc["brut"], fc["avances_deduites"], fc["net"]) == (1200000, 200000, 950000)
    assert client.post("/paie/fiches", headers=h, json={"utilisateur_id": comp, "mois": "2026-11"}).status_code == 400   # doublon
    assert client.post("/paie/fiches", headers=h, json={"utilisateur_id": comp, "mois": "2026-13"}).status_code == 400

    # Supprimer une fiche non payée : l'avance redevient à déduire
    assert client.delete(f"/paie/fiches/{fc['id']}", headers=h).status_code == 204
    assert {p["nom"]: p for p in client.get("/paie/personnel?mois=2026-11", headers=h).json()}["Comptable"]["avances_en_cours"] == 200000
    fc = client.post("/paie/fiches", headers=h, json={"utilisateur_id": comp, "mois": "2026-11"}).json()
    assert fc["net"] == 1000000

    # Payer : dépense « Salaires » dans la caisse, une seule fois
    r = client.post(f"/paie/fiches/{fc['id']}/payer", headers=compta, json={"mode": "virement"}).json()
    assert r["payee"] and r["paye_le"] == str(date.today())
    assert client.post(f"/paie/fiches/{fc['id']}/payer", headers=h, json={}).status_code == 400
    assert client.delete(f"/paie/fiches/{fc['id']}", headers=h).status_code == 400
    assert client.get("/caisse/bilan", headers=h).json()["par_categorie"]["salaires"] == 1200000
    assert client.get(f"/paie/fiches/{fc['id']}.pdf", headers=h).content.startswith(b"%PDF")
    assert client.get(f"/paie/fiches/{fp2['id']}.pdf", headers=h).content.startswith(b"%PDF")
    prof_h = client.post("/auth/token", data={"username": "p@test.gn", "password": "motdepasse123"}).json()["access_token"]
    assert client.get("/paie/personnel?mois=2026-11", headers={"Authorization": "Bearer " + prof_h}).status_code == 403


# ---------- SMS groupés ----------
def test_envoi_groupe_sms(client, dir_headers, setup, monkeypatch):
    from app import notifications
    h = dir_headers
    classe, eleve = setup
    inscrire(client, h, classe["id"], "Sow", "Awa")       # sans téléphone
    assert client.get("/messages/capacites", headers=h).json()["sms"] is False
    assert client.post("/messages/envoyer-groupe", headers=h, json={"type": "impaye"}).status_code == 400
    monkeypatch.setenv("SMS_FOURNISSEUR", "simulation")
    r = client.post("/messages/envoyer-groupe", headers=h, json={"type": "impaye"}).json()
    assert r == {"envoyes": 1, "sans_numero": 1, "echecs": []}
    assert notifications.SIMULES[0]["a"] == "224620000000" and "450 000" in notifications.SIMULES[0]["texte"]
    assert client.get("/messages/impayes", headers=h).json()[0]["dernier_envoi"]
    r = client.post("/messages/envoyer-groupe", headers=h, json={"type": "acces", "classe_id": classe["id"],
                                                                 "eleve_ids": [eleve["id"]]}).json()
    assert r["envoyes"] == 1 and "Code" in notifications.SIMULES[-1]["texte"]


def test_orange_sms_api(monkeypatch):
    """Échange avec l'API Orange simulé (jeton puis envoi)."""
    from app import notifications
    monkeypatch.setenv("SMS_FOURNISSEUR", "orange")
    monkeypatch.setenv("ORANGE_CLIENT_ID", "id")
    monkeypatch.setenv("ORANGE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ORANGE_EXPEDITEUR", "+224620000000")
    notifications._JETON_ORANGE.update(valeur=None, expire=0)
    appels = []

    def repondre(req: httpx.Request):
        appels.append(req)
        if req.url.path.endswith("/token"):
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "JETON", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer JETON"
        assert "tel%3A%2B224620000000" in str(req.url)
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(repondre)) as c:
        assert notifications.envoyer_sms("224621112233", "Bonjour", c) == (True, None)
        assert notifications.envoyer_sms("224621112233", "Encore", c) == (True, None)   # jeton réutilisé
    assert len(appels) == 3
    import json
    assert json.loads(appels[1].content)["outboundSMSMessageRequest"]["address"] == "tel:+224621112233"


# ---------- Mot de passe oublié ----------
def test_mot_de_passe_oublie(client, dir_headers, monkeypatch):
    from app import notifications
    monkeypatch.setenv("SMS_FOURNISSEUR", "simulation")
    assert client.get("/auth/recuperation").json() == {"sms": True, "email": False}
    client.patch("/auth/moi", headers=dir_headers, json={"telephone": "620 11 22 33"})
    # Adresse inconnue : même réponse, rien envoyé
    assert client.post("/auth/mot-de-passe-oublie", json={"email": "inconnu@test.gn"}).status_code == 202
    assert notifications.SIMULES == []
    client.post("/auth/mot-de-passe-oublie", json={"email": "directeur@test.gn"})
    sms = notifications.SIMULES[-1]
    assert sms["a"] == "224620112233"
    code = next(mot for mot in sms["texte"].split() if mot.isdigit() and len(mot) == 6)
    mauvais = "000000" if code != "000000" else "111111"
    assert client.post("/auth/reinitialiser", json={"email": "directeur@test.gn", "code": mauvais, "nouveau": "nouveau123"}).status_code == 400
    assert client.post("/auth/reinitialiser", json={"email": "directeur@test.gn", "code": code, "nouveau": "nouveau123"}).status_code == 204
    assert client.post("/auth/token", data={"username": "directeur@test.gn", "password": "nouveau123"}).status_code == 200
    # Un code ne sert qu'une fois
    assert client.post("/auth/reinitialiser", json={"email": "directeur@test.gn", "code": code, "nouveau": "autre1234"}).status_code == 400
    # Par e-mail si pas de téléphone ni de SMS
    monkeypatch.setenv("SMS_FOURNISSEUR", "")
    monkeypatch.setenv("SMTP_HOTE", "simulation")
    monkeypatch.setenv("SMTP_EXPEDITEUR", "ecole@test.gn")
    client.post("/auth/mot-de-passe-oublie", json={"email": "directeur@test.gn"})
    assert notifications.SIMULES[-1]["canal"] == "email" and notifications.SIMULES[-1]["a"] == "directeur@test.gn"
    # Pas plus de 3 demandes par heure
    client.post("/auth/mot-de-passe-oublie", json={"email": "directeur@test.gn"})
    assert client.post("/auth/mot-de-passe-oublie", json={"email": "directeur@test.gn"}).status_code == 429


def test_postes_personnalises(client, dir_headers):
    h = dir_headers
    creer_compte(client, h, "surveillant", "s@test.gn")
    sid = next(u["id"] for u in client.get("/utilisateurs", headers=h).json() if u["email"] == "s@test.gn")
    gardien = client.post("/paie/postes", headers=h, json={"nom": "Surveillant général", "type": "mensuel", "montant": 900000}).json()
    assert client.post("/paie/postes", headers=h, json={"nom": "surveillant général", "type": "mensuel", "montant": 1}).status_code == 400
    ligne = client.put(f"/paie/personnel/{sid}/poste?mois=2026-11", headers=h, json={"poste_id": gardien["id"]}).json()
    assert ligne["poste"] == "Surveillant général" and ligne["contrat_montant"] == 900000
    assert client.delete(f"/paie/postes/{gardien['id']}", headers=h).status_code == 400    # occupé
    f = client.post("/paie/fiches", headers=h, json={"utilisateur_id": sid, "mois": "2026-11"}).json()
    assert f["net"] == 900000
    # Changer le salaire du poste ne touche pas les fiches déjà faites
    client.patch(f"/paie/postes/{gardien['id']}", headers=h, json={"nom": "Surveillant général", "type": "mensuel", "montant": 950000})
    assert next(l for l in client.get("/paie/personnel?mois=2026-11", headers=h).json()
                if l["utilisateur_id"] == sid)["fiche"]["net"] == 900000
    sv = next(p for p in client.get("/paie/postes", headers=h).json() if p["nom"] == "Surveillant")
    client.put(f"/paie/personnel/{sid}/poste", headers=h, json={"poste_id": sv["id"]})
    assert client.delete(f"/paie/postes/{gardien['id']}", headers=h).status_code == 204


def test_paiement_mensuel_et_inscription(client, dir_headers):
    h = dir_headers
    ecole = client.get("/auth/moi", headers=h).json()["ecole"]
    assert ecole["mode_paiement"] == "mensuel" and ecole["nb_mois"] == 9
    c = client.post("/classes", headers=h, json={"nom": "CP", "niveau": "Primaire", "mensualite": 40000,
                                                  "frais_inscription": 25000}).json()
    assert c["frais_annuels"] == 360000
    ins = next(t for t in client.get("/frais", headers=h).json() if t["code"] == "inscription")
    # Inscription : le premier versement règle les frais d'inscription
    r = client.post("/eleves", headers=h, json={"nom": "Bah", "prenom": "Ali", "classe_id": c["id"],
                                                "paiement": {"montant": 25000, "type_frais_id": ins["id"]}}).json()
    eid = r["eleve"]["id"]
    s = client.get(f"/eleves/{eid}/solde", headers=h).json()
    assert [(l["nom"], l["du"], l["reste"]) for l in s["lignes"]] == [("Scolarité", 360000, 360000), ("Inscription", 25000, 0)]
    assert len(s["echeances"]) == 9 and s["echeances"][0]["nom"] == "Octobre" and s["echeances"][-1]["nom"] == "Juin"
    assert s["echeances"][0]["date_limite"] == "2026-10-05" and s["echeances"][3]["date_limite"] == "2027-01-05"
    assert all(e["montant"] == 40000 for e in s["echeances"])
    # Deux mois payés
    client.post("/paiements", headers=h, json={"eleve_id": eid, "montant": 80000})
    s = client.get(f"/eleves/{eid}/solde", headers=h).json()
    assert [e["paye"] for e in s["echeances"][:3]] == [40000, 40000, 0]
    assert [e["statut"] for e in s["echeances"][:2]] == ["payee", "payee"]
    p = client.get(f"/eleves/{eid}/paiements", headers=h).json()[0]
    assert client.get(f"/paiements/{p['id']}/recu.pdf", headers=h).content.startswith(b"%PDF")
    # Le nombre de mois change la scolarité des classes à mensualité
    client.patch("/ecole", headers=h, json={"nb_mois": 10, "mois_debut": 9})
    assert next(x for x in client.get("/classes", headers=h).json() if x["id"] == c["id"])["frais_annuels"] == 400000
    s = client.get(f"/eleves/{eid}/solde", headers=h).json()
    assert s["echeances"][0]["nom"] == "Septembre" and len(s["echeances"]) == 10
    # Le frais « Inscription » ne se modifie pas comme les autres
    assert client.patch(f"/frais/{ins['id']}", headers=h, json={"nom": "Inscr", "montant": 1, "obligatoire": True}).status_code == 400
    assert client.delete(f"/frais/{ins['id']}", headers=h).status_code == 400
    # Une classe sans frais d'inscription : pas de ligne Inscription
    c2 = client.post("/classes", headers=h, json={"nom": "CE1", "niveau": "Primaire", "mensualite": 30000}).json()
    r2 = client.post("/eleves", headers=h, json={"nom": "Sow", "prenom": "Mo", "classe_id": c2["id"],
                                                 "paiement": {"montant": 30000}}).json()
    assert [l["nom"] for l in client.get(f"/eleves/{r2['eleve']['id']}/solde", headers=h).json()["lignes"]] == ["Scolarité"]


def test_numeros_recus_par_ecole_et_tri_des_classes(client):
    from tests.conftest import creer_ecole
    h1, h2 = creer_ecole(client, "A", "a@a.gn"), creer_ecole(client, "B", "b@b.gn")
    for h in (h1, h2):
        for nom in ["10ème A", "7ème B", "CM1", "7ème A", "8ème A", "CP1"]:
            client.post("/classes", headers=h, json={"nom": nom, "niveau": "X", "mensualite": 1000})
    assert [c["nom"] for c in client.get("/classes", headers=h1).json()] == ["CP1", "CM1", "7ème A", "7ème B", "8ème A", "10ème A"]
    c1 = client.get("/classes", headers=h1).json()[0]["id"]
    c2 = client.get("/classes", headers=h2).json()[0]["id"]
    r1 = client.post("/eleves", headers=h1, json={"nom": "A", "prenom": "B", "classe_id": c1, "paiement": {"montant": 1000}}).json()
    r2 = client.post("/eleves", headers=h2, json={"nom": "A", "prenom": "B", "classe_id": c2, "paiement": {"montant": 1000}}).json()
    assert r1["paiement"]["numero_recu"].endswith("-00001") and r2["paiement"]["numero_recu"].endswith("-00001")
    assert r1["paiement"]["numero_recu"] != r2["paiement"]["numero_recu"]
    # Passage : 7ème B monte en 8ème A (pas de 8ème B), CP1 n'a pas de CP2 → à vérifier par le directeur
    p = {x["classe"]: x["vers_classe_id"] for x in client.get("/annee/proposition", headers=h1).json()["passages"]}
    noms = {c["id"]: c["nom"] for c in client.get("/classes", headers=h1).json()}
    assert noms[p["7ème A"]] == "8ème A" and noms[p["7ème B"]] == "8ème A" and p["CP1"] is None and p["10ème A"] is None
