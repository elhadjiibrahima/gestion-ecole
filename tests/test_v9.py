"""Aide au comptable et au directeur : frais au mois, familles, guichet, tiers, délais, promesses, relances,
clôture de caisse, alertes, prévision, rapprochement Wave / Orange Money, bulletin retenu."""
import io
from datetime import date, timedelta

from tests.conftest import creer_compte
from tests.test_v8 import classes, complexe


def ecole(client, email="g@c.sn"):
    h = complexe(client, email=email, cycles=("primaire", "college"))
    cl = classes(client, h)
    client.put("/classes/tarifs", headers=h, json=[{"classe_id": cl["CE1"]["id"], "mensualite": 20000, "frais_inscription": 10000},
                                                   {"classe_id": cl["6ème"]["id"], "mensualite": 30000, "frais_inscription": 10000}])
    ins = next(t for t in client.get("/frais", headers=h).json() if t["code"] == "inscription")
    return h, cl, ins


def eleve(client, h, cl, ins, nom, classe="CE1", tel="77 123 45 67", payer=True):
    r = client.post("/eleves", headers=h, json={"nom": nom, "prenom": nom[:3], "classe_id": cl[classe]["id"], "telephone_parent": tel,
                                                "paiement": {"montant": 10000, "type_frais_id": ins["id"]}})
    assert r.status_code == 201, r.text
    return r.json()["eleve"]


def test_frais_au_mois_tenue_et_famille(client):
    from app.services import mensualites
    from app.models import Ecole
    h, cl, ins = ecole(client)
    client.patch("/ecole", headers=h, json={"reduction_fratrie_pct": 10})
    tenue = client.post("/frais", headers=h, json={"nom": "Tenue", "montant": 15000, "obligatoire": True}).json()
    cantine = client.post("/frais", headers=h, json={"nom": "Cantine", "montant": 8000, "obligatoire": False, "mensuel": True}).json()
    assert cantine["mensuel"]
    a = eleve(client, h, cl, ins, "Awa", "CE1")
    b = eleve(client, h, cl, ins, "Modou", "6ème", tel="+221 77 123 45 67")       # même famille (même numéro)
    eleve(client, h, cl, ins, "Autre", "6ème", tel="76 000 00 00")
    client.put(f"/eleves/{b['id']}/frais", headers=h, json=[cantine["id"]])
    s = client.get(f"/eleves/{b['id']}/solde", headers=h).json()
    lignes = {l["nom"]: l for l in s["lignes"]}
    # 2e enfant : 10 % de réduction sur la scolarité
    assert lignes["Scolarité"]["du"] == 270000 * 9 // 10 and s["reduction_motif"] == "Réduction famille"
    assert client.get(f"/eleves/{a['id']}/solde", headers=h).json()["reduction"] == 0
    assert lignes["Tenue"]["du"] == 15000 and lignes["Tenue"]["retard"] == 15000
    # Cantine au mois : due à partir du mois de l'inscription
    from app.database import get_session
    from app.main import app
    session = next(app.dependency_overrides[get_session]())
    mois = mensualites(session.query(Ecole).first())
    debut = date.today().replace(day=1)
    applicables = [m for m in mois if mois[0].date_limite >= debut or m.date_limite >= debut]
    assert lignes["Cantine"]["mensuel"] and lignes["Cantine"]["montant_mois"] == 8000
    assert lignes["Cantine"]["du"] == 8000 * len(applicables)
    # Famille et recherche au guichet
    fam = client.get(f"/eleves/{b['id']}/famille", headers=h).json()
    assert [f["eleve_id"] for f in fam] == [b["id"], a["id"]]
    assert {x["eleve_id"] for x in client.get("/guichet/recherche?q=123 45 67", headers=h).json()} == {a["id"], b["id"]}
    assert [x["eleve_id"] for x in client.get("/guichet/recherche?q=modou", headers=h).json()] == [b["id"]]
    # Un seul paiement pour la famille : mensualité d'Awa + tenue + cantine de Modou
    r = client.post("/paiements/groupe", headers=h, json={"mode": "wave", "reference": "WV-778", "cle_client": "k1", "lignes": [
        {"eleve_id": a["id"], "montant": 20000}, {"eleve_id": a["id"], "type_frais_id": tenue["id"], "montant": 15000},
        {"eleve_id": b["id"], "type_frais_id": cantine["id"], "montant": 8000}]})
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["total"] == 43000 and len(g["paiements"]) == 3 and "wa.me/221771234567" in g["lien_whatsapp"]
    assert "43 000 FCFA" in g["message_parent"]
    assert client.post("/paiements/groupe", headers=h, json={"cle_client": "k1", "lignes": [
        {"eleve_id": a["id"], "montant": 20000}]}).json()["groupe"] == g["groupe"]               # pas de doublon
    assert client.get(f"/paiements/groupe/{g['groupe']}/recu.pdf", headers=h).content.startswith(b"%PDF")
    # Tout ou rien : une ligne trop élevée annule l'ensemble
    r = client.post("/paiements/groupe", headers=h, json={"lignes": [
        {"eleve_id": a["id"], "montant": 1000}, {"eleve_id": a["id"], "type_frais_id": tenue["id"], "montant": 1}]})
    assert r.status_code == 400
    assert len(client.get(f"/eleves/{a['id']}/paiements", headers=h).json()) == 3


