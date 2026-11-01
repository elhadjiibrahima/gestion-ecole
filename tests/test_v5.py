import io
from datetime import date, timedelta

from openpyxl import load_workbook
from PIL import Image

from tests.conftest import creer_compte, creer_ecole, inscrire

AUJ = date.today()


def _jpeg(l=800, h=600, couleur=(200, 30, 30)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (l, h), couleur).save(b, "JPEG")
    return b.getvalue()


# ---------- Échéancier ----------
def test_calcul_echeances():
    from app.models import Tranche
    from app.services import calculer_echeances
    tr = [Tranche(nom="T1", date_limite=AUJ - timedelta(days=30), pourcentage=40, ecole_id=1, annee_scolaire=""),
          Tranche(nom="T2", date_limite=AUJ - timedelta(days=1), pourcentage=30, ecole_id=1, annee_scolaire=""),
          Tranche(nom="T3", date_limite=AUJ + timedelta(days=60), pourcentage=30, ecole_id=1, annee_scolaire="")]
    e = calculer_echeances(250001, 120000, tr)
    assert [x.montant for x in e] == [100000, 75000, 75001]          # le dernier absorbe l'arrondi
    assert [(x.paye, x.statut) for x in e] == [(100000, "payee"), (20000, "en_retard"), (0, "a_venir")]


def test_echeancier_et_retard(client, dir_headers, setup):
    classe, eleve = setup                    # frais 500 000, 50 000 payés
    h = dir_headers
    assert client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()["retard"] == 450000   # sans tranches : tout est dû

    tranches = [
        {"nom": "1re tranche", "date_limite": str(AUJ - timedelta(days=20)), "pourcentage": 40},
        {"nom": "2e tranche", "date_limite": str(AUJ + timedelta(days=40)), "pourcentage": 30},
        {"nom": "3e tranche", "date_limite": str(AUJ + timedelta(days=120)), "pourcentage": 30},
    ]
    bad = [dict(t) for t in tranches]
    bad[0]["pourcentage"] = 50
    assert client.put("/tranches", headers=h, json=bad).status_code == 400            # 110 %
    assert client.put("/tranches", headers=h, json=list(reversed(tranches))).status_code == 400   # dates dans le désordre
    assert len(client.put("/tranches", headers=h, json=tranches).json()) == 3

    s = client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()
    assert s["montant_exigible"] == 200000 and s["retard"] == 150000
    assert s["prochaine_echeance"] == str(AUJ + timedelta(days=40)) and s["prochain_montant"] == 150000
    assert [e["statut"] for e in s["echeances"]] == ["en_retard", "a_venir", "a_venir"]

    st = client.get("/stats", headers=h).json()
    assert st["total_retard_echeances"] == 150000 and st["nb_retard_echeances"] == 1

    msg = client.get("/messages/impayes?en_retard=true", headers=h).json()
    assert len(msg) == 1 and "1re tranche" in msg[0]["texte"] and "150 000 FCFA" in msg[0]["texte"]

    client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 150000})
    s = client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()
    assert s["retard"] == 0 and s["echeances"][0]["statut"] == "payee"
    assert client.get("/messages/impayes?en_retard=true", headers=h).json() == []
    assert len(client.get("/messages/impayes", headers=h).json()) == 1               # reste dû, pas en retard

    assert client.put("/tranches", headers=h, json=[]).json() == []                  # retirer l'échéancier
    prof = creer_compte(client, h, "enseignant", "p@test.gn")
    assert client.put("/tranches", headers=prof, json=tranches).status_code == 403


