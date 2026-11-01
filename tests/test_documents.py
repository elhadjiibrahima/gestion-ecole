import io
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
    ws.append(["nom", "prenom", "classe", "date_naissance", "telephone_parent"])
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
    assert [c.value for c in wb["Eleves"][1]] == ["nom", "prenom", "classe", "date_naissance", "telephone_parent"]
    assert wb["Classes"]["A1"].value == "6ème A"


def test_import_ok(client, dir_headers, setup):
    contenu = _xlsx([
        ["Camara", "Fatou", "6ème A", "15/03/2014", "+224 621"],
        ["Sow", "Ibrahima", "6ÈME A ", date(2013, 1, 2), None],   # casse/espaces tolérés, vraie date Excel
        [None, None, None, None, None],                            # ligne vide ignorée
    ])
    r = _importer(client, dir_headers, contenu).json()
    assert r == {"importes": 2, "erreurs": []}
    noms = [e["nom"] for e in client.get("/eleves", headers=dir_headers).json()]
    assert set(noms) == {"Diallo", "Camara", "Sow"}


def test_import_erreurs_rien_importe(client, dir_headers, setup):
    contenu = _xlsx([
        ["Camara", "Fatou", "6ème A", "15/03/2014", None],
        ["Sow", None, "6ème A", None, None],
        ["Bah", "Ali", "CM2", None, None],
        ["Barry", "Awa", "6ème A", "32/13/2014", None],
    ])
    r = _importer(client, dir_headers, contenu).json()
    assert r["importes"] == 0 and len(r["erreurs"]) == 3
    assert any("Ligne 3" in e and "prenom" in e for e in r["erreurs"])
    assert any("CM2" in e for e in r["erreurs"])
    assert len(client.get("/eleves", headers=dir_headers).json()) == 1   # rien ajouté


def test_import_mauvais_fichier(client, dir_headers):
    assert _importer(client, dir_headers, b"pas un excel", "eleves.csv").status_code == 400
    r = _importer(client, dir_headers, b"pas un excel").json()
    assert r["importes"] == 0 and "Impossible de lire" in r["erreurs"][0]
