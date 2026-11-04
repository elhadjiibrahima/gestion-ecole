"""Complexe sénégalais : cycles, notes sur 10 / semestres, saisie par matière, publication, caisse du jour,
personnel sans compte, cahier de textes, statistiques."""
from datetime import date

from tests.conftest import creer_compte


def complexe(client, email="dir@complexe.sn", cycles=("prescolaire", "primaire", "college", "lycee")):
    r = client.post("/auth/inscription", json={"nom_ecole": "Complexe Les Pédagogues", "nom_directeur": "M. Ndiaye",
                                               "email": email, "mot_de_passe": "motdepasse123", "pays": "senegal",
                                               "cycles": list(cycles)})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def classes(client, h):
    return {c["nom"]: c for c in client.get("/classes", headers=h).json()}


def test_complexe_senegalais_cree_tout(client):
    h = complexe(client)
    ecole = client.get("/auth/moi", headers=h).json()["ecole"]
    assert ecole["devise"] == "FCFA" and ecole["indicatif_tel"] == "221" and ecole["pays"] == "senegal"
    noms = [c["nom"] for c in client.get("/classes", headers=h).json()]
    assert noms[:3] == ["Petite section", "Moyenne section", "Grande section"]
    assert noms[3:9] == ["CI", "CP", "CE1", "CE2", "CM1", "CM2"]
    assert noms[9:13] == ["6ème", "5ème", "4ème", "3ème"]
    assert noms[13:] == ["2nde L", "2nde S", "1ère L", "1ère S", "Terminale L", "Terminale S"]
    cyc = {c["code"]: c for c in client.get("/cycles", headers=h).json()}
    assert (cyc["primaire"]["periodes"], cyc["primaire"]["bareme"], cyc["primaire"]["seuil"]) == (3, 10, 5)
    assert (cyc["college"]["periodes"], cyc["college"]["bareme"]) == (2, 20)
    cl = classes(client, h)
    assert cl["CE1"]["niveau"] == "Primaire" and cl["Terminale S"]["niveau"] == "Lycée"
    # Matières de chaque cycle, coefficients des séries
    grille = lambda nom: {l["matiere"]: l["coefficient"] for l in client.get(  # noqa: E731
        f"/matieres/coefficients?classe_id={cl[nom]['id']}", headers=h).json()}
    cp = grille("CP")
    assert "Lecture" in cp and "Philosophie" not in cp and "Physique-Chimie" not in cp
    assert grille("6ème")["Français"] == 4 and "Lecture" not in grille("6ème")
    assert grille("Terminale S")["Mathématiques"] == 5 and grille("Terminale L")["Philosophie"] == 4
    # Rien à recréer, mais on peut ajouter une classe parallèle
    assert all(t["existe"] for t in client.get("/classes/types", headers=h).json())
    b = client.post(f"/classes/{cl['6ème']['id']}/parallele", headers=h).json()
    cl = classes(client, h)
    assert b["nom"] == "6ème B" and "6ème A" in cl and "6ème" not in cl and b["niveau"] == "Collège"
    c = client.post(f"/classes/{cl['6ème A']['id']}/parallele", headers=h).json()
    assert c["nom"] == "6ème C"
    # Tarifs de toutes les classes en une fois
    r = client.put("/classes/tarifs", headers=h, json=[
        {"classe_id": cl["CI"]["id"], "mensualite": 25000, "frais_inscription": 30000},
        {"classe_id": cl["6ème A"]["id"], "mensualite": 35000, "frais_inscription": 40000}]).json()
    t = {x["nom"]: x for x in r}
    assert t["CI"]["frais_annuels"] == 225000 and t["6ème A"]["frais_inscription"] == 40000
    # Passage : CM2 → 6ème (complexe), 3ème → 2nde à vérifier (L ou S), Terminale : fin
    p = {x["classe"]: x for x in client.get("/annee/proposition", headers=h).json()["passages"]}
    noms = {c["id"]: c["nom"] for c in client.get("/classes", headers=h).json()}
    assert noms[p["CM2"]["vers_classe_id"]] == "6ème A" and noms[p["Grande section"]["vers_classe_id"]] == "CI"
    assert noms[p["6ème B"]["vers_classe_id"]] == "5ème" and noms[p["CI"]["vers_classe_id"]] == "CP"
    assert p["3ème"]["a_verifier"] and noms[p["3ème"]["vers_classe_id"]].startswith("2nde")
    assert noms[p["2nde S"]["vers_classe_id"]] == "1ère S" and p["Terminale S"]["vers_classe_id"] is None


