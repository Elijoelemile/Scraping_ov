"""Connecteurs de sites : chaque site sait chercher un Produit par son nom et lire ses prix.

Un connecteur correspond à une PLATEFORME de réservation : tous les sites qui l'utilisent
(Ovoyages, Exotismes, et ceux ajoutés depuis l'application) se lisent de la même façon.

Interface commune :
    rechercher(nom)                        -> [Candidat]
    villes(candidat)                       -> {code: libellé}
    nuits(candidat, ville)                 -> [int]
    mois(candidat, ville, nuits)           -> [(mm, aaaa)]
    prix(candidat, ville, nuits, mm, aaaa) -> [{"date", "prix_eur", "meilleur_prix", "compagnie"}]
"""
import calendar
import difflib
import json
import os
import random
import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import scrape_exotismes as ex

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_CACHE = os.path.join(DOSSIER, "cache")
FICHIER_SITES = os.path.join(DOSSIER, "sites_ajoutes.json")
os.makedirs(DOSSIER_CACHE, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PAUSE = 3.0  # secondes entre deux requêtes vers un même site
DELAI = (10, 25)  # secondes max. pour se connecter, puis pour recevoir la réponse : un site en panne ne bloque pas longtemps


@dataclass
class Candidat:
    site: str
    nom: str
    url: str
    code: str
    destination: str = ""
    score: float = 0.0

    def libelle(self):
        dest = f" · {self.destination}" if self.destination else ""
        return f"{self.nom}{dest} (réf. {self.code})"


# ---------------------------------------------------------------- outils communs

MOTS_VIDES = {"hotel", "hôtel", "the", "at", "by", "de", "la", "le", "les", "et", "a", "en", "sejour", "voyage"}


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return [m for m in re.findall(r"[a-z0-9]+", texte) if m not in MOTS_VIDES]


def score_nom(requete, nom):
    """1.0 = tous les mots de la requête sont dans le nom ; départage par ressemblance globale."""
    q, n = normaliser(requete), normaliser(nom)
    if not q:
        return 0.0
    trouves = sum(1 for m in q if any(x == m or x.startswith(m) for x in n))
    ressemblance = difflib.SequenceMatcher(None, " ".join(q), " ".join(n)).ratio()
    return trouves / len(q) + 0.2 * ressemblance


def slug_en_nom(slug):
    return " ".join(m.capitalize() if not m.isdigit() else m for m in slug.replace("-", " ").split())


def _chemin_cache(cle):
    return os.path.join(DOSSIER_CACHE, re.sub(r"[^A-Za-z0-9_.-]", "_", cle) + ".json")


def cache_lire(cle, duree_s):
    try:
        if time.time() - os.path.getmtime(_chemin_cache(cle)) < duree_s:
            with open(_chemin_cache(cle), encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return None


def cache_age(cle):
    """Âge en secondes d'une entrée du cache."""
    return time.time() - os.path.getmtime(_chemin_cache(cle))


def cache_ecrire(cle, valeur):
    with open(_chemin_cache(cle), "w", encoding="utf-8") as f:
        json.dump(valeur, f, ensure_ascii=False)


def hote(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


class Connecteur:
    plateforme = ""
    type_site = "forfait"  # « forfait » (vol + hôtel) ou « hotel_seul » : deux types qu'on ne compare pas entre eux

    def __init__(self, nom, base):
        self.nom, self.base = nom, base.rstrip("/")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self._derniere = 0.0

    def attendre(self):
        """Laisse au moins PAUSE secondes (+ un peu d'aléatoire) entre deux requêtes au site."""
        delai = PAUSE + random.uniform(0, PAUSE / 2) - (time.time() - self._derniere)
        if delai > 0:
            time.sleep(delai)
        self._derniere = time.time()

    def get(self, url, **kw):
        self.attendre()
        return self.session.get(url if url.startswith("http") else self.base + url, timeout=kw.pop("timeout", DELAI), **kw)

    # Interface à implémenter par chaque plateforme
    def rechercher(self, nom, limite=8): raise NotImplementedError
    def villes(self, candidat): raise NotImplementedError
    def nuits(self, candidat, ville): raise NotImplementedError
    def mois(self, candidat, ville, nuits): raise NotImplementedError
    def prix(self, candidat, ville, nuits, mm, aaaa): raise NotImplementedError

    # Utilisé par l'analyse d'un nouveau site
    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        """True si les pages ressemblent à cette plateforme."""
        raise NotImplementedError

    def adresse_prix(self, candidat):
        """Une adresse de prix typique (pour vérifier le robots.txt)."""
        raise NotImplementedError


# ---------------------------------------------------------------- Plateforme « calendrier JSON » (Ovoyages)

class PlateformeCalendrierJSON(Connecteur):
    """Produits listés dans le plan du site ; prix via /pricetable/<code>/<ville>/<nuits>/<mois>/<année>."""
    plateforme = "calendrier-json"

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self.session.headers.update({"X-Requested-With": "XMLHttpRequest",
                                     "Accept": "application/json, text/javascript, */*; q=0.01"})

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "/pricetable/" in (accueil_html + produit_html) or 'class="catalogCode"' in produit_html

    def _json(self, chemin):
        r = self.get(chemin)
        r.raise_for_status()
        return r.json() if r.text.strip() else {}

    def catalogue(self):
        cle = f"catalogue_{hote(self.base)}"
        cat = cache_lire(cle, 24 * 3600)
        if cat is None:
            xml = self.get("/sitemap.xml").text
            sous = re.findall(r"<sitemap>\s*<loc>([^<]+)</loc>", xml)
            sources = [u for u in sous if "product" in u.lower()] or sous[:5]
            urls = re.findall(r"<url>\s*<loc>([^<]+)</loc>", xml)
            for s in sources:
                urls += re.findall(r"<loc>([^<]+)</loc>", self.get(s, timeout=60).text)
            cat = []
            for url in urls:
                m = re.search(r"/([^/]+)-(\d+)$", url)
                if not m:
                    continue
                morceaux = url.split("/")
                dest = morceaux[-2].replace("vacances-", "").replace("voyages-", "") if len(morceaux) > 5 else ""
                cat.append({"nom": re.sub(r" (\d)$", r" \1*", slug_en_nom(m.group(1))), "url": url,
                            "code": m.group(2), "destination": slug_en_nom(dest)})
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], p["destination"], score_nom(nom, p["nom"]))
               for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def villes(self, candidat):
        cle = f"villes_{hote(self.base)}_{candidat.code}"
        villes = cache_lire(cle, 24 * 3600)
        if villes is None:
            sel = BeautifulSoup(self.get(candidat.url).text, "html.parser").find("select", class_="depCityCode")
            villes = {o["value"]: o.get_text(strip=True) for o in sel.find_all("option")
                      if o.get("value") and o.get_text(strip=True)} if sel else {}
            cache_ecrire(cle, villes)
        return villes

    def nuits(self, candidat, ville):
        data = self._json(f"/pricetable/duration/{candidat.code}/{ville}")
        return sorted(int(v["nbNights"]) for v in data.values()) if isinstance(data, dict) else []

    def mois(self, candidat, ville, nuits):
        t = time.localtime()
        data = self._json(f"/pricetable/{candidat.code}/{ville}/{nuits}/{t.tm_mon:02d}/{t.tm_year}")
        return [(int(k[4:]), int(k[:4])) for k in sorted(data.get("priceMonth") or {})]

    def prix(self, candidat, ville, nuits, mm, aaaa):
        data = self._json(f"/pricetable/{candidat.code}/{ville}/{nuits}/{mm:02d}/{aaaa}")
        res = []
        for d, info in (data.get("dates") or {}).items():
            if info.get("price") and d.startswith(f"{aaaa}-{mm:02d}"):
                res.append({"date": d, "prix_eur": int(info["price"]), "meilleur_prix": bool(info.get("bestPrice")),
                            "compagnie": ""})
        return sorted(res, key=lambda r: r["date"])

    def adresse_prix(self, candidat):
        return f"{self.base}/pricetable/{candidat.code}/PAR/7/01/2027"


# ---------------------------------------------------------------- Plateforme « grille JSF » (Exotismes)

class PlateformeGrilleJSF(Connecteur):
    """Recherche par mots-clés du site ; prix via la grille tarifaire /reservation/grilles.jsf."""
    plateforme = "grille-jsf"

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self._grilles = {}  # une grille ouverte par (Produit, ville, nuits)

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "grilles.jsf?grilleid=" in produit_html or (
            "javax.faces" in accueil_html and re.search(r'id="[^"]*homeSearch"', accueil_html) is not None)

    def rechercher(self, nom, limite=8):
        cle = f"recherche_{hote(self.base)}_" + "_".join(normaliser(nom))
        trouves = cache_lire(cle, 24 * 3600)
        if trouves is None:
            accueil = self.get("/").text
            champ = BeautifulSoup(accueil, "html.parser").find("input", id=re.compile("homeSearch$"))
            if champ is None:
                raise RuntimeError("moteur de recherche introuvable")
            form = champ.find_parent("form")
            data = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")}
            data[champ["name"]] = nom
            self.attendre()
            html = self.session.post(self.base + form["action"], data=data, timeout=DELAI).text
            vus = {}
            for url, categorie, code, slug in re.findall(r'href="(/voyages/\d+-([^/"]+)/(\d+)-([^"/]+)\.jsf)"', html):
                vus[code] = {"nom": slug_en_nom(slug), "url": self.base + url, "code": code,
                             "destination": slug_en_nom(re.sub(r"^(sejour|circuit|croisiere|combine)-", "", categorie))}
            trouves = list(vus.values())
            cache_ecrire(cle, trouves)
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], p["destination"], score_nom(nom, p["nom"]))
               for p in trouves]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _grille_id(self, candidat):
        m = re.search(r"grilles\.jsf\?grilleid=(\d+)", self.get(candidat.url).text)
        if not m:
            raise RuntimeError("aucune grille tarifaire sur la page du Produit")
        return m.group(1)

    def _grille(self, candidat, ville=None, nuits=None):
        cle = (candidat.code, ville, nuits)
        if cle not in self._grilles:
            g = ex.GrilleExotismes(self._grille_id(candidat), base=self.base)
            if nuits:
                g.change("nbNuits", str(nuits))
            if ville and ville != "PAR":
                g.change("villeDepart", ville)
            self._grilles[cle] = g
        return self._grilles[cle]

    def villes(self, candidat):
        return {v: t for v, t in self._grille(candidat).options("villeDepart")}

    def nuits(self, candidat, ville):
        return sorted(int(v) for v, _ in self._grille(candidat).options("nbNuits") if v.isdigit())

    def mois(self, candidat, ville, nuits):
        return [(int(v[:2]), int(v[3:])) for v, _ in self._grille(candidat, ville, nuits).options("dateDepart")]

    def prix(self, candidat, ville, nuits, mm, aaaa):
        g = self._grille(candidat, ville, nuits)
        cle = f"{mm:02d}-{aaaa}"
        if cle not in {v for v, _ in g.options("dateDepart")}:
            return []
        g.change("dateDepart", cle, render="pageformulaire infoAutreBrochure")
        lignes = [l for l in g.calendrier() if l["prix_eur"]]
        autres = {l["devise"] for l in lignes} - {"EUR"}
        if autres:
            raise RuntimeError(f"prix en {', '.join(autres)} : non comparables à des prix en euros")
        return [{"date": l["date"], "prix_eur": l["prix_eur"], "meilleur_prix": bool(l["meilleur_prix"]),
                 "compagnie": l["compagnie"]} for l in lignes]

    def adresse_prix(self, candidat):
        return f"{self.base}/reservation/grilles.jsf?grilleid=1"


