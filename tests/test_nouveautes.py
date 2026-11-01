import io
from datetime import date, timedelta

from openpyxl import load_workbook

from tests.conftest import creer_compte, creer_ecole, inscrire


def _matieres(client, h):
    return {m["nom"]: m for m in client.get("/matieres", headers=h).json()}


# ---------- 1. Annulation de paiement ----------
def test_annulation_paiement(client, dir_headers, setup):
    _, eleve = setup                                   # déjà 50 000 payés à l'inscription
    p = client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 100000}).json()
    assert client.get(f"/eleves/{eleve['id']}/solde", headers=dir_headers).json()["total_paye"] == 150000

    assert client.post(f"/paiements/{p['id']}/annuler", headers=dir_headers, json={"motif": "x"}).status_code == 422
    r = client.post(f"/paiements/{p['id']}/annuler", headers=dir_headers, json={"motif": "Erreur de montant"})
    assert r.status_code == 200 and r.json()["annule"] and r.json()["annule_par"] == "M. Directeur"
    assert client.post(f"/paiements/{p['id']}/annuler", headers=dir_headers, json={"motif": "encore"}).status_code == 400

    # Le solde, les stats et l'historique en tiennent compte ; le paiement reste visible
    assert client.get(f"/eleves/{eleve['id']}/solde", headers=dir_headers).json()["total_paye"] == 50000
    assert client.get("/stats", headers=dir_headers).json()["total_encaisse"] == 50000
    h = client.get(f"/eleves/{eleve['id']}/paiements", headers=dir_headers).json()
    assert [x["annule"] for x in h] == [False, True] and h[1]["motif_annulation"] == "Erreur de montant"
    assert h[0]["enregistre_par"] == "M. Directeur"
    assert client.get(f"/paiements/{p['id']}/recu.pdf", headers=dir_headers).content.startswith(b"%PDF")

    compta = creer_compte(client, dir_headers, "comptable", "c@test.gn")
    p2 = client.post("/paiements", headers=compta, json={"eleve_id": eleve["id"], "montant": 1000}).json()
    assert client.post(f"/paiements/{p2['id']}/annuler", headers=compta, json={"motif": "test"}).status_code == 403


# ---------- 2. Mots de passe ----------
def test_changer_mot_de_passe(client, dir_headers):
    r = client.post("/auth/mot-de-passe", headers=dir_headers, json={"ancien": "faux", "nouveau": "nouveau123"})
    assert r.status_code == 400
    assert client.post("/auth/mot-de-passe", headers=dir_headers,
                       json={"ancien": "motdepasse123", "nouveau": "court"}).status_code == 422
    assert client.post("/auth/mot-de-passe", headers=dir_headers,
                       json={"ancien": "motdepasse123", "nouveau": "nouveau123"}).status_code == 204
    assert client.post("/auth/token", data={"username": "directeur@test.gn", "password": "motdepasse123"}).status_code == 401
    assert client.post("/auth/token", data={"username": "directeur@test.gn", "password": "nouveau123"}).status_code == 200


def test_reinitialiser_mot_de_passe(client, dir_headers):
    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn")
    uid = next(u["id"] for u in client.get("/utilisateurs", headers=dir_headers).json() if u["email"] == "p@test.gn")
    assert client.post(f"/utilisateurs/{uid}/mot-de-passe", headers=prof, json={"nouveau": "pirate123"}).status_code == 403
    assert client.post(f"/utilisateurs/{uid}/mot-de-passe", headers=dir_headers, json={"nouveau": "provisoire1"}).status_code == 204
    assert client.post("/auth/token", data={"username": "p@test.gn", "password": "provisoire1"}).status_code == 200
    autre = creer_ecole(client, nom="Autre", email="autre@test.gn")
    assert client.post(f"/utilisateurs/{uid}/mot-de-passe", headers=autre, json={"nouveau": "pirate123"}).status_code == 404


# ---------- 3. Exports Excel ----------
def test_exports(client, dir_headers, setup):
    _, eleve = setup
    m = _matieres(client, dir_headers)
    client.put(f"/notes/grille?eleve_id={eleve['id']}&trimestre=1", headers=dir_headers,
               json=[{"matiere_id": m["Mathématiques"]["id"], "valeur": 15}])
    p = client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 1000}).json()
    client.post(f"/paiements/{p['id']}/annuler", headers=dir_headers, json={"motif": "doublon"})

    wb = load_workbook(io.BytesIO(client.get("/export/eleves.xlsx", headers=dir_headers).content))
    ws = wb["Eleves"]
    assert ws["B2"].value == "Diallo" and ws["H2"].value == 50000 and ws["J2"].value == "Partiel"

    r = client.get("/export/paiements.xlsx", headers=dir_headers)
    assert "attachment" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content))["Paiements"]
    assert [ws.cell(i, 11).value for i in (2, 3)] == ["Valide", "ANNULÉ"] and ws["L3"].value == "doublon"

    wb = load_workbook(io.BytesIO(client.get("/export/notes.xlsx?trimestre=1", headers=dir_headers).content))
    ws = wb["6ème A"]
    entetes = [c.value for c in ws[1]]
    assert ws.cell(2, entetes.index("Mathématiques") + 1).value == 15
    assert ws.cell(2, entetes.index("Rang") + 1).value == 1

    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn")
    assert client.get("/export/paiements.xlsx", headers=prof).status_code == 403
    assert client.get("/export/notes.xlsx?trimestre=1", headers=prof).status_code == 200