def test_notes_sur_10_et_semestres(client):
    h = complexe(client, cycles=("primaire", "college"))
    cl = classes(client, h)
    client.put("/classes/tarifs", headers=h, json=[{"classe_id": cl["CE1"]["id"], "mensualite": 10000},
                                                   {"classe_id": cl["4ème"]["id"], "mensualite": 20000}])
    ce1 = client.post("/eleves", headers=h, json={"nom": "Fall", "prenom": "Awa", "sexe": "F", "classe_id": cl["CE1"]["id"],
                                                  "lieu_naissance": "Thiès", "paiement": {"montant": 10000, "mode": "wave"}}).json()
    assert ce1["paiement"]["mode"] == "wave" and ce1["eleve"]["sexe"] == "F"
    assert client.post("/eleves", headers=h, json={"nom": "X", "prenom": "Y", "sexe": "Z", "classe_id": cl["CE1"]["id"],
                                                   "paiement": {"montant": 1}}).status_code == 422
    mats = {m["matiere"]: m["matiere_id"] for m in client.get(f"/notes/grille?eleve_id={ce1['eleve']['id']}&trimestre=1", headers=h).json()}
    url = f"/notes/grille?eleve_id={ce1['eleve']['id']}&trimestre=1"
    assert client.put(url, headers=h, json=[{"matiere_id": mats["Lecture"], "devoirs": [11]}]).status_code == 422   # sur 10
    client.put(url, headers=h, json=[{"matiere_id": mats["Lecture"], "devoirs": [8, 9], "composition": 9},
                                     {"matiere_id": mats["Mathématiques"], "composition": 8.5}])
    client.put(f"/notes/publications/{cl['CE1']['id']}?trimestre=1", headers=h)
    b = client.get(f"/eleves/{ce1['eleve']['id']}/bulletin?trimestre=1", headers=h).json()
    assert b["bareme"] == 10 and b["periode"] == "Trimestre" and b["mention"] == "Félicitations" and b["publie"]
    pdf = client.get(f"/eleves/{ce1['eleve']['id']}/bulletin.pdf?trimestre=1", headers=h)
    assert pdf.content.startswith(b"%PDF")
    # Collège : 2 semestres ; le bulletin du 2nd semestre porte la décision annuelle
    e4 = client.post("/eleves", headers=h, json={"nom": "Diop", "prenom": "Modou", "sexe": "M", "classe_id": cl["4ème"]["id"],
                                                 "paiement": {"montant": 20000}}).json()["eleve"]
    m4 = {m["matiere"]: m["matiere_id"] for m in client.get(f"/notes/grille?eleve_id={e4['id']}&trimestre=1", headers=h).json()}
    assert client.get(f"/notes/grille?eleve_id={e4['id']}&trimestre=3", headers=h).status_code == 400
    for t, v in ((1, 7), (2, 9)):
        client.put(f"/notes/grille?eleve_id={e4['id']}&trimestre={t}", headers=h,
                   json=[{"matiere_id": m4["Français"], "composition": v}])
    b2 = client.get(f"/eleves/{e4['id']}/bulletin?trimestre=2", headers=h).json()
    assert b2["periode"] == "Semestre" and b2["moyennes_trimestres"] == [7, 9] and b2["decision"] == "Redouble"
    assert b2["mention"] is None and client.get(f"/eleves/{e4['id']}/bulletin?trimestre=1", headers=h).json()["mention"] == "Avertissement"
    assert client.get(f"/classes/{cl['4ème']['id']}/bulletins.pdf?trimestre=2", headers=h).content.startswith(b"%PDF")
    # Le cycle se règle : seuil de passage à 8
    cyc = next(c for c in client.get("/cycles", headers=h).json() if c["code"] == "college")
    client.patch(f"/cycles/{cyc['id']}", headers=h, json={"nom": "Moyen", "periodes": 2, "bareme": 20, "seuil": 8})
    assert client.get(f"/eleves/{e4['id']}/bulletin?trimestre=2", headers=h).json()["decision"] == "Admis(e)"
    assert classes(client, h)["4ème"]["niveau"] == "Moyen"          # les classes suivent le nouveau nom
    # Statistiques
    st = client.get("/statistiques?trimestre=1", headers=h).json()
    lig = {l["classe"]: l for l in st["classes"]}
    assert lig["CE1"]["filles"] == 1 and lig["CE1"]["taux_reussite"] == 100 and lig["4ème"]["taux_reussite"] == 0
    assert st["garcons"] == 1 and st["filles"] == 1
    assert client.get("/export/statistiques.xlsx?trimestre=1", headers=h).status_code == 200


