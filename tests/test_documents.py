import io
from tests.conftest import creer_compte, inscrire
from datetime import date

from openpyxl import Workbook, load_workbook

from app.pdf import en_lettres


def test_en_lettres():
    assert en_lettres(1) == "un"
    assert en_lettres(71) == "soixante et onze"
    assert en_lettres(80) == "quatre-vingts"
    assert en_lettres(200) == "deux cents"
    assert en_lettres(1000) == "mille"
    assert en_lettres(250000) == "deux cent cinquante mille"
    assert en_lettres(200000) == "deux cent mille"
    assert en_lettres(1_500_000) == "un million cinq cent mille"


def test_recu_pdf(client, dir_headers, setup):
    _, eleve = setup
    p = client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 150000}).json()
    r = client.get(f"/paiements/{p['id']}/recu.pdf", headers=dir_headers)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_bulletins_pdf(client, dir_headers, setup):
    classe, eleve = setup
    client.post("/notes", headers=dir_headers, json={
        "eleve_id": eleve["id"], "matiere": "Maths", "valeur": 12, "trimestre": 1})
    r = client.get(f"/eleves/{eleve['id']}/bulletin.pdf?trimestre=1", headers=dir_headers)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = client.get(f"/classes/{classe['id']}/bulletins.pdf?trimestre=1", headers=dir_headers)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "6eme_A" in r.headers["content-disposition"]


def _xlsx(lignes):
    wb = Workbook()
    ws = wb.active
    ws.title = "Eleves"
    ws.append(["nom", "prenom", "classe", "date_naissance", "telephone_parent",
               "montant_paye", "mode_paiement", "reference_paiement"])
    for l in lignes:
        ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _importer(client, headers, contenu, nom="eleves.xlsx"):
    return client.post("/eleves/import", headers=headers,
                       files={"fichier": (nom, contenu, "application/octet-stream")})


def test_modele_import(client, dir_headers, setup):
    r = client.get("/eleves/modele-import.xlsx", headers=dir_headers)
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert [c.value for c in wb["Eleves"][1]] == ["nom", "prenom", "classe", "date_naissance", "telephone_parent",
                                                  "montant_paye", "mode_paiement", "reference_paiement"]
    assert wb["Classes"]["A1"].value == "6ème A"


def test_import_ok(client, dir_headers, setup):
    contenu = _xlsx([
        ["Camara", "Fatou", "6ème A", "15/03/2014", "+224 621", 500000, "Orange Money", "OM77"],
        ["Sow", "Ibrahima", "6ÈME A ", date(2013, 1, 2), None, "150 000 FCFA", None, None],  # casse, vraie date, montant texte
        [None, None, None, None, None, None, None, None],                                    # ligne vide ignorée
    ])
    r = _importer(client, dir_headers, contenu).json()
    assert r == {"importes": 2, "erreurs": []}
    noms = [e["nom"] for e in client.get("/eleves", headers=dir_headers).json()]
    assert set(noms) == {"Diallo", "Camara", "Sow"}
    soldes = {s["nom_complet"]: s for s in client.get("/eleves/soldes", headers=dir_headers).json()}
    assert soldes["Fatou Camara"]["statut"] == "a_jour"
    assert soldes["Ibrahima Sow"]["total_paye"] == 150000 and soldes["Ibrahima Sow"]["reste_a_payer"] == 350000
    sid = soldes["Fatou Camara"]["eleve_id"]
    p = client.get(f"/eleves/{sid}/paiements", headers=dir_headers).json()
    assert p[0]["mode"] == "orange_money" and p[0]["reference"] == "OM77" and p[0]["numero_recu"]


def test_import_erreurs_rien_importe(client, dir_headers, setup):
    contenu = _xlsx([
        ["Camara", "Fatou", "6ème A", "15/03/2014", None, 100000],
        ["Sow", None, "6ème A", None, None, 100000],
        ["Bah", "Ali", "CM2", None, None, 100000],
        ["Barry", "Awa", "6ème A", "32/13/2014", None, 100000],
        ["Keïta", "Sékou", "6ème A", None, None, None],                    # pas de paiement
        ["Touré", "Hawa", "6ème A", None, None, 600000],                   # plus que les frais
        ["Sylla", "Binta", "6ème A", None, None, 1000, "Chèque"],          # mode inconnu
        ["Kaba", "Moussa", "6ème A", None, None, "beaucoup"],              # pas un nombre
    ])
    r = _importer(client, dir_headers, contenu).json()
    assert r["importes"] == 0 and len(r["erreurs"]) == 7
    assert any("Ligne 6" in e and "montant_paye" in e for e in r["erreurs"])
    assert any("Ligne 7" in e and "supérieur" in e for e in r["erreurs"])
    assert any("Chèque" in e for e in r["erreurs"])
    assert any("Ligne 3" in e and "prenom" in e for e in r["erreurs"])
    assert any("CM2" in e for e in r["erreurs"])
    assert len(client.get("/eleves", headers=dir_headers).json()) == 1   # rien ajouté


def test_import_mauvais_fichier(client, dir_headers):
    assert _importer(client, dir_headers, b"pas un excel", "eleves.csv").status_code == 400
    r = _importer(client, dir_headers, b"pas un excel").json()
    assert r["importes"] == 0 and "Impossible de lire" in r["erreurs"][0]


def test_soldes_statuts_et_etat_pdf(client, dir_headers, setup):
    classe, eleve = setup
    inscrire(client, dir_headers, classe["id"], "Bah", "Ali", montant=100000)
    _importer(client, dir_headers, _xlsx([["Sow", "Awa", "6ème A", None, None, 1000]]))
    client.post("/paiements", headers=dir_headers, json={"eleve_id": eleve["id"], "montant": 450000})
    soldes = client.get(f"/eleves/soldes?classe_id={classe['id']}", headers=dir_headers).json()
    assert [s["nom_complet"] for s in soldes] == ["Ali Bah", "Aminata Diallo", "Awa Sow"]   # tri par nom
    statuts = {s["nom_complet"]: (s["statut"], s["nb_paiements"]) for s in soldes}
    assert statuts == {"Aminata Diallo": ("a_jour", 2), "Ali Bah": ("partiel", 1), "Awa Sow": ("partiel", 1)}
    r = client.get(f"/classes/{classe['id']}/paiements.pdf", headers=dir_headers)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    prof = creer_compte(client, dir_headers, "enseignant", "p@test.gn")
    assert client.get(f"/classes/{classe['id']}/paiements.pdf", headers=prof).status_code == 403