# ---------- 5. Absences ----------
def test_appel_absences_et_bulletin(client, dir_headers, setup):
    classe, eleve = setup
    b = inscrire(client, dir_headers, classe["id"], "Bah", "Ali")
    hier = (date.today() - timedelta(days=1)).isoformat()
    aujourd = date.today().isoformat()

    r = client.post("/absences/appel", headers=dir_headers, json={
        "classe_id": classe["id"], "jour": hier, "trimestre": 1, "absents": [eleve["id"]], "retards": [b["id"]]})
    assert r.json() == {"absents": [eleve["id"]], "retards": [b["id"]]}
    client.post("/absences/appel", headers=dir_headers, json={
        "classe_id": classe["id"], "jour": aujourd, "trimestre": 1, "absents": [eleve["id"]]})

    liste = client.get(f"/absences?eleve_id={eleve['id']}", headers=dir_headers).json()
    assert len(liste) == 2 and liste[0]["jour"] == aujourd   # plus récent d'abord
    client.patch(f"/absences/{liste[1]['id']}", headers=dir_headers, json={"justifiee": True, "motif": "Malade"})

    # Refaire l'appel d'hier sans changement : la justification est conservée
    client.post("/absences/appel", headers=dir_headers, json={
        "classe_id": classe["id"], "jour": hier, "trimestre": 1, "absents": [eleve["id"]], "retards": [b["id"]]})
    assert client.get(f"/absences?eleve_id={eleve['id']}", headers=dir_headers).json()[1]["justifiee"] is True
    assert client.get(f"/absences/appel?classe_id={classe['id']}&jour={hier}", headers=dir_headers).json()["retards"] == [b["id"]]

    bull = client.get(f"/eleves/{eleve['id']}/bulletin?trimestre=1", headers=dir_headers).json()
    assert (bull["absences"], bull["absences_justifiees"], bull["retards"]) == (2, 1, 0)
    assert client.get(f"/eleves/{b['id']}/bulletin?trimestre=1", headers=dir_headers).json()["retards"] == 1
    res = {r["eleve"]: r for r in client.get(f"/absences/resume?classe_id={classe['id']}", headers=dir_headers).json()}
    assert res["Aminata Diallo"]["absences"] == 2 and res["Ali Bah"]["retards"] == 1

    # Corriger l'appel : Ali n'était finalement pas en retard
    client.post("/absences/appel", headers=dir_headers, json={
        "classe_id": classe["id"], "jour": hier, "trimestre": 1, "absents": [eleve["id"]]})
    assert client.get(f"/eleves/{b['id']}/bulletin?trimestre=1", headers=dir_headers).json()["retards"] == 0
    assert client.get(f"/eleves/{eleve['id']}/bulletin.pdf?trimestre=1", headers=dir_headers).content.startswith(b"%PDF")


def test_appel_controles(client, dir_headers, setup):
    classe, eleve = setup
    demain = (date.today() + timedelta(days=1)).isoformat()
    assert client.post("/absences/appel", headers=dir_headers, json={
        "classe_id": classe["id"], "jour": demain, "trimestre": 1, "absents": [eleve["id"]]}).status_code == 400
    assert client.post("/absences/appel", headers=dir_headers, json={
        "classe_id": classe["id"], "jour": date.today().isoformat(), "trimestre": 1, "absents": [9999]}).status_code == 400
    compta = creer_compte(client, dir_headers, "comptable", "c@test.gn")
    assert client.post("/absences/appel", headers=compta, json={
        "classe_id": classe["id"], "jour": date.today().isoformat(), "trimestre": 1}).status_code == 403
    sans_classe = creer_compte(client, dir_headers, "enseignant", "p0@test.gn")
    assert client.post("/absences/appel", headers=sans_classe, json={
        "classe_id": classe["id"], "jour": date.today().isoformat(), "trimestre": 1}).status_code == 403
    m = client.get("/matieres", headers=dir_headers).json()[0]["id"]
    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn", [{"matiere_id": m, "classe_id": classe["id"]}])
    assert client.post("/absences/appel", headers=prof, json={
        "classe_id": classe["id"], "jour": date.today().isoformat(), "trimestre": 1}).status_code == 200