def test_saisie_par_matiere_pour_la_classe(client):
    h = complexe(client, cycles=("college",))
    cl = classes(client, h)
    cid = cl["6ème"]["id"]
    client.put("/classes/tarifs", headers=h, json=[{"classe_id": cid, "mensualite": 1000}])
    ids = [client.post("/eleves", headers=h, json={"nom": n, "prenom": "A", "classe_id": cid, "paiement": {"montant": 1000}})
           .json()["eleve"]["id"] for n in ("Ba", "Cisse", "Dia")]
    mats = {m["nom"]: m["id"] for m in client.get("/matieres", headers=h).json()}
    prof = creer_compte(client, h, "enseignant", "maths@c.sn", [{"matiere_id": mats["Mathématiques"], "classe_id": cid}])
    url = f"/notes/classe?classe_id={cid}&matiere_id={mats['Mathématiques']}&trimestre=1"
    g = client.get(url, headers=prof).json()
    assert g["modifiable"] and [l["eleve"] for l in g["lignes"]] == ["Ba A", "Cisse A", "Dia A"] and g["coefficient"] == 4
    r = client.put(url, headers=prof, json=[{"eleve_id": ids[0], "devoirs": [12, 14], "composition": 16, "appreciation": "Bien"},
                                             {"eleve_id": ids[1], "composition": 8}]).json()
    assert r["lignes"][0]["moyenne"] == 15 and r["lignes"][0]["appreciation"] == "Bien" and r["lignes"][1]["moyenne"] == 8
    assert client.put(url, headers=prof, json=[{"eleve_id": ids[2], "composition": 21}]).status_code == 422
    autre = url.replace(f"matiere_id={mats['Mathématiques']}", f"matiere_id={mats['Français']}")
    assert client.put(autre, headers=prof, json=[{"eleve_id": ids[0], "composition": 10}]).status_code == 403
    # Visible aussi dans la grille de l'élève et dans l'historique
    case = next(c for c in client.get(f"/notes/grille?eleve_id={ids[0]}&trimestre=1", headers=h).json()
                if c["matiere"] == "Mathématiques")
    assert case["devoirs"] == [12, 14] and case["saisi_par"] == "Enseignant"
    # Publication
    pub = client.get("/notes/publications?trimestre=1", headers=h).json()
    assert pub[0]["classe"] == "6ème" and pub[0]["notes_saisies"] == 2 and not pub[0]["publie"]
    assert client.get("/notes/publications?trimestre=1", headers=prof).status_code == 403
    assert client.put(f"/notes/publications/{cid}?trimestre=1", headers=h).json()["publie"]
    assert not client.delete(f"/notes/publications/{cid}?trimestre=1", headers=h).json()["publie"]
    # Cahier de textes
    e = client.post("/cahier", headers=prof, json={"classe_id": cid, "matiere_id": mats["Mathématiques"], "jour": str(date.today()),
                                                   "contenu": "Fractions : addition", "travail": "Exercices 3 et 4 p. 21"})
    assert e.status_code == 201 and e.json()["enseignant"] == "Enseignant"
    assert client.post("/cahier", headers=prof, json={"classe_id": cid, "matiere_id": mats["Français"], "jour": str(date.today()),
                                                      "contenu": "Dictée"}).status_code == 403
    assert len(client.get(f"/cahier?classe_id={cid}", headers=h).json()) == 1
    tok = client.post(f"/eleves/{ids[0]}/code-parent", headers=h).json()
    hp = {"Authorization": "Bearer " + client.post("/parents/connexion", json={"matricule": tok["matricule"], "code": tok["code"]}).json()["access_token"]}
    esp = client.get("/parents/moi", headers=hp).json()
    assert esp["cahier"][0]["travail"].startswith("Exercices") and esp["periode"] == "Semestre" and esp["bulletins"] == []


