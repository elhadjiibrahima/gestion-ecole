import os

os.environ.setdefault("PBKDF2_ITERATIONS", "1000")  # tests rapides

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app


@pytest.fixture(autouse=True)
def compteurs_a_zero():
    """Les compteurs d'essais de connexion ne doivent pas passer d'un test à l'autre."""
    from app.routers import auth, parents
    auth._ECHECS.clear()
    parents._ECHECS.clear()
    yield


def toutes_matieres(client, headers, classe_id):
    """Affectations : toutes les matières de l'école dans une classe."""
    return [{"matiere_id": m["id"], "classe_id": classe_id} for m in client.get("/matieres", headers=headers).json()]


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


def creer_ecole(client, nom="École Test", email="directeur@test.gn"):
    r = client.post("/auth/inscription", json={
        "nom_ecole": nom, "nom_directeur": "M. Directeur",
        "email": email, "mot_de_passe": "motdepasse123",
    })
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def creer_compte(client, headers, role, email, affectations=None):
    r = client.post("/utilisateurs", headers=headers, json={
        "nom": role.capitalize(), "email": email, "role": role, "mot_de_passe": "motdepasse123",
        "affectations": affectations or [],
    })
    assert r.status_code == 201, r.text
    tok = client.post("/auth/token", data={"username": email, "password": "motdepasse123"}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


@pytest.fixture
def dir_headers(client):
    return creer_ecole(client)


def inscrire(client, headers, classe_id, nom, prenom, montant=50000, **extra):
    """Inscrit un élève avec son premier paiement ; renvoie l'élève."""
    r = client.post("/eleves", headers=headers, json={
        "nom": nom, "prenom": prenom, "classe_id": classe_id,
        "paiement": {"montant": montant}, **extra})
    assert r.status_code == 201, r.text
    return r.json()["eleve"]


@pytest.fixture
def setup(client, dir_headers):
    """Une classe (500 000) + un élève inscrit avec un premier paiement de 50 000."""
    classe = client.post("/classes", headers=dir_headers, json={
        "nom": "6ème A", "niveau": "Collège", "frais_annuels": 500000}).json()
    eleve = inscrire(client, dir_headers, classe["id"], "Diallo", "Aminata",
                     telephone_parent="+224620000000")
    return classe, eleve