# ---------- 6. Messages aux parents ----------
def test_messages_parents(client, dir_headers, setup):
    from app.routers.messages import numero_international as num
    assert num("+224 620 11 22 33", "224") == "224620112233"
    assert num("620112233", "224") == "224620112233"
    assert num("00224620112233", "224") == "224620112233"
    assert num("224620112233", "224") == "224620112233"
    assert num(None, "224") is None

    classe, eleve = setup                               # téléphone +224620000000, doit 450 000
    inscrire(client, dir_headers, classe["id"], "Sow", "Awa", montant=500000)   # à jour : pas de rappel
    msgs = client.get("/messages/impayes", headers=dir_headers).json()
    assert len(msgs) == 1
    m = msgs[0]
    assert "450 000 FCFA" in m["texte"] and "Aminata Diallo" in m["texte"]
    assert m["lien_whatsapp"].startswith("https://wa.me/224620000000?text=") and m["dernier_envoi"] is None

    assert client.post("/messages/journal", headers=dir_headers,
                       json={"eleve_id": eleve["id"], "type": "impaye", "canal": "whatsapp"}).status_code == 204
    assert client.get("/messages/impayes", headers=dir_headers).json()[0]["dernier_envoi"]

    ids = _matieres(client, dir_headers)
    client.put(f"/notes/grille?eleve_id={eleve['id']}&trimestre=1", headers=dir_headers,
               json=[{"matiere_id": ids["Français"]["id"], "valeur": 13}])
    bm = {x["eleve"]: x for x in client.get(f"/messages/bulletins?classe_id={classe['id']}&trimestre=1",
                                             headers=dir_headers).json()}
    assert "13.00/20" in bm["Aminata Diallo"]["texte"] and "1er sur 2" in bm["Aminata Diallo"]["texte"]
    assert "aucune note" in bm["Awa Sow"]["texte"] and bm["Awa Sow"]["lien_whatsapp"] is None   # pas de téléphone

    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn")
    assert client.get("/messages/impayes", headers=prof).status_code == 403