# ---------------------------------------------------------------- Plateforme « catalogue Fram »

# Villes de départ Fram (libellé -> code IATA, pour les comparer aux autres sites)
IATA_VILLES = {
    "paris": "PAR", "lyon": "LYS", "marseille": "MRS", "nantes": "NTE", "bordeaux": "BOD", "toulouse": "TLS",
    "nice": "NCE", "lille": "LIL", "strasbourg": "SXB", "brest": "BES", "rennes": "RNS", "montpellier": "MPL",
    "bruxelles": "BRU", "bruxelles ou charleroi": "BRU", "geneve": "GVA", "bale": "BSL", "bale mulhouse": "BSL",
    "luxembourg": "LUX", "francfort": "FRA", "amsterdam": "AMS", "barcelone": "BCN", "bilbao": "BIO",
    "pau": "PUF", "biarritz": "BIQ", "clermont ferrand": "CFE", "metz": "ETZ", "mulhouse": "MLH", "tours": "TUF",
}


def _code_ville(libelle, ident):
    cle = " ".join(normaliser_mots(libelle))
    return IATA_VILLES.get(cle, f"V{ident}")  # ville sans code connu : identifiant Fram


def normaliser_mots(texte):
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", texte)


class PlateformeCatalogueFram(Connecteur):
    """Produits listés dans le plan du site ; options lues dans la page Produit ;
    prix via /api/ajax/catalogueProduit/calendriers (tous les mois en un appel par ville et durée)."""
    plateforme = "catalogue-fram"
    DUREE_MEMOIRE = 120  # s : un calendrier lu sert pour tous les mois d'un même relevé

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self.session.headers["Accept"] = "application/json, text/plain, */*"
        self._calendriers = {}

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "__INITIAL_STATE__" in produit_html and "departureMonths" in produit_html and "disponibilities" in produit_html

    def catalogue(self):
        cle = f"catalogue_{hote(self.base)}"
        cat = cache_lire(cle, 24 * 3600)
        if cat is None:
            index = self.get("/sitemap.xml").text
            sous = re.findall(r"<loc>([^<]+)</loc>", index)
            urls = []
            for s in [u for u in sous if "produit" in u.lower()] or sous:
                urls += re.findall(r"<loc>([^<]+)</loc>", self.get(s, timeout=(10, 90)).text)
            cat = []
            for url in urls:
                m = re.search(r"/([^/]+)-(\d+)\.html$", url)
                if m:
                    slug = re.sub(r"^(hotel|club|circuit|sejour|autotour|croisiere)-", "", m.group(1))
                    cat.append({"nom": slug_en_nom(slug), "url": url, "code": m.group(2)})
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], "", score_nom(nom, p["nom"])) for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _fiche(self, candidat):
        """Options du Produit lues dans sa page : offre de référence, formule, villes et durées."""
        cle = f"fiche_{hote(self.base)}_{candidat.code}"
        fiche = cache_lire(cle, 24 * 3600)
        if fiche is None:
            html = self.get(candidat.url).text
            debut = html.find("window.__INITIAL_STATE__")
            etat = html[debut:debut + html[debut:].find("</script>")] if debut >= 0 else ""
            paquet = re.search(r'idPackage: "(\d+)"', etat)
            if not paquet:
                raise RuntimeError("aucun calendrier de prix sur la page du Produit")
            bloc_villes = re.search(r"departureCities: \[(.*?)\],\s*pensionTypes", etat, re.S)
            villes = re.findall(r'label: "([^"]+)",\s*value: "([^"]+)"', bloc_villes.group(1)) if bloc_villes else []
            pension = re.search(r'pensionTypes: \[\s*\{\s*label: "([^"]+)",\s*value: "([^"]+)"', etat)
            nuits = sorted({int(n) for n in re.findall(r"label: '(\d+) nuit'", etat)})
            fiche = {"idPackage": paquet.group(1),
                     "villes": [{"libelle": l.strip(), "id": re.sub(r"\D", "", v)} for l, v in villes],
                     "pension": pension.group(2) if pension else "", "pension_libelle": pension.group(1) if pension else "",
                     "nuits": nuits}
            cache_ecrire(cle, fiche)
        return fiche

    def villes(self, candidat):
        res = {}
        for v in self._fiche(candidat)["villes"]:
            res.setdefault(_code_ville(v["libelle"], v["id"]), v["libelle"])
        return res

    def nuits(self, candidat, ville):
        return self._fiche(candidat)["nuits"]

    def _calendrier(self, candidat, ville, nuits):
        cle = (candidat.code, ville, nuits)
        memo = self._calendriers.get(cle)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        fiche = self._fiche(candidat)
        ident = next((v["id"] for v in fiche["villes"] if _code_ville(v["libelle"], v["id"]) == ville), None)
        if ident is None:
            return {}
        r = self.get("/api/ajax/catalogueProduit/calendriers", headers={"Referer": candidat.url}, params={
            "idVilleDepart": ident, "idPackage": fiche["idPackage"], "intervalleDureeNuit.min": nuits,
            "intervalleDureeNuit.max": nuits, "codePension": fiche["pension"], "idHebergement": candidat.code})
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "S_OK":
            data = {}
        self._calendriers[cle] = (time.time(), data)
        return data

    def _departs(self, candidat, ville, nuits):
        fiche = self._fiche(candidat)
        for mois in self._calendrier(candidat, ville, nuits).get("moisAnnees") or []:
            for jour in mois.get("dateDeparts") or []:
                for c in jour.get("calendriers") or []:
                    prix = c.get("prix") or {}
                    if (c.get("disponible") and prix.get("prix") and prix.get("typePrix") == "PERSONNE"
                            and (c.get("duree") or {}).get("nuit", {}).get("value") == nuits):
                        yield {"date": jour["date"], "prix_eur": int(round(prix["prix"])),
                               "meilleur_prix": bool(c.get("meilleurPrix")), "compagnie": "",
                               "retour": ((c.get("periodeVoyage") or {}).get("fin") or {}).get("value", ""),
                               "formule": fiche.get("pension_libelle", ""), "voyagiste": "Fram"}

    def mois(self, candidat, ville, nuits):
        return sorted({(int(d["date"][5:7]), int(d["date"][:4])) for d in self._departs(candidat, ville, nuits)},
                      key=lambda m: (m[1], m[0]))

    def prix(self, candidat, ville, nuits, mm, aaaa):
        meilleurs = {}
        for d in self._departs(candidat, ville, nuits):
            if d["date"].startswith(f"{aaaa}-{mm:02d}") and (d["date"] not in meilleurs
                                                              or d["prix_eur"] < meilleurs[d["date"]]["prix_eur"]):
                meilleurs[d["date"]] = d  # plusieurs offres le même jour : on garde la moins chère
        return [meilleurs[k] for k in sorted(meilleurs)]

    def adresse_prix(self, candidat):
        return f"{self.base}/api/ajax/catalogueProduit/calendriers"


