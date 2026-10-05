"""Scrape le calendrier des prix d'une grille tarifaire Exotismes (exotismes.fr).

Chaque ligne du calendrier (`calendarLine`) est une semaine : semaine 1 à 5 (ou 6).

Exemples :
    python scrape_exotismes.py
    python scrape_exotismes.py --mois 11-2026 12-2026 --nuits 7 --ville PAR
    python scrape_exotismes.py --mois 10-2026 --compagnie AF --sortie prix.csv
"""
import argparse
import csv
import random
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.exotismes.fr"
GRILLE_URL = BASE + "/reservation/grilles.jsf"
# IBEROSTAR SELECTION BAVARO SUITES 5*
DEFAULT_HOTEL_URL = BASE + "/voyages/184-sejour-republique-dominicaine/25563-iberostar-selection-bavaro-suites.jsf"
JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

PAUSE = 3.0  # secondes entre deux requêtes (modifiable avec --pause)
_derniere_requete = 0.0


def attendre():
    """Laisse au moins PAUSE secondes (+ un peu d'aléatoire) entre deux requêtes au site."""
    global _derniere_requete
    delai = PAUSE + random.uniform(0, PAUSE / 2) - (time.time() - _derniere_requete)
    if delai > 0:
        time.sleep(delai)
    _derniere_requete = time.time()



class GrilleExotismes:
    def __init__(self, grille_id, base=BASE):
        """base : adresse du site (exotismes.fr par défaut ; tout site de la même plateforme convient)."""
        self.grille_id = grille_id
        self.grille_url = base + "/reservation/grilles.jsf"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
        )
        attendre()
        resp = self.session.get(self.grille_url, params={"grilleid": grille_id}, timeout=30)
        resp.raise_for_status()
        self.form_html = resp.text
        self.view_state = self._view_state(resp.text)

    @staticmethod
    def _view_state(html):
        m = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', html)
        return m.group(1) if m else None

    def _form(self):
        return BeautifulSoup(self.form_html, "html.parser").find("form", id="pageformulaire")

    def _select_id(self, suffix):
        """Retrouve l'id complet d'un <select> (les ids j_id_xx changent selon les versions du site)."""
        sel = self._form().find("select", id=re.compile(re.escape(suffix) + "$"))
        if sel is None:
            raise RuntimeError(f"Liste déroulante '{suffix}' introuvable")
        return sel["id"]

    def options(self, suffix):
        sel = self._form().find("select", id=re.compile(re.escape(suffix) + "$"))
        return [(o.get("value"), o.get_text(strip=True)) for o in sel.find_all("option")]

    def _form_values(self):
        form = self._form()
        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name or inp.get("type") == "checkbox" and not inp.has_attr("checked"):
                continue
            data[name] = inp.get("value", "")
        for sel in form.find_all("select"):
            opt = sel.find("option", selected=True) or sel.find("option")
            if opt is not None:
                data[sel["name"]] = opt.get("value", "")
        return data

    def change(self, suffix, value, render="pageformulaire"):
        """Reproduit le onchange JSF d'une liste déroulante (requête AJAX partielle)."""
        sel_id = self._select_id(suffix)
        data = self._form_values()
        data[sel_id] = value
        data.update({
            "javax.faces.ViewState": self.view_state,
            "javax.faces.source": sel_id,
            "javax.faces.partial.event": "change",
            "javax.faces.partial.execute": sel_id,
            "javax.faces.partial.render": render,
            "javax.faces.behavior.event": "change",
            "javax.faces.partial.ajax": "true",
        })
        attendre()
        resp = self.session.post(
            self.grille_url,
            data=data,
            headers={"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{self.grille_url}?grilleid={self.grille_id}"},
            timeout=60,
        )
        resp.raise_for_status()
        self._apply_partial(resp.text)

    def _apply_partial(self, xml_text):
        root = ET.fromstring(xml_text.encode("utf-8"))
        for upd in root.iter("update"):
            uid, content = upd.get("id"), upd.text or ""
            if "ViewState" in uid:
                self.view_state = content
            elif uid == "pageformulaire":
                self.form_html = content
        err = root.find(".//error")
        if err is not None:
            raise RuntimeError("Erreur JSF : " + ET.tostring(err, encoding="unicode"))

    def calendrier(self):
        """Renvoie une ligne par jour du mois : semaine (1..n), jour de la semaine, date, prix…"""
        form = self._form()
        mois = form.find("select", id=re.compile("dateDepart$"))
        mois_sel = (mois.find("option", selected=True) or mois.find("option")).get("value")
        mm, yyyy = mois_sel.split("-")
        lignes = []
        for num_semaine, ligne in enumerate(form.select("div.calendar div.calendarLine"), start=1):
            for idx_jour, case in enumerate(ligne.find_all("div", class_="day", recursive=False)):
                if "notThisMonth" in case.get("class", []):
                    continue
                jour = int(case.find("span", class_="dayNumber").get_text(strip=True))
                prix_div = case.find("div", class_="prix")
                prix, devise = None, ""
                if prix_div is not None:
                    texte = prix_div.get_text()
                    m = re.search(r"\d[\d\s ]*", texte)
                    prix = int(re.sub(r"\D", "", m.group())) if m else None
                    devise = "EUR" if "€" in texte else re.sub(r"[\d\s*.,]", "", texte) or "?"
                cache = {s["id"].rsplit(":_", 1)[-1]: s.get_text(strip=True)
                         for s in case.find_all("span", class_="hidden_data")}
                lien = case.find("a", class_="dayLink")
                lignes.append({
                    "mois": mois_sel,
                    "semaine": num_semaine,
                    "jour_semaine": JOURS[idx_jour],
                    "date": f"{yyyy}-{mm}-{jour:02d}",
                    "prix_eur": prix,
                    "devise": devise,
                    "meilleur_prix": "oui" if case.find(class_="bestPrice") else "",
                    "sur_demande": "oui" if prix_div is not None and "onDemand" in prix_div.get("class", []) else "",
                    "compagnie_code": cache.get("compagnie", ""),
                    "compagnie": lien.get("title", "") if lien else "",
                })
        return lignes


