import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.database import init_db
from fastapi.staticfiles import StaticFiles

from app.routers import (
    absences, admin, annee, auth, caisse, cycles, classes, echeancier, eleves, emploi, exports, frais, matieres, messages,
    notes, paie,
    guichet, paiements, parents, photos, scolarite, stats,
)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # crée les tables SQLite au démarrage
    tache = asyncio.create_task(_relances_quotidiennes())
    yield
    tache.cancel()


async def _relances_quotidiennes():
    """Une fois par jour, après 9 h : relances automatiques par SMS des écoles qui l'ont activé."""
    from datetime import date, datetime
    from sqlmodel import Session
    from app.database import engine
    from app.routers.guichet import relances_automatiques
    fait = None
    while True:
        try:
            if datetime.now().hour >= 9 and fait != date.today():
                fait = date.today()
                def tourner():
                    with Session(engine) as session:
                        relances_automatiques(session)
                await asyncio.to_thread(tourner)
        except Exception:   # un SMS raté ne doit jamais arrêter le serveur
            pass
        await asyncio.sleep(1800)


app = FastAPI(
    title="Gestion École",
    description="API de gestion scolaire : élèves, classes, frais et notes.",
    version="0.6.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(cycles.router)        # avant classes : /classes/types, /classes/tarifs
app.include_router(classes.router)
app.include_router(eleves.router)
app.include_router(paiements.router)
app.include_router(matieres.router)
app.include_router(notes.router)
app.include_router(stats.router)
app.include_router(absences.router)
app.include_router(exports.router)
app.include_router(messages.router)
app.include_router(annee.router)
app.include_router(echeancier.router)
app.include_router(caisse.router)
app.include_router(photos.router)
app.include_router(parents.router)
app.include_router(admin.router)
app.include_router(frais.router)
app.include_router(emploi.router)
app.include_router(paie.router)
app.include_router(scolarite.router)
app.include_router(guichet.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def interface_web():
    """Sert l'interface web (une seule page HTML)."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/espace-parents", include_in_schema=False)
def espace_parents():
    """Page de l'espace parents (sur téléphone)."""
    return FileResponse(STATIC_DIR / "parents.html")


@app.get("/admin", include_in_schema=False)
def espace_admin():
    """Console du super admin (gestion des écoles et des abonnements)."""
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Service worker à la racine : il gère tout le site (mode hors connexion)."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifeste():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sante", tags=["Santé"])
def sante():
    return {"message": "API Gestion École en marche", "docs": "/docs"}
