"""Cycles d'études (règles de notation), matières par cycle, classes types et tarifs."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.cursus import CYCLES, classes_types, creer_cursus, cycles_ecole, normaliser
from app.database import get_session
from app.models import (
    Classe, ClasseType, Cycle, CycleCreation, CycleSaisie, Ecole, Matiere, MatiereCycle, MatiereCycleSaisie, Role,
    TarifClasse, Utilisateur,
)
from app.security import TOUS, exiger_role
from app.services import get_classe, trier_classes, type_inscription

router = APIRouter(tags=["Cycles et classes"])


def _cycle(session: Session, cycle_id: int, ecole_id: int) -> Cycle:
    c = session.get(Cycle, cycle_id)
    if not c or c.ecole_id != ecole_id:
        raise HTTPException(404, "Cycle introuvable")
    return c


@router.get("/cycles", response_model=list[Cycle])
def lister(user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    return cycles_ecole(session, user.ecole_id)


@router.post("/cycles", response_model=Cycle, status_code=201)
def creer(data: CycleCreation, user: Utilisateur = Depends(exiger_role(Role.directeur)),
          session: Session = Depends(get_session)):
    """Crée un niveau à soi (Maternelle, Formation professionnelle…), placé où on veut dans le parcours,
    avec ses classes. Ses matières s'ajoutent ensuite dans Notes → Matières (colonne du niveau)."""
    existants = cycles_ecole(session, user.ecole_id)
    if any(normaliser(c.nom) == normaliser(data.nom) for c in existants):
        raise HTTPException(400, "Ce niveau existe déjà")
    noms = [n.strip() for n in data.classes if n.strip()]
    deja = {normaliser(c.nom) for c in session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all()}
    doublons = [n for n in noms if normaliser(n) in deja]
    if doublons or len({normaliser(n) for n in noms}) != len(noms):
        raise HTTPException(400, "Classe déjà existante : " + ", ".join(doublons or noms))
    # Place dans le parcours : on décale les niveaux suivants
    ordre = max((c.ordre for c in existants), default=-1) + 1
    if data.avant_cycle_id is not None:
        avant = next((c for c in existants if c.id == data.avant_cycle_id), None)
        if not avant:
            raise HTTPException(400, "Niveau introuvable")
        ordre = avant.ordre
        for c in existants:
            if c.ordre >= ordre:
                c.ordre += 1
                session.add(c)
    c = Cycle(ecole_id=user.ecole_id, nom=data.nom.strip(), periodes=data.periodes, bareme=data.bareme,
              seuil=min(data.seuil, data.bareme), ordre=ordre,
              enseignement=data.enseignement or "matiere", max_matieres=data.max_matieres)
    session.add(c)
    for n in noms:
        session.add(Classe(ecole_id=user.ecole_id, nom=n, niveau=c.nom))
    session.commit()
    session.refresh(c)
    return c


@router.patch("/cycles/{cycle_id}", response_model=Cycle)
def modifier(cycle_id: int, data: CycleSaisie, user: Utilisateur = Depends(exiger_role(Role.directeur)),
             session: Session = Depends(get_session)):
    """Change les règles d'un cycle. Renommer le cycle renomme aussi le niveau de ses classes."""
    c = _cycle(session, cycle_id, user.ecole_id)
    if data.seuil > data.bareme:
        raise HTTPException(400, f"La moyenne de passage ne peut pas dépasser {data.bareme}")
    ancien = c.nom
    c.nom, c.periodes, c.bareme, c.seuil = data.nom.strip(), data.periodes, data.bareme, data.seuil
    if data.enseignement:
        c.enseignement = data.enseignement
    if "max_matieres" in data.model_fields_set:
        c.max_matieres = data.max_matieres
    if normaliser(ancien) != normaliser(c.nom):
        for cl in session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all():
            if normaliser(cl.niveau) == normaliser(ancien):
                cl.niveau = c.nom
                session.add(cl)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@router.delete("/cycles/{cycle_id}", status_code=204)
