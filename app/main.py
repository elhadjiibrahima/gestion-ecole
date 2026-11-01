from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers import classes, eleves, notes, paiements


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # crée les tables SQLite au démarrage
    yield


app = FastAPI(
    title="Gestion École",
    description="API de gestion scolaire : élèves, classes, frais et notes.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(classes.router)
app.include_router(eleves.router)
app.include_router(paiements.router)
app.include_router(notes.router)


@app.get("/", tags=["Santé"])
def accueil():
    return {"message": "API Gestion École en marche", "docs": "/docs"}