# ---------- 7. Coefficients par classe ----------
def test_coefficients_par_classe(client, dir_headers, setup):
    classe_a, eleve_a = setup
    classe_b = client.post("/classes", headers=dir_headers, json={"nom": "3ème A", "niveau": "Collège", "frais_annuels": 500000}).json()
    eleve_b = inscrire(client, dir_headers, classe_b["id"], "Bah", "Ali")
    m = _matieres(client, dir_headers)
    maths, ang = m["Mathématiques"]["id"], m["Anglais"]["id"]
    for e in (eleve_a, eleve_b):
        client.put(f"/notes/grille?eleve_id={e['id']}&trimestre=1", headers=dir_headers,
                   json=[{"matiere_id": maths, "valeur": 10}, {"matiere_id": ang, "valeur": 16}])
    # Par défaut : Maths 4, Anglais 2 → (40 + 32) / 6 = 12
    assert client.get(f"/eleves/{eleve_b['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 12

    # En 3ème : Maths coef 5
    r = client.put("/matieres/coefficients", headers=dir_headers, json={
        "classe_ids": [classe_b["id"]], "coefficients": [{"matiere_id": maths, "coefficient": 5}]})
    lignes = {l["matiere"]: l for l in r.json()}
    assert lignes["Mathématiques"]["coefficient"] == 5 and lignes["Mathématiques"]["specifique"]
    assert lignes["Anglais"]["coefficient"] == 2 and not lignes["Anglais"]["specifique"]
    # (50 + 32) / 7 = 11.71 en 3ème, inchangé en 6ème
    assert client.get(f"/eleves/{eleve_b['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 11.71
    assert client.get(f"/eleves/{eleve_a['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 12
    grille = {c["matiere"]: c for c in client.get(f"/notes/grille?eleve_id={eleve_b['id']}&trimestre=1", headers=dir_headers).json()}
    assert grille["Mathématiques"]["coefficient"] == 5

    # Changer le coefficient par défaut ne touche pas la 3ème (coefficient spécifique)
    client.patch(f"/matieres/{maths}", headers=dir_headers, json={"coefficient": 3})
    assert client.get(f"/eleves/{eleve_b['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 11.71
    assert client.get(f"/eleves/{eleve_a['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 12.4  # (30+32)/5

    # Revenir au défaut
    client.put("/matieres/coefficients", headers=dir_headers, json={
        "classe_ids": [classe_b["id"]], "coefficients": [{"matiere_id": maths, "coefficient": None}]})
    assert client.get(f"/eleves/{eleve_b['id']}/bulletin?trimestre=1", headers=dir_headers).json()["moyenne_generale"] == 12.4
    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn")
    assert client.put("/matieres/coefficients", headers=prof, json={
        "classe_ids": [classe_b["id"]], "coefficients": []}).status_code == 403


# ---------- 8. Nouvelle année scolaire ----------
def test_nouvelle_annee(client, dir_headers):
    h = dir_headers
    c6 = client.post("/classes", headers=h, json={"nom": "6eme A", "niveau": "Collège", "frais_annuels": 200000}).json()
    c5 = client.post("/classes", headers=h, json={"nom": "5eme A", "niveau": "Collège", "frais_annuels": 300000}).json()
    c3 = client.post("/classes", headers=h, json={"nom": "3eme A", "niveau": "Collège", "frais_annuels": 400000}).json()
    ali = inscrire(client, h, c6["id"], "Bah", "Ali", montant=200000)       # passe en 5e
    awa = inscrire(client, h, c6["id"], "Sow", "Awa", montant=100000)       # redouble
    old = inscrire(client, h, c5["id"], "Barry", "Old", montant=300000)      # passe en 4e (absente) → reste
    fin = inscrire(client, h, c3["id"], "Kaba", "Fin", montant=400000)       # fin de cycle
    m = _matieres(client, h)
    client.put(f"/notes/grille?eleve_id={ali['id']}&trimestre=1", headers=h,
               json=[{"matiere_id": m["Français"]["id"], "valeur": 14}])

    prop = client.get("/annee/proposition", headers=h).json()
    assert prop["annee_actuelle"] == "2026-2027" and prop["annee_suivante"] == "2027-2028"
    suite = {p["classe"]: p["vers_classe_id"] for p in prop["passages"]}
    assert suite == {"6eme A": c5["id"], "5eme A": None, "3eme A": None}   # pas de 4eme A → sortie proposée

    assert client.post("/annee/nouvelle", headers=h, json={"nouvelle_annee": "2027-2029", "passages": []}).status_code == 400
    r = client.post("/annee/nouvelle", headers=h, json={"nouvelle_annee": "2027-2028", "passages": [
        {"classe_id": c6["id"], "vers_classe_id": c5["id"], "redoublants": [awa["id"]]},
        {"classe_id": c5["id"], "vers_classe_id": c5["id"]},           # pas de 4e : restent (cas test)
        {"classe_id": c3["id"], "vers_classe_id": None},
    ]})
    assert r.status_code == 200, r.text
    assert r.json() == {"annee_scolaire": "2027-2028", "passes": 2, "redoublants": 1, "sortants": 1}

    assert client.get("/auth/moi", headers=h).json()["ecole"]["annee_scolaire"] == "2027-2028"
    # Ali est en 5e, n'a encore rien payé cette année (frais de 5e), ses notes de l'an passé ne comptent plus
    s = client.get(f"/eleves/{ali['id']}/solde", headers=h).json()
    assert (s["classe"], s["total_paye"], s["reste_a_payer"], s["statut"]) == ("5eme A", 0, 300000, "rien_paye")
    assert client.get(f"/eleves/{ali['id']}/bulletin?trimestre=1", headers=h).json()["moyenne_generale"] is None
    # Awa redouble en 6e et repart de zéro
    assert client.get(f"/eleves/{awa['id']}/solde", headers=h).json()["total_paye"] == 0
    # Fin de cycle : n'apparaît plus dans les listes mais reste consultable
    assert fin["id"] not in [e["id"] for e in client.get("/eleves", headers=h).json()]
    assert fin["id"] in [e["id"] for e in client.get("/eleves?sortis=true", headers=h).json()]
    assert client.get("/stats", headers=h).json()["nb_eleves"] == 3
    assert client.post("/paiements", headers=h, json={"eleve_id": fin["id"], "montant": 1000}).status_code == 400
    # L'historique des paiements de l'an passé est conservé
    hist = client.get(f"/eleves/{ali['id']}/paiements", headers=h).json()
    assert hist[0]["annee_scolaire"] == "2026-2027" and hist[0]["montant"] == 200000
    # On ne peut pas refaire la même année
    assert client.post("/annee/nouvelle", headers=h, json={"nouvelle_annee": "2027-2028", "passages": [
        {"classe_id": c6["id"], "vers_classe_id": c5["id"]}]}).status_code == 400
    prof = creer_compte(client, h, "enseignant", "p@test.gn")
    assert client.get("/annee/proposition", headers=prof).status_code == 403


def test_proposition_guinee_monte():
    from app.models import Classe
    from app.routers.annee import proposer_passages
    cl = [Classe(id=i, nom=n, niveau="", frais_annuels=0, ecole_id=1)
          for i, n in enumerate(["7ème A", "8ème A", "9ème A", "10ème A"], start=1)]
    assert proposer_passages(cl) == {1: 2, 2: 3, 3: 4, 4: None}
