"""Import / modèle Excel des élèves (openpyxl)."""
import io
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

COLONNES = ["nom", "prenom", "classe", "date_naissance", "telephone_parent",
            "montant_paye", "mode_paiement", "reference_paiement"]
OBLIGATOIRES = {"nom", "prenom", "classe", "montant_paye"}
MODES = {  # texte accepté dans le fichier → valeur enregistrée
    "especes": "especes", "espèces": "especes", "espece": "especes", "espèce": "especes", "cash": "especes",
    "orange money": "orange_money", "orange_money": "orange_money", "om": "orange_money",
    "mtn momo": "mtn_momo", "mtn_momo": "mtn_momo", "momo": "mtn_momo", "mtn": "mtn_momo",
    "virement": "virement",
}
LIBELLES_MODES = ["Espèces", "Orange Money", "MTN MoMo", "Virement"]
MAX_LIGNES = 2000


def modele_import_eleves(noms_classes: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Eleves"
    ws.append(COLONNES)
    ws.append(["Diallo", "Aminata", noms_classes[0] if noms_classes else "6ème A", "15/03/2014",
               "+224 620 00 00 00", 100000, "Orange Money", "OM123456"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
    for col, largeur in zip("ABCDEFGH", (18, 18, 16, 16, 22, 15, 16, 20)):
        ws.column_dimensions[col].width = largeur
    ws.freeze_panes = "A2"

    modes = DataValidation(type="list", formula1='"' + ",".join(LIBELLES_MODES) + '"', allow_blank=True)
    ws.add_data_validation(modes)
    modes.add(f"G2:G{MAX_LIGNES + 1}")

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
        ["• nom, prenom, classe et montant_paye sont obligatoires (chaque élève inscrit a payé quelque chose)."],
        ["• montant_paye : montant déjà versé, sans espace ni « FCFA » (ex : 150000). Il ne peut pas dépasser les frais de la classe."],
        ["• mode_paiement : Espèces, Orange Money, MTN MoMo ou Virement (vide = Espèces)."],
        ["• reference_paiement : n° de transaction (facultatif). Un reçu est créé pour chaque paiement importé."],
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


def _montant(valeur) -> int:
    if isinstance(valeur, (int, float)):
        montant = valeur
    else:  # « 150 000 », « 150000 FCFA »…
        texte = str(valeur).lower().replace("fcfa", "").replace("gnf", "").replace("\u202f", "")
        texte = texte.replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            montant = float(texte)
        except ValueError:
            raise ValueError(f"montant_paye « {valeur} » n'est pas un nombre")
    if montant != int(montant) or montant <= 0:
        raise ValueError(f"montant_paye « {valeur} » doit être un nombre entier supérieur à 0")
    return int(montant)


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
            montant = _montant(valeurs["montant_paye"])
            if montant > classe.frais_annuels:
                raise ValueError(f"montant_paye ({montant:,}) supérieur aux frais de {classe.nom} "
                                 f"({classe.frais_annuels:,})".replace(",", " "))
            mode_txt = str(valeurs.get("mode_paiement") or "especes").strip().lower()
            if mode_txt not in MODES:
                raise ValueError(f"mode_paiement « {valeurs.get('mode_paiement')} » inconnu "
                                 f"(Espèces, Orange Money, MTN MoMo ou Virement)")
            ref = valeurs.get("reference_paiement")
            lignes.append({
                "eleve": {
                    "nom": str(valeurs["nom"]).strip(),
                    "prenom": str(valeurs["prenom"]).strip(),
                    "classe_id": classe.id,
                    "date_naissance": _date(valeurs.get("date_naissance")),
                    "telephone_parent": str(tel).strip() if tel not in (None, "") else None,
                },
                "paiement": {
                    "montant": montant,
                    "mode": MODES[mode_txt],
                    "reference": str(ref).strip() if ref not in (None, "") else None,
                },
            })
        except ValueError as e:
            erreurs.append(f"Ligne {num} : {e}")
    wb.close()
    if not lignes and not erreurs:
        erreurs.append("Le fichier ne contient aucun élève.")
    return lignes, erreurs
