"""Photos des élèves et cartes scolaires."""
import base64
import io

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlmodel import Session, select

from app.database import get_session
from app.models import Classe, Ecole, Eleve, Miniature, PhotoEleve, Utilisateur
from app.pdf import en_tete_pdf, pdf_cartes
from app.security import GESTION, TOUS, exiger_role
from app.services import get_classe, get_eleve

router = APIRouter(tags=["Photos et cartes"])
Image.MAX_IMAGE_PIXELS = 40_000_000   # refuse les images géantes


def preparer_photo(contenu: bytes) -> tuple[bytes, bytes]:
    """Recadre en 3:4 (format carte), réduit et ré-encode en JPEG. Renvoie (image, miniature)."""
    try:
        img = Image.open(io.BytesIO(contenu))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "Ce fichier n'est pas une image valide (JPEG, PNG…)")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, (300, 400), Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    sortie = io.BytesIO()
    img.save(sortie, "JPEG", quality=85, optimize=True)
    mini = io.BytesIO()
    img.resize((60, 80), Image.Resampling.LANCZOS).save(mini, "JPEG", quality=80)
    return sortie.getvalue(), mini.getvalue()


@router.put("/eleves/{eleve_id}/photo", status_code=204)
async def envoyer_photo(
    eleve_id: int,
    fichier: UploadFile = File(...),
    user: Utilisateur = Depends(exiger_role(*GESTION)),
    session: Session = Depends(get_session),
):
    get_eleve(session, eleve_id, user.ecole_id)
    contenu = await fichier.read()
    if len(contenu) > 8 * 1024 * 1024:
        raise HTTPException(400, "Photo trop lourde (8 Mo maximum)")
    image, miniature = preparer_photo(contenu)
    photo = session.get(PhotoEleve, eleve_id) or PhotoEleve(eleve_id=eleve_id, image=b"", miniature=b"")
    photo.image, photo.miniature = image, miniature
    session.add(photo)
    session.commit()


@router.get("/eleves/{eleve_id}/photo", response_class=Response)
def voir_photo(eleve_id: int, user: Utilisateur = Depends(exiger_role(*TOUS)),
               session: Session = Depends(get_session)):
    get_eleve(session, eleve_id, user.ecole_id)
    photo = session.get(PhotoEleve, eleve_id)
    if not photo:
        raise HTTPException(404, "Pas de photo")
    return Response(photo.image, media_type="image/jpeg")


@router.delete("/eleves/{eleve_id}/photo", status_code=204)
def supprimer_photo(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                    session: Session = Depends(get_session)):
    get_eleve(session, eleve_id, user.ecole_id)
    photo = session.get(PhotoEleve, eleve_id)
    if photo:
        session.delete(photo)
        session.commit()


@router.get("/photos/miniatures", response_model=list[Miniature])
def miniatures(classe_id: int | None = None, user: Utilisateur = Depends(exiger_role(*TOUS)),
               session: Session = Depends(get_session)):
    """Petites photos des élèves (pour les listes)."""
    stmt = select(PhotoEleve.eleve_id, PhotoEleve.miniature).join(Eleve, PhotoEleve.eleve_id == Eleve.id) \
        .where(Eleve.ecole_id == user.ecole_id)
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    return [Miniature(eleve_id=i, data_url="data:image/jpeg;base64," + base64.b64encode(m).decode())
            for i, m in session.exec(stmt).all()]


def _cartes(session: Session, ecole_id: int, eleves: list[Eleve]) -> bytes:
    photos = {p.eleve_id: p.image for p in session.exec(
        select(PhotoEleve).where(PhotoEleve.eleve_id.in_([e.id for e in eleves])))} if eleves else {}
    classes = {c.id: c.nom for c in session.exec(select(Classe).where(Classe.ecole_id == ecole_id))}
    return pdf_cartes(session.get(Ecole, ecole_id),
                      [(e, classes.get(e.classe_id, ""), photos.get(e.id)) for e in eleves])


@router.get("/eleves/{eleve_id}/carte.pdf", response_class=Response)
def carte_eleve(eleve_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                session: Session = Depends(get_session)):
    e = get_eleve(session, eleve_id, user.ecole_id)
    return Response(_cartes(session, user.ecole_id, [e]), media_type="application/pdf",
                    headers=en_tete_pdf(f"carte_{e.nom}_{e.prenom}.pdf"))


@router.get("/classes/{classe_id}/cartes.pdf", response_class=Response)
def cartes_classe(classe_id: int, user: Utilisateur = Depends(exiger_role(*GESTION)),
                  session: Session = Depends(get_session)):
    classe = get_classe(session, classe_id, user.ecole_id)
    eleves = session.exec(select(Eleve).where(Eleve.classe_id == classe_id, Eleve.actif == True)  # noqa: E712
                          .order_by(Eleve.nom, Eleve.prenom)).all()
    if not eleves:
        raise HTTPException(400, "Aucun élève dans cette classe")
    return Response(_cartes(session, user.ecole_id, eleves), media_type="application/pdf",
                    headers=en_tete_pdf(f"cartes_{classe.nom}.pdf"))
