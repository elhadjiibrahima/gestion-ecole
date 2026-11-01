from tests.conftest import creer_compte, creer_ecole, inscrire


def _matieres(client, h):
    return {m["nom"]: m for m in client.get("/matieres", headers=h).json()}


def test_matieres_par_defaut_a_la_creation(client, dir_headers):
    m = _matieres(client, dir_headers)
    assert m["Mathématiques"]["coefficient"] == 4 and len(m) == 8


def test_gestion_matieres(client, dir_headers):
    r = client.post("/matieres", headers=dir_headers, json={"nom": "Arabe", "coefficient": 2})
    assert r.status_code == 201
    assert client.post("/matieres", headers=dir_headers, json={"nom": " arabe ", "coefficient": 1}).status_code == 400
    assert client.post("/matieres", headers=dir_headers, json={"nom": "X", "coefficient": 0}).status_code == 422
    mid = r.json()["id"]
    assert client.patch(f"/matieres/{mid}", headers=dir_headers, json={"coefficient": 3}).json()["coefficient"] == 3
    assert client.delete(f"/matieres/{mid}", headers=dir_headers).status_code == 204
    # Les matières par défaut supprimées peuvent être remises
    maths = _matieres(client, dir_headers)["Mathématiques"]["id"]
    client.delete(f"/matieres/{maths}", headers=dir_headers)
    assert "Mathématiques" in {m["nom"] for m in client.post("/matieres/defaut", headers=dir_headers).json()}


def test_matieres_roles_et_isolation(client, dir_headers):
    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn")
    assert client.get("/matieres", headers=prof).status_code == 200
    assert client.post("/matieres", headers=prof, json={"nom": "Y", "coefficient": 1}).status_code == 403
    autre = creer_ecole(client, nom="Autre", email="autre@test.gn")
    mid = next(iter(_matieres(client, dir_headers).values()))["id"]
    assert client.patch(f"/matieres/{mid}", headers=autre, json={"coefficient": 9}).status_code == 404


def test_grille_saisie_modification_effacement(client, dir_headers, setup):
    _, eleve = setup
    m = _matieres(client, dir_headers)
    url = f"/notes/grille?eleve_id={eleve['id']}&trimestre=1"
    g = client.get(url, headers=dir_headers).json()
    assert len(g) == 8 and all(c["valeur"] is None for c in g)

    client.put(url, headers=dir_headers, json=[
        {"matiere_id": m["Mathématiques"]["id"], "valeur": 14},
        {"matiere_id": m["Français"]["id"], "valeur": 11.5},
    ])
    g = {c["matiere"]: c for c in client.get(url, headers=dir_headers).json()}
    assert g["Mathématiques"]["valeur"] == 14 and g["Français"]["valeur"] == 11.5 and g["Anglais"]["valeur"] is None

    # Modifier une case remplace la note (pas de doublon) ; vider une case l'efface
    client.put(url, headers=dir_headers, json=[
        {"matiere_id": m["Mathématiques"]["id"], "valeur": 16},
        {"matiere_id": m["Français"]["id"], "valeur": None},
    ])
    notes = client.get(f"/notes?eleve_id={eleve['id']}&trimestre=1", headers=dir_headers).json()
    assert [(n["matiere"], n["valeur"], n["coefficient"]) for n in notes] == [("Mathématiques", 16, 4)]

    b = client.get(f"/eleves/{eleve['id']}/bulletin?trimestre=1", headers=dir_headers).json()
    assert b["moyenne_generale"] == 16
    # Autre trimestre : vide
    assert all(c["valeur"] is None for c in client.get(url.replace("trimestre=1", "trimestre=2"), headers=dir_headers).json())


def test_grille_validation(client, dir_headers, setup):
    _, eleve = setup
    mid = _matieres(client, dir_headers)["Anglais"]["id"]
    url = f"/notes/grille?eleve_id={eleve['id']}&trimestre=1"
    assert client.put(url, headers=dir_headers, json=[{"matiere_id": mid, "valeur": 21}]).status_code == 422
    assert client.put(url, headers=dir_headers, json=[{"matiere_id": 9999, "valeur": 10}]).status_code == 400
    assert client.put(url.replace("=1", "=4"), headers=dir_headers, json=[]).status_code == 400
    compta = creer_compte(client, dir_headers, "comptable", "c@test.gn")
    assert client.put(url, headers=compta, json=[{"matiere_id": mid, "valeur": 10}]).status_code == 403


def test_changer_coefficient_met_a_jour_les_notes(client, dir_headers, setup):
    _, eleve = setup
    m = _matieres(client, dir_headers)
    url = f"/notes/grille?eleve_id={eleve['id']}&trimestre=1"
    client.put(url, headers=dir_headers, json=[
        {"matiere_id": m["Mathématiques"]["id"], "valeur": 10},
        {"matiere_id": m["Anglais"]["id"], "valeur": 16},
    ])
    # (10*4 + 16*2) / 6 = 12 ; puis Anglais coef 4 → (40 + 64) / 8 = 13
    assert client.get(f"/eleves/{eleve['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 12
    client.patch(f"/matieres/{m['Anglais']['id']}", headers=dir_headers, json={"coefficient": 4, "nom": "Anglais LV1"})
    b = client.get(f"/eleves/{eleve['id']}/bulletin?trimestre=1", headers=dir_headers).json()
    assert b["moyenne_generale"] == 13 and "Anglais LV1" in [l["matiere"] for l in b["matieres"]]
    # Suppression refusée tant que des notes existent
    assert client.delete(f"/matieres/{m['Anglais']['id']}", headers=dir_headers).status_code == 400
