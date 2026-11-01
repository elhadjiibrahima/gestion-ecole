"""Logique métier partagée : accès limité à l'école, soldes, bulletins."""
from datetime import date

from fastapi import HTTPException
from sqlmodel import Session, func, select

from app.models import Bulletin, Classe, Eleve, LigneBulletin, Note, Paiement, Solde, StatutPaiement


# ---------- Accès limité à l'école de l'utilisateur ----------
def get_classe(session: Session, classe_id: int, ecole_id: int, code: int = 404) -> Classe:
    classe = session.get(Classe, classe_id)
    if not classe or classe.ecole_id != ecole_id:
        raise HTTPException(code, "Classe introuvable")
    return classe


def get_eleve(session: Session, eleve_id: int, ecole_id: int, code: int = 404) -> Eleve:
    eleve = session.get(Eleve, eleve_id)
    if not eleve or eleve.ecole_id != ecole_id:
        raise HTTPException(code, "Élève introuvable")
    return eleve


# ---------- Paiements ----------
def creer_paiement(session: Session, eleve: Eleve, montant: int, mode, reference, date_paiement,
                   utilisateur_id: int) -> Paiement:
    """Enregistre un paiement (sans commit). Refuse un montant supérieur au reste à payer."""
    reste = calculer_solde(session, eleve).reste_a_payer
    if reste == 0:
        raise HTTPException(400, "Cet élève a déjà payé la totalité de ses frais")
    if montant > reste:
        raise HTTPException(400, f"Montant trop élevé : il ne reste que {reste:,} à payer".replace(",", " "))
    paiement = Paiement(eleve_id=eleve.id, montant=montant, mode=mode, reference=reference,
                        date_paiement=date_paiement or date.today(), enregistre_par_id=utilisateur_id)
    session.add(paiement)
    session.flush()
    paiement.numero_recu = f"R{date.today().year}-{paiement.id:06d}"
    return paiement


# ---------- Soldes ----------
def calculer_solde(session: Session, eleve: Eleve) -> Solde:
    classe = session.get(Classe, eleve.classe_id)
    total, nb, dernier = session.exec(
        select(func.coalesce(func.sum(Paiement.montant), 0), func.count(Paiement.id),
               func.max(Paiement.date_paiement))
        .where(Paiement.eleve_id == eleve.id)
    ).one()
    reste = max(classe.frais_annuels - total, 0)
    statut = (StatutPaiement.a_jour if reste == 0
              else StatutPaiement.partiel if total > 0 else StatutPaiement.rien_paye)
    return Solde(
        eleve_id=eleve.id,
        matricule=eleve.matricule,
        nom_complet=f"{eleve.prenom} {eleve.nom}",
        classe_id=classe.id,
        classe=classe.nom,
        frais_annuels=classe.frais_annuels,
        total_paye=total,
        reste_a_payer=reste,
        nb_paiements=nb,
        dernier_paiement=dernier,
        statut=statut,
    )


def soldes_ecole(session: Session, ecole_id: int, classe_id: int | None = None) -> list[Solde]:
    """Soldes des élèves de l'école (ou d'une classe), triés par classe puis par nom."""
    stmt = select(Eleve).where(Eleve.ecole_id == ecole_id)
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    soldes = [calculer_solde(session, e) for e in session.exec(stmt.order_by(Eleve.nom, Eleve.prenom)).all()]
    return sorted(soldes, key=lambda s: s.classe.lower())


# ---------- Bulletins ----------
def appreciation(moyenne: float | None) -> str:
    if moyenne is None:
        return "Pas de notes"
    if moyenne >= 16:
        return "Excellent travail, félicitations"
    if moyenne >= 14:
        return "Très bon travail, encouragements"
    if moyenne >= 12:
        return "Bon travail, tableau d'honneur"
    if moyenne >= 10:
        return "Travail passable, peut mieux faire"
    if moyenne >= 8:
        return "Travail insuffisant, doit redoubler d'efforts"
    return "Résultats très insuffisants, avertissement"


def _moyennes(notes: list[Note]) -> tuple[list[LigneBulletin], float | None]:
    par_matiere: dict[str, list[Note]] = {}
    for n in notes:
        par_matiere.setdefault(n.matiere, []).append(n)
    lignes = []
    for matiere, ns in sorted(par_matiere.items()):
        moy = sum(n.valeur for n in ns) / len(ns)
        coef = max(n.coefficient for n in ns)
        lignes.append(LigneBulletin(matiere=matiere, moyenne=round(moy, 2), coefficient=coef))
    total_coef = sum(l.coefficient for l in lignes)
    generale = round(sum(l.moyenne * l.coefficient for l in lignes) / total_coef, 2) if total_coef else None
    return lignes, generale


def bulletins_classe(session: Session, classe: Classe, trimestre: int) -> list[Bulletin]:
    """Bulletins de toute la classe, avec le rang de chaque élève."""
    eleves = session.exec(
        select(Eleve).where(Eleve.classe_id == classe.id).order_by(Eleve.nom, Eleve.prenom)
    ).all()
    ids = [e.id for e in eleves]
    notes = session.exec(
        select(Note).where(Note.eleve_id.in_(ids), Note.trimestre == trimestre)
    ).all() if ids else []
    notes_par_eleve: dict[int, list[Note]] = {}
    for n in notes:
        notes_par_eleve.setdefault(n.eleve_id, []).append(n)

    resultats = []
    for e in eleves:
        lignes, generale = _moyennes(notes_par_eleve.get(e.id, []))
        resultats.append((e, lignes, generale))

    # Rang : ex-æquo partagent le même rang (1, 2, 2, 4…)
    moyennes = sorted((g for _, _, g in resultats if g is not None), reverse=True)
    def rang(g):
        return None if g is None else moyennes.index(g) + 1

    return [
        Bulletin(
            eleve_id=e.id, matricule=e.matricule, nom_complet=f"{e.prenom} {e.nom}",
            classe=classe.nom, trimestre=trimestre, matieres=lignes,
            moyenne_generale=g, rang=rang(g), effectif=len(eleves), appreciation=appreciation(g),
        )
        for e, lignes, g in resultats
    ]


def bulletin_eleve(session: Session, eleve: Eleve, trimestre: int) -> Bulletin:
    classe = session.get(Classe, eleve.classe_id)
    return next(b for b in bulletins_classe(session, classe, trimestre) if b.eleve_id == eleve.id)
