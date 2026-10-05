"""Scrape le calendrier des prix d'un hôtel Ovoyages (ovoyages.com).

Les semaines suivent les lignes du calendrier du site (lundi -> dimanche) :
semaine 1 = ligne qui contient le 1er du mois, puis 2, 3, 4, 5 (voire 6).

Exemples :
    python scrape_ovoyages.py
    python scrape_ovoyages.py --mois 11-2026 12-2026 --nuits 7 --ville PAR
    python scrape_ovoyages.py --tous-les-mois --nuits 7
    python scrape_ovoyages.py --url "https://www.ovoyages.com/...-108504" --lister
"""
import argparse
import calendar
import csv
import random
import re
import sys
import time
from datetime import date

import requests

BASE = "https://www.ovoyages.com"
# Hôtel Coral Level at Iberostar Selection Bavaro 5*
DEFAULT_HOTEL_URL = BASE + "/voyages-rep-dominicaine/vacances-punta-cana/hotel-coral-level-at-iberostar-selection-bavaro-5-108504"
PAUSE = 3.0  # secondes entre deux requêtes (modifiable avec --pause)
_derniere_requete = 0.0


def attendre():
    """Laisse au moins PAUSE secondes (+ un peu d'aléatoire) entre deux requêtes au site."""
    global _derniere_requete
    delai = PAUSE + random.uniform(0, PAUSE / 2) - (time.time() - _derniere_requete)
    if delai > 0:
        time.sleep(delai)
    _derniere_requete = time.time()

JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
})


def code_catalogue(url):
    """Le code hôtel est le nombre à la fin de l'URL (…-bavaro-5-108504)."""
    m = re.search(r"-(\d+)(?:[?#/]|$)", url)
    if not m:
        raise ValueError("Code hôtel introuvable dans l'URL : " + url)
    return m.group(1)


def get_json(path):
    attendre()
    resp = session.get(BASE + path, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.text.strip() else {}


def durees(code, ville):
    """{nuits: prix 'à partir de'} pour la ville de départ."""
    data = get_json(f"/pricetable/duration/{code}/{ville}")
    return {int(v["nbNights"]): v for v in data.values()} if isinstance(data, dict) else {}


def table_prix(code, ville, nuits, mois, annee):
    return get_json(f"/pricetable/{code}/{ville}/{nuits}/{mois:02d}/{annee}")


def semaine_du_mois(d):
    """Numéro de la ligne du calendrier (lundi en premier) où tombe la date."""
    premier = d.replace(day=1).weekday()  # 0 = lundi
    return (d.day - 1 + premier) // 7 + 1


def calendrier(code, ville, nuits, mois, annee):
    data = table_prix(code, ville, nuits, mois, annee)
    dates = data.get("dates") or {}
    lignes = []
    for jour in range(1, calendar.monthrange(annee, mois)[1] + 1):
        d = date(annee, mois, jour)
        info = dates.get(d.isoformat(), {})
        prix = info.get("price")
        lignes.append({
            "mois": f"{mois:02d}-{annee}",
            "semaine": semaine_du_mois(d),
            "jour_semaine": JOURS[d.weekday()],
            "date": d.isoformat(),
            "prix_eur": int(prix) if prix else None,
            "meilleur_prix": "oui" if info.get("bestPrice") else "",
            "promo": "oui" if info.get("inPromotionTo") or info.get("promoReduc") else "",
            "prix_brochure": info.get("brochurePrice") or "",
            "retour": (info.get("endDate") or {}).get("date", "")[:10],
            "nuits_reelles": info.get("durationInNights", ""),
        })
    return lignes, data.get("priceMonth") or {}


def parse_mois(s):
    m = re.fullmatch(r"(\d{1,2})-(\d{4})", s)
    if not m:
        raise argparse.ArgumentTypeError("format attendu MM-AAAA, ex. 10-2026")
    return int(m.group(1)), int(m.group(2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_HOTEL_URL, help="URL de la page hôtel Ovoyages")
    p.add_argument("--code", help="code hôtel (sinon lu depuis --url)")
    p.add_argument("--ville", default="PAR", help="code ville de départ (PAR, LYS, MRS, NTE, BOD, TLS…)")
    p.add_argument("--nuits", type=int, help="nombre de nuits (défaut : durée par défaut du site)")
    p.add_argument("--mois", nargs="*", type=parse_mois, help="mois au format MM-AAAA")
    p.add_argument("--tous-les-mois", action="store_true", help="scraper tous les mois proposés")
    p.add_argument("--pause", type=float, default=3.0, help="secondes entre deux requêtes (défaut : 3)")
    p.add_argument("--sortie", default="prix_ovoyages.csv", help="fichier CSV de sortie")
    p.add_argument("--lister", action="store_true", help="afficher les durées et les mois proposés et quitter")
    args = p.parse_args()
    global PAUSE
    PAUSE = args.pause

    code = args.code or code_catalogue(args.url)
    ville = args.ville.upper()

    dispo = durees(code, ville)
    if not dispo:
        sys.exit(f"Aucune durée proposée au départ de {ville} pour l'hôtel {code}")
    nuits = args.nuits or next((n for n, v in dispo.items() if v.get("default")), min(dispo))
    if nuits not in dispo:
        sys.exit(f"{nuits} nuits non proposées. Durées possibles : {sorted(dispo)}")

    # Le 1er appel renvoie la liste des mois avec un prix ("priceMonth") et le mois par défaut.
    premier = table_prix(code, ville, nuits, date.today().month, date.today().year)
    mois_dispo = [(int(k[4:]), int(k[:4])) for k in sorted(premier.get("priceMonth") or {})]

    if args.lister:
        print(f"Hôtel {code}, départ {ville}")
        print("Durées (nuits : prix à partir de) :")
        for n, v in sorted(dispo.items()):
            print(f"  {n:3} nuits : {v.get('price')} €" + ("  (défaut)" if v.get("default") else ""))
        print(f"Mois proposés pour {nuits} nuits (prix à partir de) :")
        for k, v in sorted((premier.get("priceMonth") or {}).items()):
            print(f"  {k[4:]}-{k[:4]} : {v} €")
        return

    if args.tous_les_mois:
        mois = mois_dispo
    elif args.mois:
        mois = args.mois
    else:
        mois = [(int(premier.get("defaultMonth") or date.today().month),
                 int(premier.get("defaultYear") or date.today().year))]

    resultats = []
    for mm, aaaa in mois:
        lignes, _ = calendrier(code, ville, nuits, mm, aaaa)
        resultats.extend(lignes)
        print(f"{mm:02d}-{aaaa} : {sum(1 for l in lignes if l['prix_eur'])} prix")
        for sem in sorted({l["semaine"] for l in lignes}):
            cases = [f"{l['date'][-2:]}={l['prix_eur'] or '-'}" for l in lignes if l["semaine"] == sem]
            print(f"  Semaine {sem} : " + "  ".join(cases))

    for l in resultats:
        l.update(hotel=code, nuits=nuits, ville=ville)
    champs = ["hotel", "nuits", "ville", "mois", "semaine", "jour_semaine", "date", "prix_eur",
              "meilleur_prix", "promo", "prix_brochure", "retour", "nuits_reelles"]
    with open(args.sortie, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=champs, delimiter=";")
        w.writeheader()
        w.writerows(resultats)
    print(f"\n{len(resultats)} lignes écrites dans {args.sortie}")


if __name__ == "__main__":
    main()
