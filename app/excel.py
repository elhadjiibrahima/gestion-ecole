"""Import / modèle Excel des élèves (openpyxl)."""
import io
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

COLONNES = ["nom", "prenom", "classe", "date_naissance", "telephone_parent"]
OBLIGATOIRES = {"nom", "prenom", "classe"}
MAX_LIGNES = 2000


def modele_import_eleves(noms_classes: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Eleves"
    ws.append(COLONNES)
    ws.append(["Diallo", "Aminata", noms_classes[0] if noms_classes else "6ème A", "15/03/2014", "+224 620 00 00 00"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
    for col, largeur in zip("ABCDE", (18, 18, 16, 16, 22)):
        ws.column_dimensions[col].width = largeur
    ws.freeze_panes = "A2"

    if noms_classes:
        # Liste déroulante des classes existantes dans la colonne C
        listes = wb.create_sheet("Classes")
        for nom in noms_classes:
            listes.append([nom])
        dv = DataValidation(type="list", formula1=f"=Classes!$A$1:$A${len(noms_classes)}", allow_blank=False)
        dv.error = "Choisis une classe de la liste"
        ws.add_data_validation(dv)
        dv.add(f"C2:C{MAX_LIGNES + 1}")

    aide = wb.create_sheet("Aide")
    for ligne in [
        ["Comment remplir ce fichier"],
        ["• Une ligne par élève, à partir de la ligne 2 de l'onglet « Eleves »."],
        ["• nom, prenom et classe sont obligatoires."],
        ["• classe doit correspondre exactement à une classe déjà créée dans le logiciel."],
        ["• date_naissance au format JJ/MM/AAAA (facultatif)."],
        ["• Supprime la ligne d'exemple avant d'importer."],
    ]:
        aide.append(ligne)
    aide.column_dimensions["A"].width = 90
    aide["A1"].font = Font(bold=True, size=13)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _date(valeur) -> date | None:
    if valeur in (None, ""):
        return None
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    texte = str(valeur).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texte, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date « {texte} » invalide (format attendu JJ/MM/AAAA)")


def lire_import_eleves(contenu: bytes, classes: dict) -> tuple[list[dict], list[str]]:
    """Lit le fichier. Renvoie (lignes valides, erreurs). `classes` : {nom en minuscules: Classe}."""
    try:
        wb = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception:
        return [], ["Impossible de lire le fichier : est-ce bien un fichier Excel .xlsx ?"]
    ws = wb["Eleves"] if "Eleves" in wb.sheetnames else wb.worksheets[0]

    lignes_iter = ws.iter_rows(values_only=True)
    entetes = [str(c).strip().lower() if c is not None else "" for c in next(lignes_iter, [])]
    manquantes = OBLIGATOIRES - set(entetes)
    if manquantes:
        return [], [f"Colonne(s) manquante(s) : {', '.join(sorted(manquantes))}. Utilise le modèle."]
    idx = {nom: entetes.index(nom) for nom in COLONNES if nom in entetes}

    lignes, erreurs = [], []
    for num, row in enumerate(lignes_iter, start=2):
        valeurs = {k: (row[i] if i < len(row) else None) for k, i in idx.items()}
        if all(v in (None, "") for v in valeurs.values()):
            continue  # ligne vide
        if len(lignes) + len(erreurs) >= MAX_LIGNES:
            erreurs.append(f"Plus de {MAX_LIGNES} lignes : découpe le fichier.")
            break
        try:
            for champ in OBLIGATOIRES:
                if valeurs.get(champ) in (None, ""):
                    raise ValueError(f"« {champ} » est vide")
            nom_classe = str(valeurs["classe"]).strip()
            classe = classes.get(nom_classe.lower())
            if not classe:
                raise ValueError(f"la classe « {nom_classe} » n'existe pas")
            tel = valeurs.get("telephone_parent")
            lignes.append({
                "nom": str(valeurs["nom"]).strip(),
                "prenom": str(valeurs["prenom"]).strip(),
                "classe_id": classe.id,
                "date_naissance": _date(valeurs.get("date_naissance")),
                "telephone_parent": str(tel).strip() if tel not in (None, "") else None,
            })
        except ValueError as e:
            erreurs.append(f"Ligne {num} : {e}")
    wb.close()
    if not lignes and not erreurs:
        erreurs.append("Le fichier ne contient aucun élève.")
    return lignes, erreurs
