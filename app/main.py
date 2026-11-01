from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.database import init_db
from app.routers import (
    absences, annee, auth, classes, eleves, exports, matieres, messages, notes, paiements, stats,
)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # crée les tables SQLite au démarrage
    yield


app = FastAPI(
    title="Gestion École",
    description="API de gestion scolaire : élèves, classes, frais et notes.",
    version="0.4.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
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


@app.get("/", include_in_schema=False)
def interface_web():
    """Sert l'interface web (une seule page HTML)."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/sante", tags=["Santé"])
def sante():
    return {"message": "API Gestion École en marche", "docs": "/docs"}
