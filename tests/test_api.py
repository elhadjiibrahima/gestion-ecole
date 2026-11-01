from tests.conftest import creer_compte, creer_ecole, inscrire


def test_inscription_et_matricule(client, dir_headers, setup):
    _, eleve = setup
    assert eleve["matricule"].endswith("-00001")
    r = client.get("/eleves", params={"q": "amin"}, headers=dir_headers).json()
    assert r[0]["id"] == eleve["id"]


def test_classe_inexistante(client, dir_headers):
    r = client.post("/eleves", headers=dir_headers, json={
        "nom": "X", "prenom": "Y", "classe_id": 99, "paiement": {"montant": 1000}})
    assert r.status_code == 400


def test_inscription_exige_un_paiement(client, dir_headers, setup):
    classe, _ = setup
    base = {"nom": "Bah", "prenom": "Ali", "classe_id": classe["id"]}
    assert client.post("/eleves", headers=dir_headers, json=base).status_code == 422
    assert client.post("/eleves", headers=dir_headers, json={**base, "paiement": {"montant": 0}}).status_code == 422
    # Montant supérieur aux frais : refusé, et l'élève n'est pas créé
    r = client.post("/eleves", headers=dir_headers, json={**base, "paiement": {"montant": 600000}})
    assert r.status_code == 400
    assert len(client.get("/eleves", headers=dir_headers).json()) == 1
    r = client.post("/eleves", headers=dir_headers, json={
        **base, "paiement": {"montant": 150000, "mode": "orange_money", "reference": "OM1"}}).json()
    assert r["paiement"]["numero_recu"].endswith("000002") and r["paiement"]["eleve_id"] == r["eleve"]["id"]
    solde = client.get(f"/eleves/{r['eleve']['id']}/solde", headers=dir_headers).json()
    assert solde["total_paye"] == 150000 and solde["statut"] == "partiel"


def test_paiement_superieur_au_reste_refuse(client, dir_headers, setup):
    _, eleve = setup
    r = client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 450001})
    assert r.status_code == 400 and "450 000" in r.json()["detail"]
    assert client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 450000}).status_code == 201
    r = client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 1})
    assert r.status_code == 400 and "totalité" in r.json()["detail"]


def test_paiements_solde_et_impayes(client, dir_headers, setup):
    _, eleve = setup
    p = client.post("/paiements", headers=dir_headers, json={
        "eleve_id": eleve["id"], "montant": 200000, "mode": "orange_money", "reference": "OM123"}).json()
    assert p["numero_recu"].startswith("R") and p["numero_recu"].endswith("000002")
    client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 100000})

    solde = client.get(f"/eleves/{eleve['id']}/solde", headers=dir_headers).json()
    assert solde["total_paye"] == 350000 and solde["reste_a_payer"] == 150000

    impayes = client.get("/impayes", headers=dir_headers).json()
    assert len(impayes) == 1 and impayes[0]["reste_a_payer"] == 150000

    client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 150000})
    assert client.get("/impayes", headers=dir_headers).json() == []


def test_montant_negatif_refuse(client, dir_headers, setup):
    _, eleve = setup
    r = client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": -5})
    assert r.status_code == 422


def test_bulletin_rang_et_appreciation(client, dir_headers, setup):
    classe, eleve = setup
    autre = inscrire(client, dir_headers, classe["id"], "Bah", "Mamadou")
    for eid, matiere, valeur, coef in [
        (eleve["id"], "Maths", 14, 4), (eleve["id"], "Maths", 16, 4), (eleve["id"], "Français", 12, 3),
        (autre["id"], "Maths", 17, 4), (autre["id"], "Français", 15, 3),
    ]:
        client.post("/notes", headers=dir_headers, json={
            "eleve_id": eid, "matiere": matiere, "valeur": valeur, "coefficient": coef, "trimestre": 1})
    b = client.get(f"/eleves/{eleve['id']}/bulletin", params={"trimestre": 1}, headers=dir_headers).json()
    assert next(m for m in b["matieres"] if m["matiere"] == "Maths")["moyenne"] == 15
    assert b["moyenne_generale"] == 13.71          # (15*4 + 12*3) / 7
    assert b["rang"] == 2 and b["effectif"] == 2
    assert "Bon travail" in b["appreciation"]


def test_note_hors_bareme(client, dir_headers, setup):
    _, eleve = setup
    r = client.post("/notes", headers=dir_headers, json={
        "eleve_id": eleve["id"], "matiere": "Maths", "valeur": 25, "trimestre": 1})
    assert r.status_code == 422


def test_stats(client, dir_headers, setup):
    _, eleve = setup
    client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 125000})
    s = client.get("/stats", headers=dir_headers).json()
    assert s["nb_eleves"] == 1 and s["total_encaisse"] == 175000
    assert s["total_impaye"] == 325000 and s["taux_recouvrement"] == 35.0


def test_page_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "Gestion École" in r.text
