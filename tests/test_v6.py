from datetime import date

from tests.conftest import creer_compte, creer_ecole, inscrire, toutes_matieres


def _m(client, h):
    return {m["nom"]: m["id"] for m in client.get("/matieres", headers=h).json()}


def _bulletin(client, h, eleve_id, t=1):
    return client.get(f"/eleves/{eleve_id}/bulletin?trimestre={t}", headers=h).json()


# ---------- Enseignants : matières × classes ----------
def test_affectations_enseignant(client, dir_headers, setup):
    h = dir_headers
    classe_a, eleve_a = setup
    classe_b = client.post("/classes", headers=h, json={"nom": "5ème A", "niveau": "Collège", "frais_annuels": 500000}).json()
    eleve_b = inscrire(client, h, classe_b["id"], "Bah", "Ali")
    m = _m(client, h)
    maths, fr = m["Mathématiques"], m["Français"]

    # Une affectation vers une classe d'une autre école est refusée
    autre = creer_ecole(client, nom="Autre", email="autre@test.gn")
    c_autre = client.post("/classes", headers=autre, json={"nom": "X", "niveau": "Y", "frais_annuels": 1}).json()
    r = client.post("/utilisateurs", headers=h, json={"nom": "P", "email": "x@test.gn", "role": "enseignant",
                    "mot_de_passe": "motdepasse123", "affectations": [{"matiere_id": maths, "classe_id": c_autre["id"]}]})
    assert r.status_code == 400
    # Un comptable ne reçoit pas de matière
    r = client.post("/utilisateurs", headers=h, json={"nom": "C", "email": "c@test.gn", "role": "comptable",
                    "mot_de_passe": "motdepasse123", "affectations": [{"matiere_id": maths, "classe_id": classe_a["id"]}]})
    assert r.status_code == 400

    prof = creer_compte(client, h, "enseignant", "p@test.gn", [{"matiere_id": maths, "classe_id": classe_a["id"]}])
    moi = client.get("/auth/moi", headers=prof).json()["utilisateur"]
    assert moi["affectations"] == [{"matiere_id": maths, "classe_id": classe_a["id"]}]

    url_a = f"/notes/grille?eleve_id={eleve_a['id']}&trimestre=1"
    g = {c["matiere"]: c for c in client.get(url_a, headers=prof).json()}
    assert g["Mathématiques"]["modifiable"] and not g["Français"]["modifiable"]
    assert client.put(url_a, headers=prof, json=[{"matiere_id": maths, "composition": 14}]).status_code == 200
    assert client.put(url_a, headers=prof, json=[{"matiere_id": fr, "composition": 14}]).status_code == 403
    url_b = f"/notes/grille?eleve_id={eleve_b['id']}&trimestre=1"
    assert client.put(url_b, headers=prof, json=[{"matiere_id": maths, "composition": 14}]).status_code == 403
    assert client.post("/notes", headers=prof, json={"eleve_id": eleve_b["id"], "matiere": "Mathématiques",
                                                      "valeur": 10, "trimestre": 1}).status_code == 403
    # Appel : seulement ses classes
    jour = date.today().isoformat()
    assert client.post("/absences/appel", headers=prof, json={"classe_id": classe_a["id"], "jour": jour, "trimestre": 1,
                                                               "absents": [eleve_a["id"]]}).status_code == 200
    assert client.post("/absences/appel", headers=prof, json={"classe_id": classe_b["id"], "jour": jour,
                                                               "trimestre": 1}).status_code == 403
    assert all(a["classe"] == "6ème A" for a in client.get("/absences", headers=prof).json())
    # Mais pas de justification
    aid = client.get("/absences", headers=prof).json()[0]["id"]
    assert client.patch(f"/absences/{aid}", headers=prof, json={"justifiee": True}).status_code == 403
    assert client.delete(f"/absences/{aid}", headers=prof).status_code == 403

    # Le directeur modifie les affectations : le prof peut maintenant saisir en 5ème
    uid = moi["id"]
    r = client.put(f"/utilisateurs/{uid}/affectations", headers=h, json=[
        {"matiere_id": maths, "classe_id": classe_a["id"]}, {"matiere_id": maths, "classe_id": classe_b["id"]}])
    assert len(r.json()["affectations"]) == 2
    assert client.put(url_b, headers=prof, json=[{"matiere_id": maths, "composition": 12}]).status_code == 200
    assert client.put(f"/utilisateurs/{uid}/affectations", headers=prof, json=[]).status_code == 403


