import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app


@pytest.fixture
def client():
    # Base SQLite en mémoire, isolée pour chaque test
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _setup(client):
    classe = client.post(
        "/classes", json={"nom": "6ème A", "niveau": "Collège", "frais_annuels": 500000}
    ).json()
    eleve = client.post(
        "/eleves",
        json={"nom": "Diallo", "prenom": "Aminata", "classe_id": classe["id"],
              "telephone_parent": "+224620000000"},
    ).json()
    return classe, eleve


def test_inscription_et_matricule(client):
    _, eleve = _setup(client)
    assert eleve["matricule"].endswith("-0001")
    assert client.get("/eleves", params={"q": "amin"}).json()[0]["id"] == eleve["id"]


def test_classe_inexistante(client):
    r = client.post("/eleves", json={"nom": "X", "prenom": "Y", "classe_id": 99})
    assert r.status_code == 400


def test_paiements_solde_et_impayes(client):
    _, eleve = _setup(client)
    client.post("/paiements", json={"eleve_id": eleve["id"], "montant": 200000,
                                    "mode": "orange_money", "reference": "OM123"})
    client.post("/paiements", json={"eleve_id": eleve["id"], "montant": 100000})

    solde = client.get(f"/eleves/{eleve['id']}/solde").json()
    assert solde["total_paye"] == 300000
    assert solde["reste_a_payer"] == 200000

    impayes = client.get("/impayes").json()
    assert len(impayes) == 1 and impayes[0]["reste_a_payer"] == 200000

    client.post("/paiements", json={"eleve_id": eleve["id"], "montant": 200000})
    assert client.get("/impayes").json() == []


def test_montant_negatif_refuse(client):
    _, eleve = _setup(client)
    r = client.post("/paiements", json={"eleve_id": eleve["id"], "montant": -5})
    assert r.status_code == 422


def test_bulletin(client):
    _, eleve = _setup(client)
    for matiere, valeur, coef in [("Maths", 14, 4), ("Maths", 16, 4), ("Français", 12, 3)]:
        client.post("/notes", json={"eleve_id": eleve["id"], "matiere": matiere,
                                    "valeur": valeur, "coefficient": coef, "trimestre": 1})
    b = client.get(f"/eleves/{eleve['id']}/bulletin", params={"trimestre": 1}).json()
    maths = next(m for m in b["matieres"] if m["matiere"] == "Maths")
    assert maths["moyenne"] == 15
    # (15*4 + 12*3) / 7 = 13.71
    assert b["moyenne_generale"] == 13.71


def test_note_hors_bareme(client):
    _, eleve = _setup(client)
    r = client.post("/notes", json={"eleve_id": eleve["id"], "matiere": "Maths",
                                    "valeur": 25, "trimestre": 1})
    assert r.status_code == 422