def supprimer(cycle_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
              session: Session = Depends(get_session)):
    c = _cycle(session, cycle_id, user.ecole_id)
    if any(normaliser(cl.niveau) == normaliser(c.nom)
           for cl in session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all()):
        raise HTTPException(400, "Des classes appartiennent à ce cycle : change d'abord leur niveau")
    for l in session.exec(select(MatiereCycle).where(MatiereCycle.cycle_id == c.id)).all():
        session.delete(l)
    session.delete(c)
    session.commit()


class AjoutCycles(BaseModel):
    codes: list[str]
    classes: bool = True


@router.post("/cycles/types", response_model=list[Cycle])
def ajouter_types(data: AjoutCycles, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                  session: Session = Depends(get_session)):
    """Ajoute des cycles types (préscolaire, primaire, collège, lycée) avec leurs matières, et leurs classes."""
    ecole = session.get(Ecole, user.ecole_id)
    connus = {c[0] for c in CYCLES["senegal"]}
    if not data.codes or any(c not in connus for c in data.codes):
        raise HTTPException(400, "Cycles possibles : " + ", ".join(sorted(connus)))
    creer_cursus(session, ecole, data.codes, classes=data.classes)
    session.commit()
    return cycles_ecole(session, user.ecole_id)


# ---------- Matières d'un cycle ----------
@router.get("/matieres/cycles", response_model=list[MatiereCycle])
def toutes_matieres_cycles(user: Utilisateur = Depends(exiger_role(*TOUS)), session: Session = Depends(get_session)):
    """Toutes les associations matière ↔ cycle de l'école (une matière absente d'ici est enseignée partout)."""
    ids = [m.id for m in session.exec(select(Matiere).where(Matiere.ecole_id == user.ecole_id)).all()]
    return session.exec(select(MatiereCycle).where(MatiereCycle.matiere_id.in_(ids))).all() if ids else []


@router.get("/matieres/{matiere_id}/cycles", response_model=list[MatiereCycleSaisie])
def cycles_matiere(matiere_id: int, user: Utilisateur = Depends(exiger_role(*TOUS)),
                   session: Session = Depends(get_session)):
    m = session.get(Matiere, matiere_id)
    if not m or m.ecole_id != user.ecole_id:
        raise HTTPException(404, "Matière introuvable")
    liens = {l.cycle_id: l.coefficient for l in session.exec(select(MatiereCycle).where(MatiereCycle.matiere_id == m.id)).all()}
    return [MatiereCycleSaisie(cycle_id=c.id, coefficient=liens.get(c.id) if liens else m.coefficient)
            for c in cycles_ecole(session, user.ecole_id)]