# ---------- Surveillant ----------
def test_surveillant(client, dir_headers, setup):
    h = dir_headers
    classe, eleve = setup
    surv = creer_compte(client, h, "surveillant", "s@test.gn")
    jour = date.today().isoformat()
    assert client.post("/absences/appel", headers=surv, json={"classe_id": classe["id"], "jour": jour, "trimestre": 1,
                                                               "retards": [eleve["id"]]}).status_code == 200
    a = client.get("/absences", headers=surv).json()[0]
    assert client.patch(f"/absences/{a['id']}", headers=surv, json={"justifiee": True, "motif": "Transport"}).status_code == 200
    assert client.get("/absences", headers=surv).json()[0]["justifiee"] is True
    assert client.get("/eleves", headers=surv).status_code == 200            # voit les élèves
    assert client.get("/classes", headers=surv).status_code == 200
    # Pas d'argent, pas de notes
    assert client.get("/stats", headers=surv).status_code == 403
    assert client.post("/paiements", headers=surv, json={"eleve_id": eleve["id"], "montant": 1}).status_code == 403
    m = _m(client, h)["Mathématiques"]
    assert client.put(f"/notes/grille?eleve_id={eleve['id']}&trimestre=1", headers=surv,
                      json=[{"matiere_id": m, "composition": 10}]).status_code == 403
    assert client.delete(f"/absences/{a['id']}", headers=surv).status_code == 204


# ---------- Devoirs et compositions ----------
def test_devoirs_et_composition(client, dir_headers, setup):
    h = dir_headers
    _, eleve = setup
    m = _m(client, h)
    url = f"/notes/grille?eleve_id={eleve['id']}&trimestre=1"
    g = {c["matiere"]: c for c in client.get(url, headers=h).json()}
    assert g["Mathématiques"]["devoirs"] == [None, None]                  # 2 devoirs par défaut

    r = client.put(url, headers=h, json=[{"matiere_id": m["Mathématiques"], "devoirs": [10, 14], "composition": 15}])
    maths = {c["matiere"]: c for c in r.json()}["Mathématiques"]
    # (moyenne devoirs 12 + 15 × 2) / 3 = 14
    assert maths["devoirs"] == [10, 14] and maths["composition"] == 15 and maths["valeur"] == 14
    assert _bulletin(client, h, eleve["id"])["moyenne_generale"] == 14

    # Modifier un seul devoir ; vider la composition
    client.put(url, headers=h, json=[{"matiere_id": m["Mathématiques"], "devoirs": [16, 14]}])
    assert {c["matiere"]: c for c in client.get(url, headers=h).json()}["Mathématiques"]["valeur"] == round((15 + 30) / 3, 2)
    client.put(url, headers=h, json=[{"matiere_id": m["Mathématiques"], "effacer_composition": True}])
    maths = {c["matiere"]: c for c in client.get(url, headers=h).json()}["Mathématiques"]
    assert maths["composition"] is None and maths["valeur"] == 15            # devoirs seuls : (16+14)/2
    hist = client.get("/notes/historique", headers=h).json()
    assert {l["type_note"] for l in hist} == {"devoir 1", "devoir 2", "composition"}

    # Réglages : 3 devoirs, composition au même poids
    client.patch("/ecole", headers=h, json={"nb_devoirs": 3, "poids_composition": 1})
    client.put(url, headers=h, json=[{"matiere_id": m["Français"], "devoirs": [8, 10, 12], "composition": 14}])
    fr = {c["matiere"]: c for c in client.get(url, headers=h).json()}["Français"]
    assert fr["devoirs"] == [8, 10, 12] and fr["valeur"] == 12               # (10 + 14) / 2
    assert client.put(url, headers=h, json=[{"matiere_id": m["Français"], "devoirs": [1, 2, 3, 4]}]).status_code == 400
    assert client.put(url, headers=h, json=[{"matiere_id": m["Français"], "devoirs": [21]}]).status_code == 422