# ---------- Caisse ----------
def test_caisse(client, dir_headers, setup):
    h = dir_headers
    compta = creer_compte(client, h, "comptable", "c@test.gn")
    r = client.post("/depenses", headers=compta, json={
        "categorie": "salaires", "libelle": "Salaire M. Camara", "montant": 30000, "beneficiaire": "M. Camara"})
    assert r.status_code == 201 and r.json()["saisi_par"] == "Comptable"
    client.post("/depenses", headers=h, json={"categorie": "electricite_eau", "libelle": "Facture EDG", "montant": 5000})
    d3 = client.post("/depenses", headers=h, json={"categorie": "autre", "libelle": "Erreur", "montant": 999}).json()
    assert client.post("/depenses", headers=h, json={"categorie": "autre", "libelle": "X", "montant": 0}).status_code == 422

    assert client.post(f"/depenses/{d3['id']}/annuler", headers=compta, json={"motif": "doublon"}).status_code == 403
    assert client.post(f"/depenses/{d3['id']}/annuler", headers=h, json={"motif": "doublon"}).json()["annule"]

    b = client.get("/caisse/bilan", headers=h).json()
    # Entrées : 50 000 (inscription) ; sorties : 35 000 (la dépense annulée ne compte pas)
    assert (b["total_entrees"], b["total_sorties"], b["solde"]) == (50000, 35000, 15000)
    assert b["par_categorie"] == {"salaires": 30000, "electricite_eau": 5000}
    mois = AUJ.strftime("%Y-%m")
    assert b["mois"] == [{"mois": mois, "entrees": 50000, "sorties": 35000, "solde": 15000, "cumul": 15000}]

    assert len(client.get(f"/depenses?mois={mois}", headers=h).json()) == 3
    assert len(client.get("/depenses?categorie=salaires", headers=h).json()) == 1
    wb = load_workbook(io.BytesIO(client.get("/export/caisse.xlsx", headers=h).content))
    assert wb["Bilan mensuel"]["B2"].value == 50000 and wb["Dépenses"].max_row == 4

    prof = creer_compte(client, h, "enseignant", "p@test.gn")
    assert client.get("/caisse/bilan", headers=prof).status_code == 403
    autre = creer_ecole(client, nom="Autre", email="autre@test.gn")
    assert client.get("/caisse/bilan", headers=autre).json()["total_sorties"] == 0
    assert client.post(f"/depenses/{d3['id']}/annuler", headers=autre, json={"motif": "xxx"}).status_code == 404


# ---------- Photos et cartes ----------
def test_photos_et_cartes(client, dir_headers, setup):
    classe, eleve = setup
    h = dir_headers
    url = f"/eleves/{eleve['id']}/photo"
    assert client.put(url, headers=h, files={"fichier": ("x.txt", b"pas une image", "text/plain")}).status_code == 400
    assert client.put(url, headers=h, files={"fichier": ("p.jpg", _jpeg(), "image/jpeg")}).status_code == 204
    img = Image.open(io.BytesIO(client.get(url, headers=h).content))
    assert img.size == (300, 400) and img.format == "JPEG"          # recadrée au format carte

    m = client.get(f"/photos/miniatures?classe_id={classe['id']}", headers=h).json()
    assert m[0]["eleve_id"] == eleve["id"] and m[0]["data_url"].startswith("data:image/jpeg;base64,")

    # Une photo PNG aussi
    b = io.BytesIO()
    Image.new("RGBA", (500, 500), (0, 0, 255, 255)).save(b, "PNG")
    assert client.put(url, headers=h, files={"fichier": ("p.png", b.getvalue(), "image/png")}).status_code == 204

    inscrire(client, h, classe["id"], "Bah", "Sans-Photo")
    for u in (f"/eleves/{eleve['id']}/carte.pdf", f"/classes/{classe['id']}/cartes.pdf"):
        r = client.get(u, headers=h)
        assert r.status_code == 200 and r.content.startswith(b"%PDF")

    autre = creer_ecole(client, nom="Autre", email="autre@test.gn")
    assert client.get(url, headers=autre).status_code == 404
    assert client.get("/photos/miniatures", headers=autre).json() == []
    prof = creer_compte(client, h, "enseignant", "p@test.gn")
    assert client.put(url, headers=prof, files={"fichier": ("p.jpg", _jpeg(), "image/jpeg")}).status_code == 403
    assert client.delete(url, headers=h).status_code == 204
    assert client.get(url, headers=h).status_code == 404