def test_tiers_moratoire_promesse_relances(client):
    h, cl, ins = ecole(client, "t@c.sn")
    a = eleve(client, h, cl, ins, "Awa")
    b = eleve(client, h, cl, ins, "Binta", tel="70 111 22 33")
    t = client.post("/tiers", headers=h, json={"nom": "ONG Avenir", "contact": "M. Sy"}).json()
    s = client.put(f"/eleves/{a['id']}/prise-en-charge", headers=h, json={"tiers_id": t["id"], "pourcentage": 50}).json()
    lignes = {l["nom"]: l for l in s["lignes"]}
    assert lignes["Scolarité"]["du"] == 90000 and lignes["Scolarité — part ONG Avenir"]["du"] == 90000
    assert s["tiers"] == "ONG Avenir"
    r = client.post("/paiements/groupe", headers=h, json={"mode": "virement", "lignes": [
        {"eleve_id": a["id"], "tiers_id": t["id"], "montant": 45000}]})
    assert r.status_code == 201
    rel = client.get(f"/tiers/{t['id']}/releve", headers=h).json()
    assert rel["du"] == 90000 and rel["paye"] == 45000
    assert client.get(f"/tiers/{t['id']}/releve.pdf", headers=h).content.startswith(b"%PDF")
    # Délai accordé : plus « en retard », plus relancé
    rel_avant = {x["eleve_id"] for x in client.get("/relances", headers=h).json()}
    m = client.put(f"/eleves/{b['id']}/moratoire", headers=h, json={"jusquau": str(date.today() + timedelta(days=20)), "motif": "Décès"}).json()
    assert m["retard"] == 0 and m["moratoire_jusquau"]
    assert b["id"] not in {x["eleve_id"] for x in client.get("/relances", headers=h).json()}
    assert client.put(f"/eleves/{b['id']}/moratoire", headers=h, json={"jusquau": None}).json()["moratoire_jusquau"] is None
    # Promesse de paiement arrivée à terme
    client.post("/promesses", headers=h, json={"eleve_id": b["id"], "date_promise": str(date.today()), "montant": 20000,
                                                "note": "Le père passe vendredi"})
    rl = {x["eleve_id"]: x for x in client.get("/relances", headers=h).json()}
    if client.get(f"/eleves/{b['id']}/solde", headers=h).json()["retard"]:
        assert rl[b["id"]]["raison"] == "promesse" and "prévu de payer" in rl[b["id"]]["texte"]
    assert client.post("/relances/envoyer", headers=h).status_code == 400          # pas de fournisseur SMS
    # Alertes au directeur
    al = client.get("/alertes", headers=h).json()
    assert {x["type"] for x in al} >= {"prise_en_charge", "moratoire"}
    client.post("/alertes/lues", headers=h)
    assert client.get("/alertes", headers=h).json() == [] and client.get("/alertes?toutes=true", headers=h).json()
    compta = creer_compte(client, h, "comptable", "cpt@c.sn")
    assert client.get("/alertes", headers=compta).status_code == 403
    assert client.put(f"/eleves/{a['id']}/moratoire", headers=compta, json={"jusquau": None}).status_code == 403


