"""Rapport Word des tarifs Ovoyages du mercredi et du samedi, semaine par semaine.

Une semaine = une ligne du calendrier du site (lundi -> dimanche).

Exemples :
    python rapport_ovoyages.py
    python rapport_ovoyages.py --debut 10-2026 --fin 08-2027 --nuits 5 --ville PAR
"""
import argparse
import calendar
import re
from datetime import date, datetime

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

import scrape_ovoyages as ov

MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
           "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
MERCREDI, SAMEDI = 2, 5
BLEU_FONCE = "2E3E50"   # bandeau du calendrier Ovoyages
BLEU_CLAIR = "6DD3FB"   # étiquette « Meilleur prix »
GRIS = "EAEEF2"


def mois_entre(debut, fin):
    (m, a), (mf, af) = debut, fin
    while (a, m) <= (af, mf):
        yield m, a
        m, a = (1, a + 1) if m == 12 else (m + 1, a)


def semaines_mer_sam(lignes, mois, annee):
    """Pour chaque ligne du calendrier : (n°, info mercredi, info samedi). Les cases hors du mois restent vides."""
    par_date = {l["date"]: l for l in lignes}
    semaines = calendar.Calendar(firstweekday=0).monthdatescalendar(annee, mois)
    resultat = []
    for num, jours in enumerate(semaines, start=1):
        cases = []
        for d in (jours[MERCREDI], jours[SAMEDI]):
            cases.append(par_date.get(d.isoformat()) if d.month == mois else {"hors_mois": d})
        resultat.append((num, *cases))
    return resultat


def ombrer(cellule, couleur):
    tc_pr = cellule._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), couleur)
    tc_pr.append(shd)


def ecrire(cellule, texte, gras=False, couleur=None, taille=10, centre=True):
    p = cellule.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if centre else WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run(texte)
    run.bold = gras
    run.font.size = Pt(taille)
    if couleur:
        run.font.color.rgb = RGBColor.from_string(couleur)
    return p


def date_courte(iso):
    d = date.fromisoformat(iso)
    return f"{d.day:02d}/{d.month:02d}"


def remplir_case(cell_date, cell_prix, info):
    if info and "hors_mois" in info:
        d = info["hors_mois"]
        ecrire(cell_date, f"{d.day:02d}/{d.month:02d}", couleur="9AA5B1", taille=9)
        ecrire(cell_prix, "hors mois", couleur="9AA5B1", taille=9)
        ombrer(cell_date, GRIS)
        ombrer(cell_prix, GRIS)
        return
    ecrire(cell_date, date_courte(info["date"]))
    if info["prix_eur"]:
        ecrire(cell_prix, f"{info['prix_eur']} €", gras=True, taille=11)
        if info["meilleur_prix"]:
            ombrer(cell_prix, BLEU_CLAIR)
            ecrire_ligne(cell_prix, "Meilleur prix", taille=8)
        if info.get("compagnie"):
            ecrire_ligne(cell_prix, info["compagnie"], taille=8)
    else:
        ecrire(cell_prix, "pas de départ", couleur="9AA5B1", taille=9)


def ecrire_ligne(cellule, texte, taille=8):
    p = cellule.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(texte)
    r.font.size = Pt(taille)


