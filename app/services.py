"""Logique métier partagée : accès limité à l'école, soldes, bulletins."""
from datetime import date

from fastapi import HTTPException
from sqlmodel import Session, func, select

from app.models import (
    Absence, Bulletin, Classe, CoefficientClasse, Echeance, Ecole, Eleve, LigneBulletin, Matiere, Note,
    Paiement, Solde, StatutPaiement, Tranche, TypeAbsence,
)


# ---------- Année scolaire ----------
def annee_courante(session: Session, ecole_id: int) -> str:
    return session.get(Ecole, ecole_id).annee_scolaire


# ---------- Matières et coefficients ----------
def matieres_ecole(session: Session, ecole_id: int) -> list[Matiere]:
    return session.exec(
        select(Matiere).where(Matiere.ecole_id == ecole_id).order_by(Matiere.ordre, Matiere.id)
    ).all()


def coefficients_specifiques(session: Session, classe_id: int) -> dict[int, int]:
    """{matiere_id: coefficient} propres à une classe."""
    return {c.matiere_id: c.coefficient for c in session.exec(
        select(CoefficientClasse).where(CoefficientClasse.classe_id == classe_id)).all()}


def matieres_classe(session: Session, classe: Classe) -> list[tuple[Matiere, int]]:
    """Matières de l'école avec le coefficient applicable à cette classe."""
    specifiques = coefficients_specifiques(session, classe.id)
    return [(m, specifiques.get(m.id, m.coefficient)) for m in matieres_ecole(session, classe.ecole_id)]


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
                        date_paiement=date_paiement or date.today(), enregistre_par_id=utilisateur_id,
                        annee_scolaire=annee_courante(session, eleve.ecole_id))
    session.add(paiement)
    session.flush()
    paiement.numero_recu = f"R{date.today().year}-{paiement.id:06d}"
    return paiement


# ---------- Échéancier ----------
def tranches_annee(session: Session, ecole_id: int) -> list[Tranche]:
    return session.exec(
        select(Tranche).where(Tranche.ecole_id == ecole_id,
                              Tranche.annee_scolaire == annee_courante(session, ecole_id))
        .order_by(Tranche.ordre, Tranche.date_limite)
    ).all()


def calculer_echeances(frais: int, total_paye: int, tranches: list[Tranche],
                       aujourd_hui: date | None = None) -> list[Echeance]:
    """Répartit les frais en tranches et y impute les paiements dans l'ordre."""
    aujourd_hui = aujourd_hui or date.today()
    montants, cumul = [], 0
    for i, t in enumerate(tranches):
        m = frais - cumul if i == len(tranches) - 1 else round(frais * t.pourcentage / 100)
        montants.append(m)
        cumul += m
    reste_paye, echeances = total_paye, []
    for t, m in zip(tranches, montants):
        couvert = min(m, max(reste_paye, 0))
        reste_paye -= couvert
        statut = "payee" if couvert >= m else ("en_retard" if t.date_limite < aujourd_hui else "a_venir")
        echeances.append(Echeance(nom=t.nom, date_limite=t.date_limite, montant=m, paye=couvert,
                                  reste=m - couvert, statut=statut))
    return echeances