def test_caisse_du_jour_et_tableau_de_bord(client):
    h = complexe(client, cycles=("primaire",))
    cl = classes(client, h)
    client.put("/classes/tarifs", headers=h, json=[{"classe_id": cl["CI"]["id"], "mensualite": 20000, "frais_inscription": 15000}])
    ins = next(t for t in client.get("/frais", headers=h).json() if t["code"] == "inscription")
    e = client.post("/eleves", headers=h, json={"nom": "Sarr", "prenom": "Ami", "sexe": "F", "classe_id": cl["CI"]["id"],
                                                "paiement": {"montant": 15000, "type_frais_id": ins["id"]}}).json()["eleve"]
    compta = creer_compte(client, h, "comptable", "c@c.sn")
    client.post("/paiements", headers=compta, json={"eleve_id": e["id"], "montant": 20000, "mode": "orange_money"})
    client.post("/recettes", headers=compta, json={"categorie": "vente", "libelle": "Tenues", "montant": 5000})
    client.post("/depenses", headers=h, json={"categorie": "fournitures", "libelle": "Craie", "montant": 3000})
    j = client.get("/caisse/jour", headers=compta).json()
    assert j["entrees"] == 40000 and j["sorties"] == 3000 and j["solde"] == 37000
    assert j["par_mode"] == {"especes": 17000, "orange_money": 20000}
    assert j["par_caissier"] == {"M. Ndiaye": 15000, "Comptable": 25000}
    assert client.get("/caisse/bilan", headers=h).json()["total_recettes"] == 5000
    st = client.get("/stats", headers=h).json()
    assert st["encaisse_jour"] == 40000 and st["encaisse_mois"] == 40000 and st["filles"] == 1
    s = client.get(f"/eleves/{e['id']}/solde", headers=h).json()
    assert s["retard_autres"] == 0 and (s["paye_jusqua"] == "Octobre" or s["paye_jusqua"] is None)


def test_personnel_sans_compte_et_absences(client):
    h = complexe(client, cycles=("college",))
    cl = classes(client, h)
    mats = {m["nom"]: m["id"] for m in client.get("/matieres", headers=h).json()}
    creer_compte(client, h, "enseignant", "p@c.sn", [{"matiere_id": mats["Mathématiques"], "classe_id": cl["6ème"]["id"]}])
    prof = next(u["id"] for u in client.get("/utilisateurs", headers=h).json() if u["email"] == "p@c.sn")
    client.post("/creneaux", headers=h, json={"classe_id": cl["6ème"]["id"], "jour": 1, "debut": "08:00", "fin": "11:00",
                                              "matiere_id": mats["Mathématiques"], "enseignant_id": prof})
    postes = {p["nom"]: p for p in client.get("/paie/postes", headers=h).json()}
    client.patch(f"/paie/postes/{postes['Enseignant']['id']}", headers=h, json={"nom": "Enseignant", "type": "horaire", "montant": 3000})
    appui = postes["Personnel d'appui"]["id"]
    client.patch(f"/paie/postes/{appui}", headers=h,
                 json={"nom": "Gardien", "type": "mensuel", "montant": 78000})
    g = client.post("/paie/agents", headers=h, json={"nom": "Ousmane Gardien", "telephone": "771234567"}).json()
    assert g["sans_compte"] and g["poste"] == "Gardien" and g["contrat_montant"] == 78000
    # Un agent ne peut pas se connecter, ni recevoir un mot de passe
    agent = next(u for u in client.get("/utilisateurs", headers=h).json() if u["nom"] == "Ousmane Gardien")
    assert client.post(f"/utilisateurs/{agent['id']}/mot-de-passe", headers=h, json={"nouveau": "motdepasse9"}).status_code == 400
    # Absences du mois : lundi 2 novembre 2026 (3 h de cours) pour le prof, une journée pour le gardien
    client.post("/paie/absences", headers=h, json={"utilisateur_id": prof, "jour": "2026-11-02"})
    client.post("/paie/absences", headers=h, json={"utilisateur_id": g["utilisateur_id"], "jour": "2026-11-03", "motif": "Malade"})
    assert client.post("/paie/absences", headers=h, json={"utilisateur_id": prof, "jour": "2026-11-02"}).status_code == 400
    pers = {p["nom"]: p for p in client.get("/paie/personnel?mois=2026-11", headers=h).json()}
    assert pers["Enseignant"]["heures_prevues"] == 15 and pers["Enseignant"]["heures_absence"] == 3
    assert pers["Ousmane Gardien"]["jours_absence"] == 1 and pers["Ousmane Gardien"]["retenue_proposee"] == 3000
    fp = client.post("/paie/fiches", headers=h, json={"utilisateur_id": prof, "mois": "2026-11"}).json()
    assert fp["heures"] == 12 and fp["brut"] == 36000
    fg = client.post("/paie/fiches", headers=h, json={"utilisateur_id": g["utilisateur_id"], "mois": "2026-11"}).json()
    assert fg["retenues"] == 3000 and fg["net"] == 75000
    # Absence justifiée : plus de retenue
    a = next(x for x in client.get("/paie/absences?mois=2026-11", headers=h).json() if x["utilisateur_id"] == g["utilisateur_id"])
    client.patch(f"/paie/absences/{a['id']}?justifiee=true", headers=h)
    assert {p["nom"]: p for p in client.get("/paie/personnel?mois=2026-11", headers=h).json()}["Ousmane Gardien"]["retenue_proposee"] == 0
    assert client.delete(f"/paie/agents/{g['utilisateur_id']}", headers=h).status_code == 204
    assert "Ousmane Gardien" not in {p["nom"] for p in client.get("/paie/personnel?mois=2026-12", headers=h).json()}


