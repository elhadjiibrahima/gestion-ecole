"""Logique métier partagée : accès limité à l'école, soldes, bulletins."""
import re
from datetime import date

from fastapi import HTTPException
from sqlmodel import Session, func, select

from app.models import (
    Absence, Bulletin, Classe, CoefficientClasse, Echeance, Ecole, Eleve, LigneBulletin, Matiere, Note,
    Paiement, Solde, StatutPaiement, Tranche, TypeAbsence,
)


# ---------- Logo et cachet ----------
def images_ecole(session: Session, ecole_id: int) -> dict:
    """{"logo": PNG ou None, "cachet": PNG ou None} pour les documents PDF."""
    from app.models import ImageEcole
    images = {"logo": None, "cachet": None}
    for i in session.exec(select(ImageEcole).where(ImageEcole.ecole_id == ecole_id)).all():
        images[i.type] = i.image
    return images


# ---------- Abonnement ----------
def etat_abonnement(ecole: Ecole):
    """Statut réel : essai / actif / expire / suspendu, et jours restants."""
    from app.config import CONTACT_ABONNEMENT
    from app.models import AbonnementInfo
    jours = (ecole.abonnement_fin - date.today()).days if ecole.abonnement_fin else None
    if ecole.abonnement_statut == "suspendu":
        statut = "suspendu"
        message = "L'accès de l'école est suspendu" + (f" : {ecole.motif_suspension}" if ecole.motif_suspension else ".")
    elif jours is not None and jours < 0:
        statut = "expire"
        message = (f"L'abonnement de l'école a expiré le {ecole.abonnement_fin:%d/%m/%Y}. "
                   "Les données restent consultables en export, mais l'application est bloquée jusqu'au renouvellement.")
    else:
        statut = ecole.abonnement_statut
        message = None
        if jours is not None and jours <= 7:
            quoi = "La période d'essai" if statut == "essai" else "L'abonnement"
            message = f"{quoi} se termine dans {jours} jour(s), le {ecole.abonnement_fin:%d/%m/%Y}."
    return AbonnementInfo(statut=statut, fin=ecole.abonnement_fin, jours_restants=jours, message=message,
                          contact=CONTACT_ABONNEMENT or None)


# ---------- Année scolaire ----------
def cle_classe(nom: str) -> tuple:
    """Tri naturel : CP1, CP2, CE1… puis 7ème A, 8ème A… 10ème A (et pas 10ème avant 7ème)."""
    n = (nom or "").strip().lower()
    for i, p in enumerate(PRIMAIRE):
        if n.startswith(p):
            return (0, i, n)
    m = re.search(r"\d+", n)
    return (1, int(m.group()), n) if m else (2, 0, n)


PRIMAIRE = ["maternelle", "petite", "moyenne", "grande", "cp1", "cp2", "ce1", "ce2", "cm1", "cm2", "cp", "ce", "cm"]


def trier_classes(classes: list) -> list:
    return sorted(classes, key=lambda c: cle_classe(c.nom))


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
                   utilisateur_id: int, type_frais_id: int | None = None) -> Paiement:
    """Enregistre un paiement (sans commit) pour la scolarité ou un autre frais.
    Refuse un montant supérieur au reste à payer de ce frais."""
    solde = calculer_solde(session, eleve)
    ligne = next((l for l in solde.lignes if l.type_frais_id == type_frais_id and l.du > 0), None)
    if ligne is None:
        raise HTTPException(400, "Ce frais ne concerne pas cet élève")
    nom = "ses frais de scolarité" if type_frais_id is None else f"« {ligne.nom} »"
    if ligne.reste == 0:
        raise HTTPException(400, f"Cet élève a déjà payé la totalité de {nom}")
    if montant > ligne.reste:
        raise HTTPException(400, f"Montant trop élevé : il ne reste que {ligne.reste:,} à payer pour {nom}".replace(",", " "))
    paiement = Paiement(eleve_id=eleve.id, type_frais_id=type_frais_id, montant=montant, mode=mode, reference=reference,
                        date_paiement=date_paiement or date.today(), enregistre_par_id=utilisateur_id,
                        annee_scolaire=annee_courante(session, eleve.ecole_id))
    session.add(paiement)
    session.flush()
    # Numérotation propre à l'école et à l'année : R2026-1-00001, R2026-1-00002…
    prefixe = f"R{date.today().year}-{eleve.ecole_id}-"
    dernier = session.exec(select(func.max(Paiement.numero_recu)).join(Eleve, Eleve.id == Paiement.eleve_id)
                           .where(Eleve.ecole_id == eleve.ecole_id, Paiement.numero_recu.like(prefixe + "%"))).one()
    paiement.numero_recu = f"{prefixe}{(int(dernier.rsplit('-', 1)[1]) if dernier else 0) + 1:05d}"
    return paiement


