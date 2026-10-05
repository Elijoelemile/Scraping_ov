"""Rapport Word des tarifs Exotismes du mercredi et du samedi, semaine par semaine.

Même présentation que rapport_ovoyages.py. Une semaine = une ligne du calendrier du site.

Exemples :
    python rapport_exotismes.py
    python rapport_exotismes.py --debut 10-2026 --fin 08-2027 --nuits 7 --ville PAR
"""
import argparse
import re

import scrape_exotismes as ex
from rapport_ovoyages import construire_rapport
from scrape_ovoyages import parse_mois


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=ex.DEFAULT_HOTEL_URL, help="URL de la page hôtel Exotismes")
    p.add_argument("--ville", help="code ville de départ (défaut du site : PAR)")
    p.add_argument("--nuits", help="nombre de nuits (défaut du site : 5)")
    p.add_argument("--debut", type=parse_mois, default=(10, 2026), help="premier mois MM-AAAA (défaut 10-2026)")
    p.add_argument("--fin", type=parse_mois, default=(8, 2027), help="dernier mois MM-AAAA (défaut 08-2027)")
    p.add_argument("--pause", type=float, default=3.0, help="secondes entre deux requêtes (défaut : 3)")
    p.add_argument("--sortie", default="tarifs_mercredi_samedi_exotismes.docx", help="fichier Word de sortie")
    args = p.parse_args()
    ex.PAUSE = args.pause

    url = args.url.split("?")[0]
    ex.attendre()
    page = ex.requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    hotel = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else "Hôtel"
    g = re.search(r"grilles\.jsf\?grilleid=(\d+)", page)
    if not g:
        raise SystemExit("Aucune grille tarifaire trouvée sur " + url)

    grille = ex.GrilleExotismes(g.group(1))
    if args.nuits:
        grille.change("nbNuits", args.nuits)
    if args.ville:
        grille.change("villeDepart", args.ville.upper())

    def selection(suffix):
        sel = grille._form().find("select", id=re.compile(suffix + "$"))
        opt = sel.find("option", selected=True) or sel.find("option")
        return opt.get("value"), opt.get_text(strip=True)

    nuits = selection("nbNuits")[0]
    ville = selection("villeDepart")[1]
    mois_dispo = {v for v, _ in grille.options("dateDepart")}

    def lignes_du_mois(mm, aaaa):
        cle = f"{mm:02d}-{aaaa}"
        if cle not in mois_dispo:
            return []
        grille.change("dateDepart", cle, render="pageformulaire infoAutreBrochure")
        return grille.calendrier()

    construire_rapport(source="Exotismes", url=url, hotel=hotel, ville=ville, nuits=nuits,
                       debut=args.debut, fin=args.fin, sortie=args.sortie, lignes_du_mois=lignes_du_mois)


if __name__ == "__main__":
    main()