def trouver_grille_id(hotel_url):
    attendre()
    html = requests.get(hotel_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    m = re.search(r"grilles\.jsf\?grilleid=(\d+)", html)
    if not m:
        raise RuntimeError("Aucune grille tarifaire trouvée sur " + hotel_url)
    return m.group(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_HOTEL_URL, help="URL de la page hôtel Exotismes")
    p.add_argument("--grille", help="grilleid (sinon lu depuis --url)")
    p.add_argument("--mois", nargs="*", help="mois au format MM-AAAA (défaut : le mois affiché par le site)")
    p.add_argument("--tous-les-mois", action="store_true", help="scraper tous les mois proposés")
    p.add_argument("--nuits", help="nombre de nuits (défaut du site : 5)")
    p.add_argument("--ville", help="code ville de départ, ex. PAR, LYS, MRS, NTE")
    p.add_argument("--compagnie", help="code compagnie, ex. AF, UX (défaut : meilleur prix)")
    p.add_argument("--pause", type=float, default=3.0, help="secondes entre deux requêtes (défaut : 3)")
    p.add_argument("--sortie", default="prix_exotismes.csv", help="fichier CSV de sortie")
    p.add_argument("--lister", action="store_true", help="afficher les options disponibles et quitter")
    args = p.parse_args()
    global PAUSE
    PAUSE = args.pause

    grille_id = args.grille or trouver_grille_id(args.url)
    g = GrilleExotismes(grille_id)

    if args.lister:
        for suffix in ["nbNuits", "villeDepart", "compagnieSelection", "dateDepart"]:
            print(f"\n{suffix} :")
            for v, t in g.options(suffix):
                print(f"  {v:10} {t}")
        return

    # Même ordre que sur le site : nuits -> ville -> compagnie, puis le mois.
    if args.nuits:
        g.change("nbNuits", args.nuits)
    if args.ville:
        g.change("villeDepart", args.ville)
    if args.compagnie:
        g.change("compagnieSelection", args.compagnie)

    mois_dispo = [v for v, _ in g.options("dateDepart")]
    mois = mois_dispo if args.tous_les_mois else (args.mois or [None])

    resultats = []
    for m in mois:
        if m is not None:
            if m not in mois_dispo:
                print(f"Mois {m} non proposé, ignoré", file=sys.stderr)
                continue
            g.change("dateDepart", m, render="pageformulaire infoAutreBrochure")
        lignes = g.calendrier()
        resultats.extend(lignes)
        print(f"{lignes[0]['mois'] if lignes else m} : {sum(1 for l in lignes if l['prix_eur'])} prix")
        for sem in sorted({l["semaine"] for l in lignes}):
            cases = [f"{l['date'][-2:]}={l['prix_eur'] or '-'}" for l in lignes if l["semaine"] == sem]
            print(f"  Semaine {sem} : " + "  ".join(cases))

    for l in resultats:
        l.update(grille=grille_id, nuits=args.nuits or "5", ville=args.ville or "PAR")
    champs = ["grille", "nuits", "ville", "mois", "semaine", "jour_semaine", "date", "prix_eur",
              "meilleur_prix", "sur_demande", "compagnie_code", "compagnie"]
    with open(args.sortie, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=champs, delimiter=";")
        w.writeheader()
        w.writerows(resultats)
    print(f"\n{len(resultats)} lignes écrites dans {args.sortie}")


if __name__ == "__main__":
    main()