# ---------- Échéancier ----------
# ---------- Postes de la paie ----------
POSTES_DEFAUT = [("Directeur", "mensuel", "directeur"), ("Comptable", "mensuel", "comptable"),
                 ("Enseignant", "horaire", "enseignant"), ("Surveillant", "mensuel", "surveillant")]


def ajouter_postes_defaut(session: Session, ecole_id: int) -> None:
    """Un poste par rôle (salaire à définir par le directeur). Sans commit."""
    from app.models import Poste
    for nom, type_, role in POSTES_DEFAUT:
        session.add(Poste(ecole_id=ecole_id, nom=nom, type=type_, montant=0, role=role))


MOIS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre",
        "Novembre", "Décembre"]


def mensualites(ecole: Ecole) -> list[Tranche]:
    """Paiement mensuel : une échéance par mois (non enregistrée), due avant le « jour limite » du mois."""
    try:
        annee = int(ecole.annee_scolaire[:4])
    except (TypeError, ValueError):
        annee = date.today().year
    n = ecole.nb_mois or 9
    tranches = []
    for i in range(n):
        m0 = (ecole.mois_debut or 10) - 1 + i
        an, mois = annee + m0 // 12, m0 % 12 + 1
        tranches.append(Tranche(ecole_id=ecole.id, annee_scolaire=ecole.annee_scolaire, ordre=i, nom=MOIS[mois - 1],
                                date_limite=date(an, mois, min(ecole.jour_limite or 5, 28)), pourcentage=100 / n))
    return tranches


def tranches_annee(session: Session, ecole_id: int) -> list[Tranche]:
    ecole = session.get(Ecole, ecole_id)
    if ecole.mode_paiement == "mensuel":
        return mensualites(ecole)
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
def frais_eleve(classe: Classe, eleve: Eleve) -> tuple[int, int]:
    """(frais dus par l'élève, montant de la réduction)."""
    reduction = round(classe.frais_annuels * (eleve.reduction_pct or 0) / 100) + (eleve.reduction_montant or 0)
    reduction = min(reduction, classe.frais_annuels)
    return classe.frais_annuels - reduction, reduction


def type_inscription(session: Session, ecole_id: int, creer: bool = False):
    """Le frais « Inscription » de l'école (montant fixé par classe), créé au besoin."""
    from app.models import TypeFrais
    t = session.exec(select(TypeFrais).where(TypeFrais.ecole_id == ecole_id, TypeFrais.code == "inscription")).first()
    if t is None and creer:
        t = TypeFrais(ecole_id=ecole_id, nom="Inscription", montant=0, obligatoire=True, code="inscription", ordre=-1)
        session.add(t)
        session.flush()
    return t


def frais_annexes(session: Session, eleve: Eleve) -> list:
    """Autres frais dus par l'élève cette année : les obligatoires + les facultatifs souscrits."""
    from app.models import SouscriptionFrais, TypeFrais
    annee = annee_courante(session, eleve.ecole_id)
    souscrits = set(session.exec(select(SouscriptionFrais.type_frais_id).where(
        SouscriptionFrais.eleve_id == eleve.id, SouscriptionFrais.annee_scolaire == annee)).all())
    types = session.exec(select(TypeFrais).where(TypeFrais.ecole_id == eleve.ecole_id, TypeFrais.actif == True)  # noqa: E712
                         .order_by(TypeFrais.ordre, TypeFrais.id)).all()
    return [t for t in types if t.obligatoire or t.id in souscrits]