# ---------- Soldes ----------
def calculer_solde(session: Session, eleve: Eleve, tranches: list[Tranche] | None = None) -> Solde:
    classe = session.get(Classe, eleve.classe_id)
    total, nb, dernier = session.exec(
        select(func.coalesce(func.sum(Paiement.montant), 0), func.count(Paiement.id),
               func.max(Paiement.date_paiement))
        .where(Paiement.eleve_id == eleve.id, Paiement.annule == False,  # noqa: E712
               Paiement.annee_scolaire == annee_courante(session, eleve.ecole_id))
    ).one()
    reste = max(classe.frais_annuels - total, 0)
    statut = (StatutPaiement.a_jour if reste == 0
              else StatutPaiement.partiel if total > 0 else StatutPaiement.rien_paye)
    if tranches is None:
        tranches = tranches_annee(session, eleve.ecole_id)
    if tranches:
        echeances = calculer_echeances(classe.frais_annuels, total, tranches)
        exigible = sum(e.montant for e in echeances if e.date_limite < date.today())
        prochaine = next((e for e in echeances if e.statut == "a_venir"), None)
    else:   # pas d'échéancier : tout est dû
        echeances, exigible, prochaine = [], classe.frais_annuels, None
    return Solde(
        montant_exigible=exigible,
        retard=max(exigible - total, 0),
        prochaine_echeance=prochaine.date_limite if prochaine else None,
        prochain_montant=prochaine.reste if prochaine else 0,
        echeances=echeances,
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
    """Soldes des élèves inscrits (ou d'une classe), triés par classe puis par nom."""
    stmt = select(Eleve).where(Eleve.ecole_id == ecole_id, Eleve.actif == True)  # noqa: E712
    if classe_id:
        stmt = stmt.where(Eleve.classe_id == classe_id)
    tranches = tranches_annee(session, ecole_id)
    soldes = [calculer_solde(session, e, tranches) for e in session.exec(stmt.order_by(Eleve.nom, Eleve.prenom)).all()]
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


def _moyennes(notes: list[Note], ordre: dict[str, int] | None = None) -> tuple[list[LigneBulletin], float | None]:
    par_matiere: dict[str, list[Note]] = {}
    for n in notes:
        par_matiere.setdefault(n.matiere, []).append(n)
    ordre = ordre or {}
    lignes = []
    for matiere, ns in sorted(par_matiere.items(), key=lambda kv: (ordre.get(kv[0].lower(), 999), kv[0])):
        moy = sum(n.valeur for n in ns) / len(ns)
        coef = max(n.coefficient for n in ns)
        lignes.append(LigneBulletin(matiere=matiere, moyenne=round(moy, 2), coefficient=coef))
    total_coef = sum(l.coefficient for l in lignes)
    generale = round(sum(l.moyenne * l.coefficient for l in lignes) / total_coef, 2) if total_coef else None
    return lignes, generale


def bulletins_classe(session: Session, classe: Classe, trimestre: int,
                     inclure: Eleve | None = None) -> list[Bulletin]:
    """Bulletins de toute la classe (élèves inscrits), avec le rang de chaque élève."""
    eleves = list(session.exec(
        select(Eleve).where(Eleve.classe_id == classe.id, Eleve.actif == True)  # noqa: E712
        .order_by(Eleve.nom, Eleve.prenom)
    ).all())
    if inclure and inclure not in eleves:   # élève sorti : bulletin consultable quand même
        eleves.append(inclure)
    ids = [e.id for e in eleves]
    annee = annee_courante(session, classe.ecole_id)
    notes = session.exec(
        select(Note).where(Note.eleve_id.in_(ids), Note.trimestre == trimestre, Note.annee_scolaire == annee)
    ).all() if ids else []
    notes_par_eleve: dict[int, list[Note]] = {}
    for n in notes:
        notes_par_eleve.setdefault(n.eleve_id, []).append(n)
    absences = session.exec(
        select(Absence).where(Absence.eleve_id.in_(ids), Absence.trimestre == trimestre,
                              Absence.annee_scolaire == annee)
    ).all() if ids else []
    abs_par_eleve: dict[int, list[Absence]] = {}
    for a in absences:
        abs_par_eleve.setdefault(a.eleve_id, []).append(a)

    ordre = {m.nom.lower(): i for i, m in enumerate(matieres_ecole(session, classe.ecole_id))}
    resultats = []
    for e in eleves:
        lignes, generale = _moyennes(notes_par_eleve.get(e.id, []), ordre)
        resultats.append((e, lignes, generale))

    # Rang : ex-æquo partagent le même rang (1, 2, 2, 4…) ; un élève sorti n'est pas classé
    classes_ids = {e.id for e in eleves if e.actif}
    moyennes = sorted((g for e, _, g in resultats if g is not None and e.id in classes_ids), reverse=True)
    def rang(e, g):
        return None if g is None or e.id not in classes_ids else moyennes.index(g) + 1

    def compte(e, type_, justifiee=None):
        return sum(1 for a in abs_par_eleve.get(e.id, [])
                   if a.type == type_ and (justifiee is None or a.justifiee == justifiee))

    return [
        Bulletin(
            eleve_id=e.id, matricule=e.matricule, nom_complet=f"{e.prenom} {e.nom}",
            classe=classe.nom, trimestre=trimestre, matieres=lignes,
            moyenne_generale=g, rang=rang(e, g), effectif=len(classes_ids), appreciation=appreciation(g),
            absences=compte(e, TypeAbsence.absence), absences_justifiees=compte(e, TypeAbsence.absence, True),
            retards=compte(e, TypeAbsence.retard),
        )
        for e, lignes, g in resultats
    ]


def bulletin_eleve(session: Session, eleve: Eleve, trimestre: int) -> Bulletin:
    classe = session.get(Classe, eleve.classe_id)
    return next(b for b in bulletins_classe(session, classe, trimestre, inclure=eleve) if b.eleve_id == eleve.id)