def test_remplir_ecole_de_test(client):
    from app.database import get_session
    from app.donnees_test import remplir
    from app.main import app
    from app.routers.auth import verifier_affectations
    from app.models import AffectationSaisie
    session = next(app.dependency_overrides[get_session]())
    r = remplir(session, "Complexe Test", "senegal", "Directeur Test", "dir@test-complexe.sn", "motdepasse1", graine=1)
    assert r["classes"] == 16 and r["eleves"] == 48 and r["creneaux"] > 250
    h = {"Authorization": "Bearer " + client.post("/auth/token", data={"username": "dir@test-complexe.sn",
                                                                       "password": "motdepasse1"}).json()["access_token"]}
    assert len(client.get("/eleves", headers=h).json()) == 48
    users = client.get("/utilisateurs", headers=h).json()
    profs = [u for u in users if u["role"] == "enseignant"]
    cl = {c["nom"]: c["id"] for c in client.get("/classes", headers=h).json()}
    mats = {m["nom"]: m["id"] for m in client.get("/matieres", headers=h).json()}
    # Un maître par classe au primaire (toutes les matières), profs à 2 matières au plus au collège, 1 au lycée
    maitre_cp = [u for u in profs if any(a["classe_id"] == cl["CP"] for a in u["affectations"])]
    assert len(maitre_cp) == 1 and {a["classe_id"] for a in maitre_cp[0]["affectations"]} == {cl["CP"]}
    assert len(maitre_cp[0]["affectations"]) == 11
    for u in profs:   # toutes les attributions respectent les règles du complexe
        verifier_affectations(session, u["ecole_id"], [AffectationSaisie(**a) for a in u["affectations"]])
    maths_college = next(u for u in profs if any(a["classe_id"] == cl["6ème"] and a["matiere_id"] == mats["Mathématiques"]
                                                 for a in u["affectations"]))
    assert {a["matiere_id"] for a in maths_college["affectations"]} == {mats["Mathématiques"], mats["Physique-Chimie"]}
    philo = next(u for u in profs if any(a["matiere_id"] == mats["Philosophie"] for a in u["affectations"]))
    assert {a["matiere_id"] for a in philo["affectations"]} == {mats["Philosophie"]}
    # Le prof de maths du collège se connecte, saisit, et a son emploi du temps sans conflit
    hp = {"Authorization": "Bearer " + client.post("/auth/token", data={"username": maths_college["email"],
                                                                        "password": r["mot_de_passe_equipe"]}).json()["access_token"]}
    n = client.get(f"/notes/classe?classe_id={cl['6ème']}&matiere_id={mats['Mathématiques']}&trimestre=1", headers=hp).json()
    assert n["modifiable"] and len(n["lignes"]) == 3 and all(l["moyenne"] is not None for l in n["lignes"])
    edt = client.get("/emploi-du-temps", headers=hp).json()
    assert edt and len({(c["jour"], c["debut"]) for c in edt}) == len(edt)
    for nom in ("CP", "6ème", "Terminale S"):
        e = client.get(f"/emploi-du-temps?classe_id={cl[nom]}", headers=h).json()
        assert len(e) >= 15 and len({(c["jour"], c["debut"]) for c in e}) == len(e)
    pers = client.get("/paie/personnel?mois=2026-11", headers=h).json()
    assert sum(1 for p in pers if p["sans_compte"]) == 3 and all(p["contrat_montant"] for p in pers)
    assert next(p for p in pers if p["nom"] == maths_college["nom"])["heures_prevues"] > 0
    assert client.get(f"/classes/{cl['CP']}/bulletins.pdf?trimestre=1", headers=h).content.startswith(b"%PDF")