def calculer_solde(session: Session, eleve: Eleve, tranches: list[Tranche] | None = None) -> Solde:
    from app.models import LigneFrais, TypeFrais
    classe = session.get(Classe, eleve.classe_id)
    scolarite, reduction = frais_eleve(classe, eleve)
    annee = annee_courante(session, eleve.ecole_id)
    par_type = {t: (s or 0, n) for t, s, n in session.exec(
        select(Paiement.type_frais_id, func.sum(Paiement.montant), func.count(Paiement.id))
        .where(Paiement.eleve_id == eleve.id, Paiement.annule == False,  # noqa: E712
               Paiement.annee_scolaire == annee)
        .group_by(Paiement.type_frais_id)).all()}
    nb = sum(n for _, n in par_type.values())
    dernier = session.exec(select(func.max(Paiement.date_paiement)).where(
        Paiement.eleve_id == eleve.id, Paiement.annule == False, Paiement.annee_scolaire == annee)).one()  # noqa: E712
    # Une ligne par frais : scolarité, puis les autres frais dus (ou déjà payés)
    paye_sco = par_type.get(None, (0, 0))[0]
    lignes = [LigneFrais(type_frais_id=None, nom="Scolarité", du=scolarite, paye=paye_sco,
                         reste=max(scolarite - paye_sco, 0))]
    annexes = frais_annexes(session, eleve)
    ids_annexes = {t.id for t in annexes if t.code != "inscription" or classe.frais_inscription or par_type.get(t.id)}
    for t in annexes:
        p = par_type.get(t.id, (0, 0))[0]
        du = classe.frais_inscription if t.code == "inscription" else t.montant
        if du == 0 and p == 0 and t.code == "inscription":
            continue
        lignes.append(LigneFrais(type_frais_id=t.id, nom=t.nom, du=du, paye=p, reste=max(du - p, 0)))
    for tid, (p, _) in par_type.items():   # payé pour un frais qui ne s'applique plus : on le montre quand même
        if tid is not None and tid not in ids_annexes:
            t = session.get(TypeFrais, tid)
            lignes.append(LigneFrais(type_frais_id=tid, nom=t.nom if t else "?", du=0, paye=p, reste=0))
    du_total = sum(l.du for l in lignes)
    total = sum(l.paye for l in lignes)
    reste = sum(l.reste for l in lignes)
    reste_annexes = sum(l.reste for l in lignes[1:])
    statut = (StatutPaiement.a_jour if reste == 0
              else StatutPaiement.partiel if total > 0 else StatutPaiement.rien_paye)
    # L'échéancier porte sur la scolarité ; les autres frais sont dus dès maintenant
    if tranches is None:
        tranches = tranches_annee(session, eleve.ecole_id)
    if tranches:
        echeances = calculer_echeances(scolarite, paye_sco, tranches)
        exigible_sco = sum(e.montant for e in echeances if e.date_limite < date.today())
        prochaine = next((e for e in echeances if e.statut == "a_venir"), None)
    else:
        echeances, exigible_sco, prochaine = [], scolarite, None
    return Solde(
        montant_exigible=exigible_sco + sum(l.du for l in lignes[1:]),
        retard=max(exigible_sco - paye_sco, 0) + reste_annexes,
        prochaine_echeance=prochaine.date_limite if prochaine else None,
        prochain_montant=prochaine.reste if prochaine else 0,
        echeances=echeances,
        lignes=lignes,
        frais_classe=classe.frais_annuels,
        reduction=reduction,
        reduction_motif=eleve.reduction_motif if reduction else None,
        eleve_id=eleve.id,
        matricule=eleve.matricule,
        nom_complet=f"{eleve.prenom} {eleve.nom}",
        classe_id=classe.id,
        classe=classe.nom,
        frais_annuels=du_total,
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
    return sorted(soldes, key=lambda s: cle_classe(s.classe))


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


def moyenne_matiere(notes: list[Note], poids_composition: float = 2) -> float | None:
    """(moyenne des devoirs + composition × poids) / (1 + poids) ; une seule sorte de note : sa moyenne."""
    devoirs = [n.valeur for n in notes if n.type != "composition"]
    compos = [n.valeur for n in notes if n.type == "composition"]
    if devoirs and compos:
        md, mc = sum(devoirs) / len(devoirs), sum(compos) / len(compos)
        return (md + mc * poids_composition) / (1 + poids_composition)
    valeurs = devoirs or compos
    return sum(valeurs) / len(valeurs) if valeurs else None


def _moyennes(notes: list[Note], ordre: dict[str, int] | None = None,
              poids_composition: float = 2) -> tuple[list[LigneBulletin], float | None]:
    par_matiere: dict[str, list[Note]] = {}
    for n in notes:
        par_matiere.setdefault(n.matiere, []).append(n)
    ordre = ordre or {}
    lignes = []
    for matiere, ns in sorted(par_matiere.items(), key=lambda kv: (ordre.get(kv[0].lower(), 999), kv[0])):
        moy = moyenne_matiere(ns, poids_composition)
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
    ecole = session.get(Ecole, classe.ecole_id)
    resultats = []
    for e in eleves:
        lignes, generale = _moyennes(notes_par_eleve.get(e.id, []), ordre, ecole.poids_composition)
        resultats.append((e, lignes, generale))

    # Rang : ex-æquo partagent le même rang (1, 2, 2, 4…) ; un élève sorti n'est pas classé
    classes_ids = {e.id for e in eleves if e.actif}
    moyennes = sorted((g for e, _, g in resultats if g is not None and e.id in classes_ids), reverse=True)
    def rang(e, g):
        return None if g is None or e.id not in classes_ids else moyennes.index(g) + 1

    def compte(e, type_, justifiee=None):
        return sum(1 for a in abs_par_eleve.get(e.id, [])
                   if a.type == type_ and (justifiee is None or a.justifiee == justifiee))

    annuels = {r.eleve_id: r for r in resultats_annuels(session, classe, inclure)} if trimestre == 3 else {}
    # Statistiques de la classe par matière (élèves inscrits)
    from app.models import AppreciationMatiere, AvisConseil
    par_matiere: dict[str, list[tuple[int, float]]] = {}
    for e, lignes, _ in resultats:
        if e.id in classes_ids:
            for l in lignes:
                par_matiere.setdefault(l.matiere, []).append((e.id, l.moyenne))
    appreciations = {(a.eleve_id, a.matiere.lower()): a.texte for a in session.exec(
        select(AppreciationMatiere).where(AppreciationMatiere.eleve_id.in_(ids), AppreciationMatiere.trimestre == trimestre,
                                          AppreciationMatiere.annee_scolaire == annee)).all()} if ids else {}
    avis = {a.eleve_id: a.texte for a in session.exec(
        select(AvisConseil).where(AvisConseil.eleve_id.in_(ids), AvisConseil.trimestre == trimestre,
                                  AvisConseil.annee_scolaire == annee)).all()} if ids else {}
    generales = [g for e, _, g in resultats if g is not None and e.id in classes_ids]
    moyenne_classe = round(sum(generales) / len(generales), 2) if generales else None
    for e, lignes, _ in resultats:
        for l in lignes:
            valeurs = [v for _, v in par_matiere.get(l.matiere, [])]
            if valeurs:
                l.moyenne_classe = round(sum(valeurs) / len(valeurs), 2)
                l.min_classe, l.max_classe = min(valeurs), max(valeurs)
                if e.id in classes_ids:
                    l.rang_matiere = sorted(valeurs, reverse=True).index(l.moyenne) + 1
            l.appreciation = appreciations.get((e.id, l.matiere.lower()))
    bulletins = []
    for e, lignes, g in resultats:
        b = Bulletin(
            eleve_id=e.id, matricule=e.matricule, nom_complet=f"{e.prenom} {e.nom}",
            classe=classe.nom, trimestre=trimestre, matieres=lignes,
            moyenne_generale=g, rang=rang(e, g), effectif=len(classes_ids), appreciation=appreciation(g),
            absences=compte(e, TypeAbsence.absence), absences_justifiees=compte(e, TypeAbsence.absence, True),
            retards=compte(e, TypeAbsence.retard),
            avis_conseil=avis.get(e.id), moyenne_classe=moyenne_classe,
        )
        if e.id in annuels:
            a = annuels[e.id]
            b.moyennes_trimestres, b.moyenne_annuelle = a.moyennes_trimestres, a.moyenne_annuelle
            b.rang_annuel, b.decision = a.rang, a.decision
        bulletins.append(b)
    return bulletins


def resultats_annuels(session: Session, classe: Classe, inclure: Eleve | None = None) -> list:
    """Moyenne de l'année (moyenne des trimestres notés), rang annuel et décision de passage."""
    from app.models import ResultatAnnuel
    ecole = session.get(Ecole, classe.ecole_id)
    eleves = list(session.exec(
        select(Eleve).where(Eleve.classe_id == classe.id, Eleve.actif == True)  # noqa: E712
        .order_by(Eleve.nom, Eleve.prenom)).all())
    if inclure and inclure not in eleves:
        eleves.append(inclure)
    ids = [e.id for e in eleves]
    notes = session.exec(select(Note).where(Note.eleve_id.in_(ids),
                                            Note.annee_scolaire == ecole.annee_scolaire)).all() if ids else []
    par_eleve_trim: dict[tuple[int, int], list[Note]] = {}
    for n in notes:
        par_eleve_trim.setdefault((n.eleve_id, n.trimestre), []).append(n)
    lignes = []
    for e in eleves:
        moys = [_moyennes(par_eleve_trim.get((e.id, t), []), None, ecole.poids_composition)[1] for t in (1, 2, 3)]
        notees = [m for m in moys if m is not None]
        annuelle = round(sum(notees) / len(notees), 2) if notees else None
        decision = None if annuelle is None else ("Admis(e)" if annuelle >= ecole.seuil_admission else "Redouble")
        lignes.append(ResultatAnnuel(eleve_id=e.id, matricule=e.matricule, eleve=f"{e.prenom} {e.nom}",
                                     moyennes_trimestres=moys, moyenne_annuelle=annuelle, rang=None,
                                     decision=decision))
    classees = sorted((l.moyenne_annuelle for l in lignes
                       if l.moyenne_annuelle is not None and any(e.id == l.eleve_id and e.actif for e in eleves)),
                      reverse=True)
    for l in lignes:
        if l.moyenne_annuelle is not None and l.moyenne_annuelle in classees:
            l.rang = classees.index(l.moyenne_annuelle) + 1
    return lignes


def bulletin_eleve(session: Session, eleve: Eleve, trimestre: int) -> Bulletin:
    classe = session.get(Classe, eleve.classe_id)
    return next(b for b in bulletins_classe(session, classe, trimestre, inclure=eleve) if b.eleve_id == eleve.id)
