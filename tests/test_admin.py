import os
import subprocess
import sys
from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app.database import get_session
from app.main import app
from tests.conftest import creer_compte, creer_ecole, inscrire


@pytest.fixture
def admin_headers(client):
    """Crée un super admin directement en base (comme la commande en ligne)."""
    from app.models import SuperAdmin
    from app.security import hacher_mot_de_passe
    session = next(app.dependency_overrides[get_session]())
    session.add(SuperAdmin(email="admin@editeur.gn", nom="Éditeur", mot_de_passe_hash=hacher_mot_de_passe("tres-long-mdp-123")))
    session.commit()
    r = client.post("/admin/connexion", json={"email": "admin@editeur.gn", "mot_de_passe": "tres-long-mdp-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _ecole_id(client, h):
    return client.get("/auth/moi", headers=h).json()["ecole"]["id"]


def test_inscription_donne_une_periode_d_essai(client, dir_headers):
    moi = client.get("/auth/moi", headers=dir_headers).json()
    assert moi["abonnement"]["statut"] == "essai"
    assert moi["abonnement"]["fin"] == str(date.today() + timedelta(days=30))
    assert moi["abonnement"]["jours_restants"] == 30


def test_super_admin_connexion_et_cloisonnement(client, dir_headers, admin_headers):
    assert client.post("/admin/connexion", json={"email": "admin@editeur.gn", "mot_de_passe": "faux"}).status_code == 401
    # Le directeur n'accède pas à la console, le super admin n'accède pas aux écrans d'une école
    assert client.get("/admin/ecoles", headers=dir_headers).status_code == 401
    assert client.get("/eleves", headers=admin_headers).status_code == 401
    assert client.get("/admin/ecoles").status_code == 401
    # Pas de route publique pour créer un super admin
    assert client.post("/admin/superadmins", json={}).status_code in (404, 405)


def test_gestion_abonnements(client, dir_headers, admin_headers, setup):
    h, a = dir_headers, admin_headers
    eid = _ecole_id(client, h)
    creer_ecole(client, nom="Autre École", email="autre@test.gn")

    ecoles = {e["nom"]: e for e in client.get("/admin/ecoles", headers=a).json()}
    assert set(ecoles) == {"École Test", "Autre École"}
    e = ecoles["École Test"]
    assert e["directeur"] == "M. Directeur" and e["nb_eleves"] == 1 and e["abonnement"]["statut"] == "essai"

    # Expiration : l'école est bloquée (402), sauf les exports ; le message est clair
    client.patch(f"/admin/ecoles/{eid}", headers=a, json={"fin": str(date.today() - timedelta(days=1))})
    r = client.get("/eleves", headers=h)
    assert r.status_code == 402 and "expiré" in r.json()["detail"]
    assert client.get("/export/eleves.xlsx", headers=h).status_code == 200
    assert client.get("/auth/moi", headers=h).json()["abonnement"]["statut"] == "expire"
    st = client.get("/admin/stats", headers=a).json()
    assert st["expirees"] == 1 and st["en_essai"] == 1

    # Paiement de 3 mois : réouvert jusqu'à aujourd'hui + 3 mois − 1 jour
    r = client.post(f"/admin/ecoles/{eid}/abonnement", headers=a, json={
        "mois": 3, "montant": 300000, "mode": "orange_money", "reference": "OM9", "plan": "Standard"})
    v = r.json()
    assert v["abonnement"]["statut"] == "actif" and v["plan"] == "Standard" and v["total_abonnements"] == 300000
    fin1 = date.fromisoformat(v["abonnement"]["fin"])
    assert fin1 > date.today() + timedelta(days=85)
    assert client.get("/eleves", headers=h).status_code == 200
    # Un 2e paiement s'ajoute à la suite (pas depuis aujourd'hui)
    v = client.post(f"/admin/ecoles/{eid}/abonnement", headers=a, json={"mois": 1, "montant": 100000}).json()
    assert date.fromisoformat(v["abonnement"]["fin"]) > fin1 + timedelta(days=27)
    hist = client.get(f"/admin/ecoles/{eid}/abonnements", headers=a).json()
    assert [x["montant"] for x in hist] == [100000, 300000]
    assert client.get("/admin/stats", headers=a).json()["revenus_mois"] == 400000

    # Suspension : tout est bloqué, même les exports et l'espace parents
    code = client.get(f"/eleves/{setup[1]['id']}/code-parent", headers=h).json()["code"]
    tok_parent = client.post("/parents/connexion", json={"matricule": setup[1]["matricule"], "code": code}).json()["access_token"]
    client.post(f"/admin/ecoles/{eid}/suspendre", headers=a, json={"motif": "Impayé"})
    assert client.get("/eleves", headers=h).status_code == 402
    assert client.get("/export/eleves.xlsx", headers=h).status_code == 402
    assert client.get("/parents/moi", headers={"Authorization": "Bearer " + tok_parent}).status_code == 402
    assert "Impayé" in client.get("/auth/moi", headers=h).json()["abonnement"]["message"]
    assert client.post(f"/admin/ecoles/{eid}/reactiver", headers=a).json()["abonnement"]["statut"] == "actif"
    assert client.get("/eleves", headers=h).status_code == 200

    # L'autre école n'est pas touchée
    autre = client.post("/auth/token", data={"username": "autre@test.gn", "password": "motdepasse123"}).json()["access_token"]
    assert client.get("/eleves", headers={"Authorization": "Bearer " + autre}).status_code == 200


def test_alerte_fin_proche(client, dir_headers, admin_headers):
    eid = _ecole_id(client, dir_headers)
    client.patch(f"/admin/ecoles/{eid}", headers=admin_headers, json={"fin": str(date.today() + timedelta(days=3))})
    a = client.get("/auth/moi", headers=dir_headers).json()["abonnement"]
    assert a["jours_restants"] == 3 and "3 jour(s)" in a["message"]
    assert client.get("/admin/stats", headers=admin_headers).json()["expirent_bientot"] == 1


def test_admin_cree_ecole_et_aide_le_directeur(client, admin_headers):
    r = client.post("/admin/ecoles?mois_offerts=2", headers=admin_headers, json={
        "nom_ecole": "Lycée Nouveau", "nom_directeur": "Mme Bah", "email": "bah@lycee.gn", "mot_de_passe": "provisoire1"})
    assert r.status_code == 201 and r.json()["abonnement"]["statut"] == "actif"
    eid = r.json()["id"]
    client.post(f"/admin/ecoles/{eid}/directeur/mot-de-passe", headers=admin_headers, json={"nouveau": "nouveau-mdp-1"})
    assert client.post("/auth/token", data={"username": "bah@lycee.gn", "password": "nouveau-mdp-1"}).status_code == 200


def test_inscription_publique_fermee(client, monkeypatch):
    from app.routers import auth
    monkeypatch.setattr(auth, "INSCRIPTION_PUBLIQUE", False)
    r = client.post("/auth/inscription", json={"nom_ecole": "X", "nom_directeur": "Y", "email": "x@y.gn",
                                               "mot_de_passe": "motdepasse123"})
    assert r.status_code == 403


def test_aucune_cle_dans_le_code():
    """Aucune valeur de clé par défaut dans le code source."""
    import pathlib
    racine = pathlib.Path(__file__).resolve().parent.parent / "app"
    for f in racine.rglob("*.py"):
        texte = f.read_text(encoding="utf-8")
        assert "dev-secret" not in texte, f
        assert 'getenv("SECRET_KEY", "' not in texte.replace("getenv(\"SECRET_KEY\", \"\")", ""), f


def test_production_refuse_sans_cle(tmp_path):
    """En production, sans SECRET_KEY, l'application refuse de démarrer."""
    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    env.update({"ENVIRONNEMENT": "production", "FICHIER_ENV": str(tmp_path / "absent.env")})
    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = "import app.config"
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env={**env, "PYTHONPATH": racine},
                       capture_output=True, text=True)
    assert r.returncode != 0 and "SECRET_KEY manquante" in r.stderr


def test_modeles_compatibles_python_314_et_plus():
    """Python 3.14+ évalue les annotations après coup : dans une classe, un champ ne doit pas porter
    le nom d'un type utilisé par un autre champ (ex. champ « date » + champ de type « date »)."""
    import ast
    import pathlib
    for fichier in (pathlib.Path(__file__).resolve().parent.parent / "app").rglob("*.py"):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for classe in (n for n in ast.walk(arbre) if isinstance(n, ast.ClassDef)):
            champs = [n for n in classe.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
            noms_champs = {c.target.id for c in champs}
            for c in champs:
                types = {n.id for n in ast.walk(c.annotation) if isinstance(n, ast.Name)}
                conflit = (types & noms_champs) - {c.target.id} | ({c.target.id} & types if c.target.id in types else set())
                assert not conflit, f"{fichier.name}, classe {classe.name} : le champ {conflit} cache un type"
