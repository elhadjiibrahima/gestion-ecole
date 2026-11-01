from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlmodel import Session, select

from app.database import get_session
from app.excel import lire_import_eleves, modele_import_eleves
from app.models import (
    Bulletin, Classe, Ecole, Eleve, EleveCreate, EleveInscrit, EleveUpdate, ResultatImport,
    Solde, Utilisateur,
)
from app.pdf import en_tete_pdf, pdf_bulletins
from app.security import GESTION, TOUS, exiger_role
from app.services import (
    bulletin_eleve, calculer_solde, creer_paiement, get_classe, get_eleve, soldes_ecole,
)

router = APIRouter(prefix="/eleves", tags=["Élèves"])


def _attribuer_matricule(session: Session, eleve: Eleve) -> None:
    session.flush()  # obtient l'id
    eleve.matricule = f"{date.today().year}-{eleve.id:05d}"


@router.post("", response_model=EleveInscrit, status_code=201)
def inscrire_eleve(
    data: EleveCreate,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Inscrit un élève avec son premier paiement (obligatoire). Tout ou rien."""
    get_classe(session, data.classe_id, user.ecole_id, code=400)
    eleve = Eleve.model_validate(data.model_dump(exclude={"paiement"}), update={"ecole_id": user.ecole_id})
    session.add(eleve)
    _attribuer_matricule(session, eleve)
    p = data.paiement
    paiement = creer_paiement(session, eleve, p.montant, p.mode, p.reference, None, user.id)
    session.commit()  # si le paiement est refusé, rien n'est enregistré
    session.refresh(eleve)
    session.refresh(paiement)
    return EleveInscrit(eleve=eleve, paiement=paiement)


@router.get("", response_model=list[Eleve])
def lister_eleves(
    q: str | None = None,
    classe_id: int | None = None,
    sortis: bool = False,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    """Élèves inscrits. `q` filtre par nom, prénom ou matricule ; `sortis=true` : anciens élèves."""
    stmt = select(Eleve).where(Eleve.ecole_id == user.ecole_id, Eleve.actif == (not sortis))
    if q:
        motif = f"%{q}%"
        stmt = stmt.where(
            Eleve.nom.ilike(motif) | Eleve.prenom.ilike(motif) | Eleve.matricule.ilike(motif)
        )
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    return session.exec(stmt.order_by(Eleve.nom, Eleve.prenom)).all()


@router.get("/soldes", response_model=list[Solde])
def soldes(
    classe_id: int | None = None,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Solde de chaque élève, trié par classe puis par nom."""
    if classe_id:
        get_classe(session, classe_id, user.ecole_id)
    return soldes_ecole(session, user.ecole_id, classe_id)


# ---------- Import Excel ----------
@router.get("/modele-import.xlsx", response_class=Response)
def telecharger_modele(
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    classes = session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all()
    return Response(
        modele_import_eleves([c.nom for c in classes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="modele_import_eleves.xlsx"'},
    )


@router.post("/import", response_model=ResultatImport)
async def importer_eleves(
    fichier: UploadFile = File(...),
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    """Importe des élèves depuis un fichier Excel (.xlsx).

    Colonnes : nom, prenom, classe, montant_paye (obligatoires), date_naissance, telephone_parent,
    mode_paiement, reference_paiement (facultatifs). Un paiement + reçu est créé pour chaque élève.
    Si une ligne contient une erreur, rien n'est importé et la liste des erreurs est renvoyée.
    """
    if not (fichier.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Le fichier doit être au format .xlsx")
    contenu = await fichier.read()
    if len(contenu) > 5 * 1024 * 1024:
        raise HTTPException(400, "Fichier trop gros (5 Mo maximum)")

    classes = {
        c.nom.strip().lower(): c
        for c in session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all()
    }
    lignes, erreurs = lire_import_eleves(contenu, classes)
    if erreurs:
        return ResultatImport(importes=0, erreurs=erreurs)
    for ligne in lignes:
        eleve = Eleve(**ligne["eleve"], ecole_id=user.ecole_id)
        session.add(eleve)
        _attribuer_matricule(session, eleve)
        p = ligne["paiement"]
        creer_paiement(session, eleve, p["montant"], p["mode"], p["reference"], None, user.id)
    session.commit()
    return ResultatImport(importes=len(lignes), erreurs=[])


# ---------- Un élève ----------
@router.get("/{eleve_id}", response_model=Eleve)
def voir_eleve(
    eleve_id: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    return get_eleve(session, eleve_id, user.ecole_id)


@router.patch("/{eleve_id}", response_model=Eleve)
def modifier_eleve(
    eleve_id: int,
    data: EleveUpdate,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    changes = data.model_dump(exclude_unset=True)
    if "classe_id" in changes:
        get_classe(session, changes["classe_id"], user.ecole_id, code=400)
    eleve.sqlmodel_update(changes)
    session.add(eleve)
    session.commit()
    session.refresh(eleve)
    return eleve


@router.get("/{eleve_id}/solde", response_model=Solde)
def solde_eleve(
    eleve_id: int,
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    return calculer_solde(session, get_eleve(session, eleve_id, user.ecole_id))


@router.get("/{eleve_id}/bulletin", response_model=Bulletin)
def bulletin(
    eleve_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    return bulletin_eleve(session, get_eleve(session, eleve_id, user.ecole_id), trimestre)


@router.get("/{eleve_id}/bulletin.pdf", response_class=Response)
def bulletin_pdf(
    eleve_id: int,
    trimestre: int,
    user: Utilisateur = Depends(exiger_role(*TOUS)),
    session: Session = Depends(get_session),
):
    if trimestre not in (1, 2, 3):
        raise HTTPException(400, "Le trimestre doit être 1, 2 ou 3")
    eleve = get_eleve(session, eleve_id, user.ecole_id)
    b = bulletin_eleve(session, eleve, trimestre)
    pdf = pdf_bulletins(session.get(Ecole, user.ecole_id), [b])
    return Response(pdf, media_type="application/pdf",
                    headers=en_tete_pdf(f"bulletin_{eleve.nom}_{eleve.prenom}_T{trimestre}.pdf"))