@router.put("/matieres/{matiere_id}/cycles", response_model=list[MatiereCycleSaisie])
def definir_cycles_matiere(matiere_id: int, data: list[MatiereCycleSaisie],
                           user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    """Dans quels cycles la matière est enseignée, et avec quel coefficient (vide : pas enseignée)."""
    m = session.get(Matiere, matiere_id)
    if not m or m.ecole_id != user.ecole_id:
        raise HTTPException(404, "Matière introuvable")
    ids = {c.id for c in cycles_ecole(session, user.ecole_id)}
    if any(d.cycle_id not in ids for d in data):
        raise HTTPException(400, "Cycle inconnu")
    for l in session.exec(select(MatiereCycle).where(MatiereCycle.matiere_id == m.id)).all():
        session.delete(l)
    session.flush()
    for d in data:
        if d.coefficient:
            session.add(MatiereCycle(matiere_id=m.id, cycle_id=d.cycle_id, coefficient=d.coefficient))
    session.commit()
    return cycles_matiere(matiere_id, user, session)


# ---------- Classes types, tarifs, classe parallèle ----------
@router.get("/classes/types", response_model=list[ClasseType])
def lister_types(user: Utilisateur = Depends(exiger_role(Role.directeur)), session: Session = Depends(get_session)):
    return [ClasseType(nom=n, niveau=niv, existe=e) for n, niv, e in classes_types(session, session.get(Ecole, user.ecole_id))]


class AjoutClasses(BaseModel):
    noms: list[str]


@router.post("/classes/types", response_model=list[Classe], status_code=201)
def creer_types(data: AjoutClasses, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                session: Session = Depends(get_session)):
    """Crée en un clic les classes types choisies (le cycle et ses matières sont ajoutés au besoin)."""
    ecole = session.get(Ecole, user.ecole_id)
    types = {normaliser(n): (n, niv) for n, niv, e in classes_types(session, ecole) if not e}
    choisies = [types[normaliser(n)] for n in data.noms if normaliser(n) in types]
    if not choisies:
        raise HTTPException(400, "Aucune nouvelle classe à créer")
    codes = {code for code, nom, *_ in CYCLES["senegal"] if any(normaliser(niv) == normaliser(nom) for _, niv in choisies)}
    creer_cursus(session, ecole, sorted(codes), classes=False)
    crees = []
    for nom, niveau in choisies:
        c = Classe(ecole_id=user.ecole_id, nom=nom, niveau=niveau)
        session.add(c)
        crees.append(c)
    session.flush()
    from app.cursus import _coefficients_series
    _coefficients_series(session, user.ecole_id)
    session.commit()
    for c in crees:
        session.refresh(c)
    return trier_classes(crees)


@router.put("/classes/tarifs", response_model=list[Classe])
def definir_tarifs(tarifs: list[TarifClasse], user: Utilisateur = Depends(exiger_role(Role.directeur)),
                   session: Session = Depends(get_session)):
    """Mensualité (ou frais de l'année) et frais d'inscription de plusieurs classes d'un coup."""
    ecole = session.get(Ecole, user.ecole_id)
    for t in tarifs:
        c = get_classe(session, t.classe_id, user.ecole_id, code=400)
        if t.mensualite is not None:
            c.mensualite = t.mensualite
            if ecole.mode_paiement == "mensuel":
                c.frais_annuels = t.mensualite * ecole.nb_mois
        if t.frais_annuels is not None and ecole.mode_paiement != "mensuel":
            c.frais_annuels = t.frais_annuels
        if t.frais_inscription is not None:
            c.frais_inscription = t.frais_inscription
        session.add(c)
    if any(t.frais_inscription for t in tarifs):
        type_inscription(session, user.ecole_id, creer=True)
    session.commit()
    return trier_classes(session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all())


@router.post("/classes/{classe_id}/parallele", response_model=Classe, status_code=201)
def classe_parallele(classe_id: int, user: Utilisateur = Depends(exiger_role(Role.directeur)),
                     session: Session = Depends(get_session)):
    """Ajoute une classe du même niveau (6ème → 6ème B, ou 6ème A → 6ème B), avec les mêmes frais et coefficients."""
    from app.models import CoefficientClasse
    c = get_classe(session, classe_id, user.ecole_id)
    noms = {normaliser(x.nom) for x in session.exec(select(Classe).where(Classe.ecole_id == user.ecole_id)).all()}
    base = c.nom[:-2] if len(c.nom) > 2 and c.nom[-2] == " " and c.nom[-1].isalpha() and c.nom[-1].isupper() else c.nom
    if base == c.nom and normaliser(base + " a") not in noms:
        c.nom = base + " A"                  # la classe d'origine devient « … A »
        session.add(c)
        noms.add(normaliser(c.nom))
    lettre = next(l for l in "BCDEFGHIJKLMNOPQRSTUVWXYZ" if normaliser(f"{base} {l}") not in noms)
    nouvelle = Classe(ecole_id=user.ecole_id, nom=f"{base} {lettre}", niveau=c.niveau, frais_annuels=c.frais_annuels,
                      mensualite=c.mensualite, frais_inscription=c.frais_inscription)
    session.add(nouvelle)
    session.flush()
    for x in session.exec(select(CoefficientClasse).where(CoefficientClasse.classe_id == c.id)).all():
        session.add(CoefficientClasse(matiere_id=x.matiere_id, classe_id=nouvelle.id, coefficient=x.coefficient))
    session.commit()
    session.refresh(nouvelle)
    return nouvelle