# ---------------------------------------------------------------- Hôtel seul : Pick Albatros

VILLE_HOTEL_SEUL = "HOTEL"  # pas de ville de départ : l'hôtel seul se réserve sans vol


def taux_usd():
    """Taux de change officiel de la Banque centrale européenne : (dollars pour 1 €, date du taux)."""
    taux = cache_lire("taux_bce_usd", 12 * 3600)
    if taux is None:
        xml = requests.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
                           headers={"User-Agent": UA}, timeout=DELAI).text
        taux = {"usd": float(re.search(r"currency='USD' rate='([\d.]+)'", xml).group(1)),
                "date": re.search(r"time='([\d-]+)'", xml).group(1)}
        cache_ecrire("taux_bce_usd", taux)
    return taux["usd"], taux["date"]


def _montant(texte):
    """« $ 1,273.50 » -> 1273.5"""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", texte or "")
    return float(m.group().replace(",", "")) if m else None


class PlateformePickAlbatros(Connecteur):
    """Site d'hôtels (sans vol) : hôtels Pickalbatros, prix par chambre et par nuit en dollars, lus dans le
    calendrier de tarifs du moteur de réservation (TravelClick / Amadeus). Les prix renvoyés sont convertis
    en euros (taux BCE) et donnés par personne : prix de la chambre pour le séjour ÷ 2 (base chambre double)."""
    plateforme = "hotel-seul-albatros"
    type_site = "hotel_seul"
    NUITS = list(range(1, 22))
    DUREE_MEMOIRE = 120  # s : un calendrier lu sert pour tous les mois d'un même relevé

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self._calendriers, self._relais = {}, None

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return False  # site intégré à l'application, jamais ajouté par l'analyse automatique

    def catalogue(self):
        """Les hôtels et leur numéro de réservation : chaque lien TravelClick de l'accueil redirige
        vers la page de réservation de l'hôtel (/<hôtel>/book/dates-of-stay)."""
        cat = cache_lire(f"catalogue_{hote(self.base)}", 7 * 24 * 3600)
        if cat is None:
            accueil = self.get("/fr").text
            cat = []
            for ident in sorted(set(re.findall(r"reservations\.travelclick\.com/(\d+)", accueil))):
                url = ""
                for _ in range(2):  # un lien lent ne doit pas faire échouer tout le catalogue
                    try:
                        url = self.get(f"https://reservations.travelclick.com/{ident}?", allow_redirects=True).url
                        break
                    except requests.RequestException:
                        continue
                m = re.search(r"pickalbatros\.com/([a-z0-9-]+)/book/", url)
                if m:
                    cat.append({"nom": "Pickalbatros " + slug_en_nom(m.group(1)), "code": ident,
                                "url": f"{self.base}/{m.group(1)}", "slug": m.group(1)})
            cache_ecrire(f"catalogue_{hote(self.base)}", cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], "Hôtel seul", score_nom(nom, p["nom"]))
               for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def villes(self, candidat):
        return {VILLE_HOTEL_SEUL: "Hôtel seul (sans vol)"}

    def nuits(self, candidat, ville):
        return self.NUITS

    def _relais_prix(self, candidat):
        """Adresse et clé publique du relais de prix, écrites dans la page de réservation (comme pour tout visiteur)."""
        if self._relais is None:
            html = self.get(f"{candidat.url}/book/dates-of-stay").text
            url = re.search(r"""proxy_url['"]?\s*:\s*['"]([^'"]+)""", html)
            cle = re.search(r"""proxy_key['"]?\s*:\s*['"]([^'"]+)""", html)
            if not (url and cle):
                raise RuntimeError("calendrier de tarifs introuvable")
            self._relais = (url.group(1).rstrip("/"), cle.group(1), f"{candidat.url}/book/dates-of-stay")
        return self._relais

    def _tarifs_nuit(self, candidat, debut):
        """{date: prix de la chambre pour la nuit, en dollars} sur 91 jours à partir de debut."""
        cle = (candidat.code, debut)
        memo = self._calendriers.get(cle)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        url, cle_publique, page = self._relais_prix(candidat)
        r = self.get(f"{url}/tc/shop/v1/hotel/{candidat.code}/calendar", params={"dateIn": debut, "lang": "fr"},
                     headers={"X-Galaxy-Key": cle_publique, "Referer": page, "Accept": "application/json"})
        r.raise_for_status()
        tarifs = {}
        for jour in r.json() or []:
            montant = _montant(jour.get("rate_discounted") or jour.get("rate"))
            if jour.get("is_available") and montant and "$" in (jour.get("rate_discounted") or jour.get("rate") or ""):
                tarifs[jour["date"]] = montant
        self._calendriers[cle] = (time.time(), tarifs)
        return tarifs

    def _sejours(self, candidat, nuits, mm, aaaa):
        """Prix de chaque arrivée du mois pour `nuits` nuits : toutes les nuits doivent être disponibles."""
        from datetime import date, timedelta
        premier = max(date(aaaa, mm, 1), date.today())
        dernier = date(aaaa, mm, calendar.monthrange(aaaa, mm)[1])
        if premier > dernier:
            return []
        tarifs = self._tarifs_nuit(candidat, premier.isoformat())  # 91 jours : couvre le mois et le séjour
        usd, _ = taux_usd()
        res = []
        d = premier
        while d <= dernier:
            nuits_sejour = [(d + timedelta(days=k)).isoformat() for k in range(nuits)]
            if all(n in tarifs for n in nuits_sejour):
                chambre_usd = sum(tarifs[n] for n in nuits_sejour)
                res.append({"date": d.isoformat(), "prix_eur": int(round(chambre_usd / usd / 2)),
                            "meilleur_prix": False, "compagnie": "",
                            "retour": (d + timedelta(days=nuits)).isoformat(), "voyagiste": "Pick Albatros"})
            d += timedelta(days=1)
        return res

    def mois(self, candidat, ville, nuits):
        """Mois qui ont au moins une arrivée possible, dans les 12 prochains mois."""
        res = []
        for mm, aaaa in mois_suivants(12):
            if self._sejours(candidat, nuits, mm, aaaa):
                res.append((mm, aaaa))
            elif res:  # le calendrier ne va pas plus loin
                break
        return res

    def prix(self, candidat, ville, nuits, mm, aaaa):
        return self._sejours(candidat, nuits, mm, aaaa)

    def adresse_prix(self, candidat):
        return f"{self.base}/tc/shop/v1/hotel/{candidat.code}/calendar"