# ---------- Espace parents ----------
def test_espace_parents(client, dir_headers, setup):
    classe, eleve = setup
    h = dir_headers
    from app.routers import parents
    parents._ECHECS.clear()

    code = client.get(f"/eleves/{eleve['id']}/code-parent", headers=h).json()["code"]
    assert len(code) == 9 and code[4] == "-"
    assert client.get(f"/eleves/{eleve['id']}/code-parent", headers=h).json()["code"] == code   # stable

    assert client.post("/parents/connexion", json={"matricule": eleve["matricule"], "code": "AAAA-AAAA"}).status_code == 401
    r = client.post("/parents/connexion", json={"matricule": eleve["matricule"].lower(), "code": code.lower().replace("-", "")})
    assert r.status_code == 200          # casse et tiret ignorés
    hp = {"Authorization": "Bearer " + r.json()["access_token"]}

    m = client.get("/matieres", headers=h).json()
    client.put(f"/notes/grille?eleve_id={eleve['id']}&trimestre=1", headers=h, json=[{"matiere_id": m[0]["id"], "valeur": 15}])
    client.post("/absences/appel", headers=h, json={"classe_id": classe["id"], "jour": str(AUJ), "trimestre": 1,
                                                     "absents": [eleve["id"]]})
    e = client.get("/parents/moi", headers=hp).json()
    assert e["prenom"] == "Aminata" and e["classe"] == "6ème A" and e["ecole"] == "École Test"
    assert e["solde"]["total_paye"] == 50000 and len(e["paiements"]) == 1
    assert e["bulletins"][0]["moyenne_generale"] == 15 and e["bulletins"][0]["absences"] == 1
    assert e["absences"][0]["type"] == "absence"

    pid = e["paiements"][0]["id"]
    assert client.get(f"/parents/recu/{pid}.pdf", headers=hp).content.startswith(b"%PDF")
    assert client.get("/parents/bulletin.pdf?trimestre=1", headers=hp).content.startswith(b"%PDF")

    # Le jeton parent n'ouvre pas l'espace du personnel, et inversement
    assert client.get("/eleves", headers=hp).status_code == 401
    assert client.get("/parents/moi", headers=h).status_code == 401
    # Un parent ne voit pas le reçu d'un autre élève
    autre = inscrire(client, h, classe["id"], "Bah", "Ali")
    pid2 = client.get(f"/eleves/{autre['id']}/paiements", headers=h).json()[0]["id"]
    assert client.get(f"/parents/recu/{pid2}.pdf", headers=hp).status_code == 404

    # Nouveau code : l'ancien ne marche plus et la session ouverte est coupée
    nouveau = client.post(f"/eleves/{eleve['id']}/code-parent", headers=h).json()["code"]
    assert nouveau != code
    assert client.post("/parents/connexion", json={"matricule": eleve["matricule"], "code": code}).status_code == 401
    assert client.get("/parents/moi", headers=hp).status_code == 401
    assert client.post("/parents/connexion", json={"matricule": eleve["matricule"], "code": nouveau}).status_code == 200

    assert client.get(f"/classes/{classe['id']}/codes-parents.pdf", headers=h).content.startswith(b"%PDF")
    msgs = {m["eleve"]: m for m in client.get(f"/messages/acces?classe_id={classe['id']}", headers=h).json()}
    assert nouveau in msgs["Aminata Diallo"]["texte"] and "/espace-parents" in msgs["Aminata Diallo"]["texte"]
    prof = creer_compte(client, h, "enseignant", "p@test.gn")
    assert client.get(f"/eleves/{eleve['id']}/code-parent", headers=prof).status_code == 403


def test_parents_trop_d_essais(client, dir_headers, setup):
    _, eleve = setup
    from app.routers import parents
    parents._ECHECS.clear()
    code = client.get(f"/eleves/{eleve['id']}/code-parent", headers=dir_headers).json()["code"]
    for _ in range(5):
        client.post("/parents/connexion", json={"matricule": eleve["matricule"], "code": "ZZZZ-ZZZZ"})
    r = client.post("/parents/connexion", json={"matricule": eleve["matricule"], "code": code})
    assert r.status_code == 429          # bloqué même avec le bon code
    parents._ECHECS.clear()


def test_page_parents_et_pwa(client):
    for u in ("/espace-parents", "/sw.js", "/manifest.webmanifest"):
        assert client.get(u).status_code == 200


# ---------- Hors connexion : pas de doublon au renvoi ----------
def test_paiement_cle_client_idempotent(client, dir_headers, setup):
    _, eleve = setup
    corps = {"eleve_id": eleve["id"], "montant": 10000, "cle_client": "abc-123"}
    p1 = client.post("/paiements", headers=dir_headers, json=corps).json()
    p2 = client.post("/paiements", headers=dir_headers, json=corps).json()
    assert p1["id"] == p2["id"]
    assert client.get(f"/eleves/{eleve['id']}/solde", headers=dir_headers).json()["total_paye"] == 60000