def tableau_mois(doc, semaines):
    entetes = ["Semaine", "Mercredi", "Tarif mercredi", "Samedi", "Tarif samedi"]
    largeurs = [Cm(2.6), Cm(3.0), Cm(3.6), Cm(3.0), Cm(3.6)]
    t = doc.add_table(rows=1, cols=len(entetes))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(entetes):
        c = t.rows[0].cells[i]
        ecrire(c, h, gras=True, couleur="FFFFFF")
        ombrer(c, BLEU_FONCE)
    for num, mer, sam in semaines:
        cells = t.add_row().cells
        ecrire(cells[0], f"Semaine {num}", gras=True)
        remplir_case(cells[1], cells[2], mer)
        remplir_case(cells[3], cells[4], sam)
    for i, row in enumerate(t.rows):
        # Une ligne ne se coupe pas, et le tableau reste sur une seule page
        tr_pr = row._tr.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:cantSplit"))
        for c, w in zip(row.cells, largeurs):
            c.width = w
            if i < len(t.rows) - 1:
                for par in c.paragraphs:
                    par.paragraph_format.keep_with_next = True
    return t


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=ov.DEFAULT_HOTEL_URL, help="URL de la page hôtel Ovoyages")
    p.add_argument("--ville", default="PAR", help="code ville de départ (défaut : PAR)")
    p.add_argument("--nuits", type=int, help="nombre de nuits (défaut : durée par défaut du site)")
    p.add_argument("--debut", type=ov.parse_mois, default=(10, 2026), help="premier mois MM-AAAA (défaut 10-2026)")
    p.add_argument("--fin", type=ov.parse_mois, default=(8, 2027), help="dernier mois MM-AAAA (défaut 08-2027)")
    p.add_argument("--pause", type=float, default=3.0, help="secondes entre deux requêtes (défaut : 3)")
    p.add_argument("--sortie", default="tarifs_mercredi_samedi.docx", help="fichier Word de sortie")
    args = p.parse_args()
    ov.PAUSE = args.pause

    code = ov.code_catalogue(args.url)
    ville = args.ville.upper()
    dispo = ov.durees(code, ville)
    nuits = args.nuits or next((n for n, v in dispo.items() if v.get("default")), min(dispo))
    if nuits not in dispo:
        raise SystemExit(f"{nuits} nuits non proposées. Durées possibles : {sorted(dispo)}")

    # Nom de l'hôtel : lu sur la page produit
    page = ov.session.get(args.url.split("?")[0], timeout=30).text
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    hotel = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else f"Hôtel {code}"
    hotel = re.sub(r"(\d)$", r"\1*", hotel)  # l'étoile du « 5* » est une icône sur le site

    construire_rapport(
        source="Ovoyages", url=args.url.split("?")[0], hotel=hotel, ville=ov_ville(ville), nuits=nuits,
        debut=args.debut, fin=args.fin, sortie=args.sortie,
        lignes_du_mois=lambda mm, aaaa: ov.calendrier(code, ville, nuits, mm, aaaa)[0],
    )


def construire_rapport(source, url, hotel, ville, nuits, debut, fin, sortie, lignes_du_mois):
    """Écrit le rapport Word. lignes_du_mois(mm, aaaa) renvoie une ligne par jour
    (date, prix_eur, meilleur_prix et, si la source la donne, compagnie)."""
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    doc.add_heading("Tarifs du mercredi et du samedi", level=0)
    infos = doc.add_paragraph()
    infos.add_run(hotel).bold = True
    infos.add_run(f"\nSource : {source} ({url})")
    infos.add_run(f"\nDépart : {ville} · Durée : {nuits} nuits · "
                  f"Période : {MOIS_FR[debut[0]]} {debut[1]} à {MOIS_FR[fin[0]]} {fin[1]}")
    infos.add_run(f"\nPrix par personne (base chambre double), relevés le "
                  f"{datetime.now():%d/%m/%Y} à {datetime.now():%H:%M}. Les tarifs évoluent en continu.")
    note = doc.add_paragraph()
    note.add_run("Une semaine correspond à une ligne du calendrier du site (du lundi au dimanche). "
                 "Les cases « hors mois » appartiennent au mois voisin et figurent dans son propre tableau.").italic = True

    for i, (mm, aaaa) in enumerate(mois_entre(debut, fin)):
        lignes = lignes_du_mois(mm, aaaa)
        semaines = semaines_mer_sam(lignes, mm, aaaa)
        prix_ms = [c["prix_eur"] for _, *cs in semaines for c in cs if c and c.get("prix_eur")]
        print(f"{mm:02d}-{aaaa} : {len(prix_ms)} tarifs mercredi/samedi")
        if i:
            doc.add_paragraph()
        h = doc.add_heading(f"{MOIS_FR[mm]} {aaaa}", level=1)
        h.paragraph_format.keep_with_next = True
        resume = doc.add_paragraph()
        resume.paragraph_format.keep_with_next = True
        if prix_ms:
            resume.add_run(f"Mercredi/samedi le moins cher : {min(prix_ms)} € · le plus cher : {max(prix_ms)} €")
        else:
            resume.add_run("Aucun départ un mercredi ou un samedi ce mois-ci.")
        tableau_mois(doc, semaines)

    doc.save(sortie)
    print(f"\nRapport écrit dans {sortie}")


def ov_ville(code):
    return {"PAR": "Paris", "LYS": "Lyon", "MRS": "Marseille", "NTE": "Nantes", "BOD": "Bordeaux",
            "TLS": "Toulouse", "NCE": "Nice", "SXB": "Strasbourg", "BRU": "Bruxelles"}.get(code, code)


if __name__ == "__main__":
    main()
