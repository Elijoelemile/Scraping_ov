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


# ---------------------------------------------------------------- Registre des sites

PLATEFORMES = {c.plateforme: c for c in (PlateformeCalendrierJSON, PlateformeGrilleJSF)}

SITES_INTEGRES = [
    {"nom": "Ovoyages", "base": "https://www.ovoyages.com", "plateforme": "calendrier-json"},
    {"nom": "Exotismes", "base": "https://www.exotismes.fr", "plateforme": "grille-jsf"},
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