# ---------------------------------------------------------------- Relevé fait dans le navigateur (Promoséjours)

DOSSIER_IMPORTS = os.path.join(DOSSIER, "imports")
FORMULES = {"TI": "Tout inclus", "PC": "Pension complète", "DP": "Demi-pension", "PD": "Petit déjeuner",
            "LS": "Logement seul", "LO": "Logement seul", "SA": "Logement seul"}


def _fichier_import(base):
    return os.path.join(DOSSIER_IMPORTS, re.sub(r"[^A-Za-z0-9_.-]", "_", hote(base)) + ".json")


def importer_releve(contenu):
    """Importe un fichier exporté par l'extension « Relevé de prix ». Fusionne avec les relevés déjà importés
    (pour une même date, le relevé le plus récent l'emporte) et ajoute le site à la liste si besoin.
    Renvoie un résumé : {"site", "produits", "lignes", "nouvelles"}."""
    data = json.loads(contenu)
    if data.get("format") != "comparateur-releve-navigateur":
        raise ValueError("ce fichier n'est pas un relevé exporté par l'extension")
    base = data["base"].rstrip("/")
    chemin = _fichier_import(base)
    try:
        with open(chemin, encoding="utf-8") as f:
            stock = json.load(f)
    except (OSError, ValueError):
        stock = {"site": data["site"], "base": base, "produits": {}, "lignes": []}
    stock["produits"].update(data.get("produits") or {})
    lignes = {(l["code"], l["ville"], l["nuits"], l["date"]): l for l in stock["lignes"]}
    avant = len(lignes)
    for c in data.get("captures") or []:
        rep, params = c.get("reponse") or {}, c.get("params") or {}
        code = str(params.get("productId") or "")
        ville = (rep.get("availabilityForm") or {}).get("departureCityCode") or params.get("departureCityCode")
        for a in rep.get("availabilityList") or []:
            if not (code and ville and a.get("dt") and a.get("p") and a.get("n")):
                continue
            if a.get("t") not in (None, "BY_PERSON"):  # on ne compare que des prix par personne
                continue
            ligne = {"code": code, "ville": ville, "nuits": int(a["n"]), "date": a["dt"], "prix": int(a["p"]),
                     "meilleur_prix": bool(a.get("bestMonthPrice")), "retour": a.get("dr") or "",
                     "formule": FORMULES.get(a.get("ml"), a.get("ml") or ""),
                     "vol_direct": "Oui" if a.get("directFlight") else "Non",
                     "voyagiste": ((data.get("produits") or {}).get(code) or {}).get("voyagiste", ""),
                     "capture_le": c.get("capture_le", "")}
            cle = (code, ville, ligne["nuits"], ligne["date"])
            if cle not in lignes or ligne["capture_le"] >= lignes[cle].get("capture_le", ""):
                lignes[cle] = ligne
    stock["lignes"] = list(lignes.values())
    os.makedirs(DOSSIER_IMPORTS, exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(stock, f, ensure_ascii=False)
    if not any(hote(s["base"]) == hote(base) for s in sites_configures().values()):
        enregistrer_site({"nom": data["site"], "base": base, "plateforme": "import-navigateur"})
    return {"site": data["site"], "produits": len({l["code"] for l in stock["lignes"]}),
            "lignes": len(stock["lignes"]), "nouvelles": len(stock["lignes"]) - avant}


class ImportNavigateur(Connecteur):
    """Site lu à partir des relevés faits dans le navigateur (extension « Relevé de prix ») :
    aucune requête n'est envoyée au site par l'application."""
    plateforme = "import-navigateur"

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return False  # jamais proposé par l'analyse automatique

    def _stock(self):
        try:
            with open(_fichier_import(self.base), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"produits": {}, "lignes": []}

    def _lignes(self, code, ville=None, nuits=None):
        return [l for l in self._stock()["lignes"] if l["code"] == code
                and (ville is None or l["ville"] == ville) and (nuits is None or l["nuits"] == nuits)]

    def rechercher(self, nom, limite=8):
        stock = self._stock()
        codes = {l["code"] for l in stock["lignes"]}
        res = []
        for code in codes:
            p = stock["produits"].get(code, {})
            nom_p = p.get("nom") or f"Produit {code}"
            res.append(Candidat(self.nom, nom_p, p.get("url", self.base), code,
                                f"voyagiste {p['voyagiste']}" if p.get("voyagiste") else "", score_nom(nom, nom_p)))
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def villes(self, candidat):
        libelles = {v["code"]: v["libelle"] for v in self._stock()["produits"].get(candidat.code, {}).get("villes", [])}
        return {l["ville"]: libelles.get(l["ville"], l["ville"]) for l in self._lignes(candidat.code)}

    def nuits(self, candidat, ville):
        return sorted({l["nuits"] for l in self._lignes(candidat.code, ville)})

    def mois(self, candidat, ville, nuits):
        return sorted({(int(l["date"][5:7]), int(l["date"][:4])) for l in self._lignes(candidat.code, ville, nuits)},
                      key=lambda m: (m[1], m[0]))

    def _du_mois(self, candidat, ville, nuits, mm, aaaa):
        return [l for l in self._lignes(candidat.code, ville, nuits) if l["date"].startswith(f"{aaaa}-{mm:02d}")]

    def prix(self, candidat, ville, nuits, mm, aaaa):
        return [{"date": l["date"], "prix_eur": l["prix"], "meilleur_prix": l["meilleur_prix"], "compagnie": "",
                 "retour": l["retour"], "formule": l["formule"], "vol_direct": l["vol_direct"],
                 "voyagiste": l["voyagiste"]} for l in sorted(self._du_mois(candidat, ville, nuits, mm, aaaa),
                                                               key=lambda l: l["date"])]

    def age_secondes(self, candidat, ville, nuits, mm, aaaa):
        """Âge des prix importés (le plus ancien relevé du mois) : ils ne sont jamais relevés à nouveau ici."""
        from datetime import datetime
        dates = [l["capture_le"] for l in self._du_mois(candidat, ville, nuits, mm, aaaa) if l.get("capture_le")]
        if not dates:
            return 0
        plus_ancien = datetime.fromisoformat(min(dates).replace("Z", "+00:00"))
        return max(0, time.time() - plus_ancien.timestamp())

    def adresse_prix(self, candidat):
        return self.base


# ---------------------------------------------------------------- Registre des sites

PLATEFORMES = {c.plateforme: c for c in (PlateformeCalendrierJSON, PlateformeGrilleJSF, PlateformeCatalogueFram,
                                          PlateformePickAlbatros, ImportNavigateur)}

SITES_INTEGRES = [
    {"nom": "Ovoyages", "base": "https://www.ovoyages.com", "plateforme": "calendrier-json"},
    {"nom": "Exotismes", "base": "https://www.exotismes.fr", "plateforme": "grille-jsf"},
    {"nom": "Fram", "base": "https://www.fram.fr", "plateforme": "catalogue-fram"},
    {"nom": "Pick Albatros", "base": "https://www.pickalbatros.com", "plateforme": "hotel-seul-albatros"},
]


def sites_ajoutes():
    try:
        with open(FICHIER_SITES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def enregistrer_site(conf):
    sites = [s for s in sites_ajoutes() if s["nom"] != conf["nom"]] + [conf]
    with open(FICHIER_SITES, "w", encoding="utf-8") as f:
        json.dump(sites, f, ensure_ascii=False, indent=2)


def retirer_site(nom):
    with open(FICHIER_SITES, "w", encoding="utf-8") as f:
        json.dump([s for s in sites_ajoutes() if s["nom"] != nom], f, ensure_ascii=False, indent=2)


FICHIER_A_ETUDIER = os.path.join(DOSSIER, "sites_a_etudier.json")


def sites_a_etudier():
    """Sites refusés pour « plateforme non reconnue », en attente d'un nouveau connecteur."""
    try:
        with open(FICHIER_A_ETUDIER, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _ecrire_a_etudier(liste):
    with open(FICHIER_A_ETUDIER, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)


def noter_a_etudier(entree):
    """Ajoute ou met à jour un site (une entrée par site), en gardant la note déjà saisie."""
    liste = sites_a_etudier()
    ancienne = next((s for s in liste if hote(s["base"]) == hote(entree["base"])), {})
    entree["note"] = ancienne.get("note", "")
    _ecrire_a_etudier([s for s in liste if hote(s["base"]) != hote(entree["base"])] + [entree])


def annoter_a_etudier(base, note):
    liste = sites_a_etudier()
    for s in liste:
        if hote(s["base"]) == hote(base):
            s["note"] = note
    _ecrire_a_etudier(liste)


def retirer_a_etudier(base):
    _ecrire_a_etudier([s for s in sites_a_etudier() if hote(s["base"]) != hote(base)])


def sites_configures():
    """{nom: configuration} : sites intégrés puis sites ajoutés depuis l'application."""
    return {s["nom"]: s for s in SITES_INTEGRES + sites_ajoutes()}


def type_site(conf):
    """« forfait » (vol + hôtel) ou « hotel_seul »."""
    return PLATEFORMES[conf["plateforme"]].type_site


def creer_connecteur(conf):
    return PLATEFORMES[conf["plateforme"]](conf["nom"], conf["base"])


def candidat_depuis_dict(d):
    return Candidat(**d)


def candidat_en_dict(c):
    return asdict(c)


def mois_suivants(n=12):
    t = time.localtime()
    mm, aaaa = t.tm_mon, t.tm_year
    for _ in range(n):
        yield mm, aaaa
        mm, aaaa = (1, aaaa + 1) if mm == 12 else (mm + 1, aaaa)


def jours_du_mois(mm, aaaa):
    return calendar.monthrange(aaaa, mm)[1]