# ---------- Moyenne annuelle et décision ----------
def test_resultats_annuels(client, dir_headers, setup):
    h = dir_headers
    classe, bon = setup
    faible = inscrire(client, h, classe["id"], "Bah", "Ali")
    maths = _m(client, h)["Mathématiques"]
    for eleve, notes in ((bon, (12, 14, 16)), (faible, (8, 9, None))):
        for t, v in enumerate(notes, start=1):
            if v is not None:
                client.put(f"/notes/grille?eleve_id={eleve['id']}&trimestre={t}", headers=h,
                           json=[{"matiere_id": maths, "composition": v}])
    r = {x["eleve"]: x for x in client.get(f"/classes/{classe['id']}/resultats-annuels", headers=h).json()}
    assert r["Aminata Diallo"]["moyennes_trimestres"] == [12, 14, 16]
    assert r["Aminata Diallo"]["moyenne_annuelle"] == 14 and r["Aminata Diallo"]["rang"] == 1
    assert r["Aminata Diallo"]["decision"] == "Admis(e)"
    assert r["Ali Bah"]["moyenne_annuelle"] == 8.5 and r["Ali Bah"]["decision"] == "Redouble"   # T3 non noté

    b3 = _bulletin(client, h, bon["id"], 3)
    assert b3["moyenne_annuelle"] == 14 and b3["rang_annuel"] == 1 and b3["decision"] == "Admis(e)"
    assert _bulletin(client, h, bon["id"], 1)["moyenne_annuelle"] is None           # seulement au 3e trimestre
    assert client.get(f"/eleves/{bon['id']}/bulletin.pdf?trimestre=3", headers=h).content.startswith(b"%PDF")

    # Le seuil est réglable ; la nouvelle année propose les redoublants
    client.patch("/ecole", headers=h, json={"seuil_admission": 8})
    r = {x["eleve"]: x for x in client.get(f"/classes/{classe['id']}/resultats-annuels", headers=h).json()}
    assert r["Ali Bah"]["decision"] == "Admis(e)"
    client.patch("/ecole", headers=h, json={"seuil_admission": 10})
    prop = client.get("/annee/proposition", headers=h).json()
    assert prop["passages"][0]["redoublants_proposes"] == [faible["id"]]


# ---------- Réductions ----------
def test_reductions(client, dir_headers, setup):
    h = dir_headers
    classe, eleve = setup                          # frais 500 000, 50 000 payés
    url = f"/eleves/{eleve['id']}/reduction"
    assert client.put(url, headers=h, json={"pourcentage": 50}).status_code == 400          # motif obligatoire
    s = client.put(url, headers=h, json={"pourcentage": 50, "motif": "2e enfant"}).json()
    assert (s["frais_classe"], s["reduction"], s["frais_annuels"], s["reste_a_payer"]) == (500000, 250000, 250000, 200000)
    assert s["reduction_motif"] == "2e enfant"
    s = client.put(url, headers=h, json={"pourcentage": 10, "montant": 100000, "motif": "Bourse + fratrie"}).json()
    assert s["reduction"] == 150000 and s["frais_annuels"] == 350000
    # La réduction compte partout : paiements plafonnés, stats, reçu
    assert client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 300001}).status_code == 400
    p = client.post("/paiements", headers=h, json={"eleve_id": eleve["id"], "montant": 300000}).json()
    assert client.get(f"/eleves/{eleve['id']}/solde", headers=h).json()["statut"] == "a_jour"
    assert client.get("/stats", headers=h).json()["total_attendu"] == 350000
    assert client.get(f"/paiements/{p['id']}/recu.pdf", headers=h).content.startswith(b"%PDF")
    # Gratuité totale
    s = client.put(url, headers=h, json={"pourcentage": 100, "motif": "Enfant d'enseignant"}).json()
    assert s["frais_annuels"] == 0 and s["reste_a_payer"] == 0
    # Retirer
    s = client.put(url, headers=h, json={"pourcentage": 0, "montant": 0}).json()
    assert s["reduction"] == 0 and s["reduction_motif"] is None
    compta = creer_compte(client, h, "comptable", "c@test.gn")
    assert client.put(url, headers=compta, json={"pourcentage": 50, "motif": "x"}).status_code == 403


# ---------- Blocage après mots de passe faux ----------
def test_blocage_connexion_personnel(client, dir_headers):
    for i in range(4):
        r = client.post("/auth/token", data={"username": "directeur@test.gn", "password": "faux"})
        assert r.status_code == 401
    assert "1 essai(s) restant(s)" in r.json()["detail"]
    client.post("/auth/token", data={"username": "directeur@test.gn", "password": "faux"})
    r = client.post("/auth/token", data={"username": "directeur@test.gn", "password": "motdepasse123"})
    assert r.status_code == 429 and "bloqué" in r.json()["detail"]
    # Un autre compte n'est pas touché
    creer_compte(client, dir_headers, "comptable", "c@test.gn")
    assert client.post("/auth/token", data={"username": "c@test.gn", "password": "motdepasse123"}).status_code == 200


def test_code_parent_jamais_dans_les_listes(client, dir_headers, setup):
    _, eleve = setup
    client.get(f"/eleves/{eleve['id']}/code-parent", headers=dir_headers)
    assert "code_parent" not in client.get("/eleves", headers=dir_headers).json()[0]
    assert "code_parent" not in client.get(f"/eleves/{eleve['id']}", headers=dir_headers).json()