def test_cloture_prevision_rapprochement_bulletin_retenu(client):
    h, cl, ins = ecole(client, "c@c.sn")
    a = eleve(client, h, cl, ins, "Awa")                              # 10 000 en espèces (inscription)
    compta = creer_compte(client, h, "comptable", "cc@c.sn")
    client.post("/paiements", headers=compta, json={"eleve_id": a["id"], "montant": 20000, "mode": "wave", "reference": "WV-1"})
    client.post("/paiements", headers=compta, json={"eleve_id": a["id"], "montant": 20000, "mode": "wave", "reference": "WV-2"})
    client.post("/depenses", headers=h, json={"categorie": "fournitures", "libelle": "Craie", "montant": 2000})
    # Clôture : 8 000 attendus en espèces, 7 500 comptés → écart signalé au directeur
    c = client.post("/caisse/cloture", headers=compta, json={"compte": 7500, "commentaire": "Billet manquant"}).json()
    assert c["attendu"] == 8000 and c["ecart"] == -500 and c["valide_par"] is None
    assert client.post("/caisse/cloture", headers=compta, json={"compte": 7500}).status_code == 400
    assert any(x["type"] == "ecart_caisse" for x in client.get("/alertes", headers=h).json())
    assert client.post(f"/caisse/clotures/{c['id']}/valider", headers=compta).status_code == 403
    assert client.post(f"/caisse/clotures/{c['id']}/valider", headers=h).json()["valide_par"] == "M. Ndiaye"
    # Prévision de trésorerie
    pv = client.get("/caisse/prevision", headers=h).json()
    assert pv and all(set(m) >= {"mois", "attendu", "salaires", "solde_prevu"} for m in pv)
    assert sum(m["attendu"] for m in pv) == client.get(f"/eleves/{a['id']}/solde", headers=h).json()["reste_a_payer"]
    # Rapprochement : WV-1 trouvé, WV-2 absent du relevé, WV-9 pas saisi, WV-3 montant différent
    client.post("/paiements", headers=compta, json={"eleve_id": a["id"], "montant": 5000, "mode": "wave", "reference": "WV-3"})
    j = date.today().strftime("%d/%m/%Y")
    csv = f"Date;Référence;Montant;Expéditeur\n{j};WV-1;20 000;771234567\n{j};WV-9;15000;770000000\n{j};WV-3;6000;77\n"
    r = client.post("/caisse/rapprochement", headers=h, data={"mode": "wave"},
                    files={"fichier": ("releve.csv", io.BytesIO(csv.encode()), "text/csv")}).json()
    st = {l["reference"]: l["statut"] for l in r["lignes"]}
    assert st == {"WV-1": "trouve", "WV-9": "absent_logiciel", "WV-3": "montant_different", "WV-2": "absent_releve"}
    assert (r["trouves"], r["absents_logiciel"], r["absents_releve"], r["differences"]) == (1, 1, 1, 1)
    # Bulletin retenu tant que la scolarité n'est pas à jour
    client.patch("/ecole", headers=h, json={"bloquer_bulletin_impaye": True})
    b = eleve(client, h, cl, ins, "Binta", tel="70 999 99 99")
    tok = client.post(f"/eleves/{b['id']}/code-parent", headers=h).json()
    hp = {"Authorization": "Bearer " + client.post("/parents/connexion", json={"matricule": tok["matricule"], "code": tok["code"]}).json()["access_token"]}
    client.put(f"/notes/publications/{cl['CE1']['id']}?trimestre=1", headers=h)
    retard = client.get(f"/eleves/{b['id']}/solde", headers=h).json()["retard_scolarite"]
    esp = client.get("/parents/moi", headers=hp).json()
    assert esp["bulletins_retenus"] == (retard > 0)
    if retard:
        assert client.get("/parents/bulletin.pdf?trimestre=1", headers=hp).status_code == 403