def test_regles_des_enseignants(client):
    h = complexe(client, email="regles@c.sn")
    cl = classes(client, h)
    mats = {m["nom"]: m["id"] for m in client.get("/matieres", headers=h).json()}
    aff = lambda *paires: [{"matiere_id": mats[m], "classe_id": cl[c]["id"]} for m, c in paires]  # noqa: E731
    def creer(email, *paires):
        return client.post("/utilisateurs", headers=h, json={"nom": "P", "email": email, "role": "enseignant",
                                                            "mot_de_passe": "motdepasse1", "affectations": aff(*paires)})
    # Un prof du collège n'enseigne pas au lycée
    r = creer("a@c.sn", ("Mathématiques", "3ème"), ("Mathématiques", "2nde S"))
    assert r.status_code == 400 and "seul cycle" in r.json()["detail"]
    # Collège : 2 matières au plus ; lycée : une seule
    assert creer("b@c.sn", ("Mathématiques", "6ème"), ("SVT", "6ème")).status_code == 201
    r = creer("c@c.sn", ("Mathématiques", "6ème"), ("SVT", "6ème"), ("Français", "5ème"))
    assert r.status_code == 400 and "au plus 2" in r.json()["detail"]
    assert creer("d@c.sn", ("Mathématiques", "2nde S"), ("Physique-Chimie", "2nde S")).status_code == 400
    # Une matière qui n'existe pas dans la classe
    r = creer("e@c.sn", ("Philosophie", "6ème"))
    assert r.status_code == 400 and "pas enseignée" in r.json()["detail"]
    # Primaire : le maître a toutes les matières de sa classe
    cp = [(m["matiere"], "CP") for m in client.get(f"/matieres/coefficients?classe_id={cl['CP']['id']}", headers=h).json()]
    assert creer("f@c.sn", *cp).status_code == 201
    # Emploi du temps : le prof doit enseigner cette matière dans cette classe
    pid = next(u["id"] for u in client.get("/utilisateurs", headers=h).json() if u["email"] == "b@c.sn")
    cr = lambda m: client.post("/creneaux", headers=h, json={"classe_id": cl["6ème"]["id"], "jour": 2, "debut": "08:00",  # noqa: E731
                                                            "fin": "09:00", "matiere_id": mats[m], "enseignant_id": pid})
    r = cr("Français")
    assert r.status_code == 400 and "n'enseigne pas" in r.json()["detail"]
    assert cr("SVT").status_code == 201


def test_niveau_personnalise(client):
    h = complexe(client, email="niv@c.sn", cycles=("primaire", "college"))
    cyc = {c["code"]: c for c in client.get("/cycles", headers=h).json()}
    r = client.post("/cycles", headers=h, json={"nom": "Maternelle", "periodes": 3, "bareme": 10, "seuil": 5,
                                                "enseignement": "classe", "avant_cycle_id": cyc["primaire"]["id"],
                                                "classes": ["Maternelle 1", "Maternelle 2"]})
    assert r.status_code == 201, r.text
    noms = [c["nom"] for c in client.get("/classes", headers=h).json()]
    assert noms[:3] == ["Maternelle 1", "Maternelle 2", "CI"]                    # placé avant le primaire
    assert [c["nom"] for c in client.get("/cycles", headers=h).json()] == ["Maternelle", "Primaire", "Collège"]
    assert client.post("/cycles", headers=h, json={"nom": "maternelle", "periodes": 3, "bareme": 10, "seuil": 5}).status_code == 400
    assert client.post("/cycles", headers=h, json={"nom": "Pro", "periodes": 2, "bareme": 20, "seuil": 10,
                                                   "classes": ["CI"]}).status_code == 400   # classe existante
    # Une matière propre au niveau, et les règles s'appliquent (notes sur 10)
    m = client.post("/matieres", headers=h, json={"nom": "Éveil musical", "coefficient": 1}).json()
    client.put(f"/matieres/{m['id']}/cycles", headers=h, json=[{"cycle_id": r.json()["id"], "coefficient": 1}])
    cl = classes(client, h)
    mats = [x["matiere"] for x in client.get(f"/matieres/coefficients?classe_id={cl['Maternelle 1']['id']}", headers=h).json()]
    assert mats == ["Éveil musical"]
    assert "Éveil musical" not in [x["matiere"] for x in client.get(f"/matieres/coefficients?classe_id={cl['CI']['id']}", headers=h).json()]
