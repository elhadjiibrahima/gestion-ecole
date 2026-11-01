from tests.conftest import creer_compte, creer_ecole


def test_sans_connexion_refuse(client):
    assert client.get("/eleves").status_code == 401
    assert client.get("/eleves", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_connexion(client, dir_headers):
    ok = client.post("/auth/token", data={"username": "DIRECTEUR@test.gn", "password": "motdepasse123"})
    assert ok.status_code == 200
    ko = client.post("/auth/token", data={"username": "directeur@test.gn", "password": "mauvais"})
    assert ko.status_code == 401
    moi = client.get("/auth/moi", headers=dir_headers).json()
    assert moi["utilisateur"]["role"] == "directeur" and moi["ecole"]["nom"] == "École Test"


def test_email_unique(client, dir_headers):
    r = client.post("/auth/inscription", json={
        "nom_ecole": "Autre", "nom_directeur": "X", "email": "directeur@test.gn", "mot_de_passe": "motdepasse123"})
    assert r.status_code == 400


def test_mot_de_passe_trop_court(client):
    r = client.post("/auth/inscription", json={
        "nom_ecole": "E", "nom_directeur": "X", "email": "a@b.gn", "mot_de_passe": "court"})
    assert r.status_code == 422


def test_isolation_entre_ecoles(client, dir_headers, setup):
    classe, eleve = setup
    autre = creer_ecole(client, nom="Autre École", email="autre@test.gn")
    # L'autre école ne voit rien et ne peut rien toucher
    assert client.get("/eleves", headers=autre).json() == []
    assert client.get("/classes", headers=autre).json() == []
    assert client.get(f"/eleves/{eleve['id']}", headers=autre).status_code == 404
    assert client.get(f"/eleves/{eleve['id']}/bulletin.pdf?trimestre=1", headers=autre).status_code == 404
    r = client.post("/paiements", headers=autre, json={"eleve_id": eleve["id"], "montant": 1000})
    assert r.status_code == 400
    r = client.post("/eleves", headers=autre, json={
        "nom": "X", "prenom": "Y", "classe_id": classe["id"], "paiement": {"montant": 1000}})
    assert r.status_code == 400
    assert client.get("/stats", headers=autre).json()["nb_eleves"] == 0


def test_roles(client, dir_headers, setup):
    classe, eleve = setup
    compta = creer_compte(client, dir_headers, "comptable", "compta@test.gn")
    prof = creer_compte(client, dir_headers, "enseignant", "prof@test.gn")

    # Comptable : paiements oui, notes et classes non
    assert client.post("/paiements", headers=compta, json={"eleve_id": eleve["id"], "montant": 1000}).status_code == 201
    assert client.post("/notes", headers=compta, json={
        "eleve_id": eleve["id"], "matiere": "M", "valeur": 10, "trimestre": 1}).status_code == 403
    assert client.post("/classes", headers=compta, json={
        "nom": "X", "niveau": "Y", "frais_annuels": 1}).status_code == 403

    # Enseignant : notes et bulletins oui, argent non
    assert client.post("/notes", headers=prof, json={
        "eleve_id": eleve["id"], "matiere": "M", "valeur": 10, "trimestre": 1}).status_code == 201
    assert client.get(f"/eleves/{eleve['id']}/bulletin?trimestre=1", headers=prof).status_code == 200
    assert client.get("/stats", headers=prof).status_code == 403
    assert client.get("/impayes", headers=prof).status_code == 403
    assert client.post("/paiements", headers=prof, json={"eleve_id": eleve["id"], "montant": 1}).status_code == 403

    # Seul le directeur gère les comptes
    assert client.get("/utilisateurs", headers=compta).status_code == 403
    assert len(client.get("/utilisateurs", headers=dir_headers).json()) == 3


def test_desactivation_compte(client, dir_headers):
    compta = creer_compte(client, dir_headers, "comptable", "compta@test.gn")
    uid = next(u["id"] for u in client.get("/utilisateurs", headers=dir_headers).json() if u["role"] == "comptable")
    client.patch(f"/utilisateurs/{uid}/actif", params={"actif": False}, headers=dir_headers)
    assert client.get("/eleves", headers=compta).status_code == 401
    assert client.post("/auth/token", data={"username": "compta@test.gn", "password": "motdepasse123"}).status_code == 401
