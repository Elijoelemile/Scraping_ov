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

# « club » : les sites l'ajoutent ou l'omettent selon les cas (« Club Jumbo Palm Galleria » / « Jumbo Palm Galleria »)
MOTS_VIDES = {"hotel", "hôtel", "the", "at", "by", "de", "la", "le", "les", "et", "a", "en", "sejour", "voyage", "club"}


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return [m for m in re.findall(r"[a-z0-9]+", texte) if m not in MOTS_VIDES]


def mot_correspond(m, x):
    """Un mot cherché correspond à un mot du nom : identique, début du mot, ou une faute de frappe sur un mot
    d'au moins 5 lettres (« galeria » / « galleria »)."""
    return x == m or x.startswith(m) or (len(m) >= 5 and difflib.SequenceMatcher(None, m, x).ratio() >= 0.85)


SCORE_SANS_MOT_RARE = 0.7  # plafond quand le mot le plus rare manque : sous le seuil « Produit trouvé » de l'application
_vocabulaire = {"signature": None, "mots": {}, "memo": {}, "vu": 0.0}


def _frequences():
    """{mot: nombre de noms de Produits qui le contiennent}, sur tous les catalogues de sites en cache."""
    if time.time() - _vocabulaire["vu"] < 5:  # appelé pour chaque Produit d'un catalogue : on ne relit pas le dossier
        return _vocabulaire
    _vocabulaire["vu"] = time.time()
    fichiers = sorted(f for f in os.listdir(DOSSIER_CACHE) if f.startswith("catalogue") and f.endswith(".json"))
    signature = tuple((f, os.path.getmtime(os.path.join(DOSSIER_CACHE, f))) for f in fichiers)
    if signature != _vocabulaire["signature"]:
        mots = {}
        for f in fichiers:
            try:
                with open(os.path.join(DOSSIER_CACHE, f), encoding="utf-8") as fh:
                    produits = json.load(fh)
            except (OSError, ValueError):
                continue
            for p in produits if isinstance(produits, list) else []:
                for m in set(normaliser(p.get("nom", ""))):
                    mots[m] = mots.get(m, 0) + 1
        _vocabulaire.update(signature=signature, mots=mots, memo={})
    return _vocabulaire


def frequence_mot(m):
    """Nombre de noms de Produits (tous sites) où l'on retrouve ce mot, fautes de frappe comprises."""
    voc = _frequences()
    if m not in voc["memo"]:
        voc["memo"][m] = sum(n for x, n in voc["mots"].items() if mot_correspond(m, x))
    return voc["memo"][m]


def mot_rare(mots):
    """Le mot le plus rare de la liste : celui qui désigne le mieux l'hôtel (« Kantaoui » dans
    « Iberostar Selection Kantaoui Bay »). À égalité, le plus long. None si aucun catalogue n'est chargé."""
    if not mots or not _frequences()["mots"]:
        return None
    return min(mots, key=lambda m: (frequence_mot(m), -len(m)))


def score_nom(requete, nom):
    """1.0 = tous les mots de la requête sont dans le nom ; départage par ressemblance globale.
    Les étoiles (« 3* », « 5 ») ne comptent pas : les sites les écrivent rarement dans le nom.
    Si le mot le plus rare de la requête manque, le score est plafonné : le Produit n'est plus retenu
    d'office (il reste proposé parmi les Produits approchants)."""
    q, n = normaliser(requete), normaliser(nom)
    q = [m for m in q if not (len(m) == 1 and m.isdigit())] or q
    if not q:
        return 0.0
    trouves = sum(1 for m in q if any(mot_correspond(m, x) for x in n))
    ressemblance = difflib.SequenceMatcher(None, " ".join(q), " ".join(n)).ratio()
    score = trouves / len(q) + 0.2 * ressemblance
    if len(q) > 1 and score > SCORE_SANS_MOT_RARE:
        rare = mot_rare(q)
        if rare and not any(mot_correspond(rare, x) for x in n):
            score = SCORE_SANS_MOT_RARE
    return score


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

    FORMULE_PREFEREE = "AI"  # All Inclusive par défaut quand il existe : la formule habituelle des comparatifs

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self.session.headers["Accept"] = "application/json, text/plain, */*"
        self._calendriers = {}
        self.formules_choisies = {}  # code du Produit -> code de formule choisi dans l'application

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "__INITIAL_STATE__" in produit_html and "departureMonths" in produit_html and "disponibilities" in produit_html

    def catalogue(self):
        """Produits du plan du site : Produits classiques (…-76450.html) et forfaits dynamiques (…-dp100152.html),
        dont le vol et l'hôtel sont assemblés à la demande (code « dp… »)."""
        cle = f"catalogue_v2_{hote(self.base)}"
        cat = cache_lire(cle, 24 * 3600)
        if cat is None:
            index = self.get("/sitemap.xml").text
            sous = re.findall(r"<loc>([^<]+)</loc>", index)
            urls = []
            for s in [u for u in sous if "produit" in u.lower()] or sous:
                urls += re.findall(r"<loc>([^<]+)</loc>", self.get(s, timeout=(10, 90)).text)
            cat = []
            for url in urls:
                m = re.search(r"/([^/]+)-(dp)?(\d+)\.html$", url)
                if m:
                    slug = re.sub(r"^(hotel|club|circuit|sejour|autotour|croisiere)-", "", m.group(1))
                    cat.append({"nom": slug_en_nom(slug), "url": url, "code": (m.group(2) or "") + m.group(3)})
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], "", score_nom(nom, p["nom"])) for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _fiche(self, candidat):
        """Options du Produit : offre de référence, formules, villes et durées.
        Produit classique : lues dans les données de sa page. Forfait dynamique (« dp… ») : la page ne donne que
        l'offre (« Réf : … ») ; villes, formules et durées viennent de /api/ajax/catalogueProduit/calendriersCritere."""
        cle = f"fiche_v2_{hote(self.base)}_{candidat.code}"
        fiche = cache_lire(cle, 24 * 3600)
        if fiche is None:
            html = self.get(candidat.url).text
            debut = html.find("window.__INITIAL_STATE__")
            etat = html[debut:debut + html[debut:].find("</script>")] if debut >= 0 else ""
            if candidat.code.startswith("dp"):
                offre = re.search(r'offerId\s*:\s*"(\d+)"', etat) or re.search(r"Réf\s*:\s*(\d{8,})", html)
                if not offre:
                    raise RuntimeError("offre introuvable sur la page du Produit")
                crit = self.get("/api/ajax/catalogueProduit/calendriersCritere", params={"idPackage": offre.group(1)},
                                headers={"Referer": candidat.url}).json()
                if crit.get("status") != "S_OK":
                    raise RuntimeError("aucun calendrier de prix pour ce Produit")
                fiche = {"dynamique": True, "idPackage": offre.group(1),
                         "villes": [{"libelle": v["libelle"], "code": v["codeVilleDepart"]}
                                    for v in crit.get("villeDeparts") or []],
                         "formules": [{"code": p["code"], "libelle": p["libelle"]} for p in crit.get("pensions") or []],
                         "formule_fram": next((p["code"] for p in crit.get("pensions") or [] if p.get("selectionne")), ""),
                         # « durees » ne couvre que le groupe présélectionné (1 semaine) : « dureesJourNuit » les a toutes
                         "nuits": sorted({d["dureeNuit"]["value"]
                                          for d in (crit.get("dureesJourNuit") or []) + (crit.get("durees") or [])})}
            else:
                paquet = re.search(r'idPackage: "(\d+)"', etat)
                if not paquet:
                    raise RuntimeError("aucun calendrier de prix sur la page du Produit")
                bloc_villes = re.search(r"departureCities: \[(.*?)\],\s*pensionTypes", etat, re.S)
                villes = re.findall(r'label: "([^"]+)",\s*value: "([^"]+)"', bloc_villes.group(1)) if bloc_villes else []
                pensions = re.search(r"pensionTypes: \[(.*?)\]", etat, re.S)
                formules = re.findall(r'label: "([^"]+)",\s*value: "([^"]+)"', pensions.group(1)) if pensions else []
                fiche = {"dynamique": False, "idPackage": paquet.group(1),
                         "villes": [{"libelle": l.strip(), "id": re.sub(r"\D", "", v)} for l, v in villes],
                         "formules": [{"code": c, "libelle": l} for l, c in formules],
                         "formule_fram": formules[0][1] if formules else "",
                         "nuits": sorted({int(n) for n in re.findall(r"label: '(\d+) nuit'", etat)})}
            cache_ecrire(cle, fiche)
        return fiche

    def formules(self, candidat):
        """Formules du Produit (All Inclusive, Demi Pension…) : [{"code", "libelle"}]."""
        return self._fiche(candidat)["formules"]

    def formule_par_defaut(self, candidat):
        """All Inclusive s'il existe, sinon la formule présélectionnée par Fram."""
        fiche = self._fiche(candidat)
        codes = [f["code"] for f in fiche["formules"]]
        return self.FORMULE_PREFEREE if self.FORMULE_PREFEREE in codes else (fiche["formule_fram"] or (codes or [""])[0])

    def formule(self, candidat):
        """Formule utilisée pour ce Produit : celle choisie dans l'application, sinon celle par défaut."""
        return self.formules_choisies.get(candidat.code) or self.formule_par_defaut(candidat)

    def libelle_formule(self, candidat):
        code = self.formule(candidat)
        return next((f["libelle"] for f in self.formules(candidat) if f["code"] == code), code)

    def villes(self, candidat):
        res = {}
        for v in self._fiche(candidat)["villes"]:
            code = v["code"] if "code" in v else _code_ville(v["libelle"], v["id"])
            res.setdefault(code, v["libelle"])
        return res

    def nuits(self, candidat, ville):
        return self._fiche(candidat)["nuits"]

    def _calendrier(self, candidat, ville, nuits):
        formule = self.formule(candidat)
        cle = (candidat.code, ville, nuits, formule)
        memo = self._calendriers.get(cle)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        fiche = self._fiche(candidat)
        params = {"idPackage": fiche["idPackage"], "intervalleDureeNuit.min": nuits,
                  "intervalleDureeNuit.max": nuits, "codePension": formule}
        if fiche["dynamique"]:
            if ville not in {v["code"] for v in fiche["villes"]}:
                return {}
            params["codeVilleDepart"] = ville
        else:
            ident = next((v["id"] for v in fiche["villes"] if _code_ville(v["libelle"], v["id"]) == ville), None)
            if ident is None:
                return {}
            params.update(idVilleDepart=ident, idHebergement=candidat.code)
        r = self.get("/api/ajax/catalogueProduit/calendriers", headers={"Referer": candidat.url}, params=params)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "S_OK":
            data = {}
        self._calendriers[cle] = (time.time(), data)
        return data

    def _departs(self, candidat, ville, nuits):
        libelle = self.libelle_formule(candidat)
        for mois in self._calendrier(candidat, ville, nuits).get("moisAnnees") or []:
            for jour in mois.get("dateDeparts") or []:
                for c in jour.get("calendriers") or []:
                    prix = c.get("prix") or {}
                    if (c.get("disponible") and prix.get("prix") and prix.get("typePrix") == "PERSONNE"
                            and (c.get("duree") or {}).get("nuit", {}).get("value") == nuits):
                        yield {"date": jour["date"], "prix_eur": int(round(prix["prix"])),
                               "meilleur_prix": bool(c.get("meilleurPrix")), "compagnie": "",
                               "retour": ((c.get("periodeVoyage") or {}).get("fin") or {}).get("value", ""),
                               "formule": libelle, "voyagiste": "Fram"}

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


# ---------------------------------------------------------------- Plateforme Mondial Tourisme

class PlateformeMondialTourisme(Connecteur):
    """Mondial Tourisme (site Drupal Commerce) : Produits listés dans le plan du site (/fr/sejours/<nom>) ;
    villes dans la page Produit ; durées et calendrier complet d'une ville et d'une durée (« 9/7 » = 9 jours,
    7 nuits) renvoyés par la page Produit elle-même (POST « update-availabilities », comme quand on change
    la ville ou la durée). Prix TTC par personne, base chambre double."""
    plateforme = "mondial-tourisme"
    DUREE_MEMOIRE = 120  # s : un calendrier lu sert pour tous les mois d'un même relevé

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self._pages, self._reponses = {}, {}

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "commerce-cart-alter-add-to-cart-alt" in produit_html and "hidden_product_availabilities" in produit_html

    def catalogue(self):
        cle = f"catalogue_{hote(self.base)}"
        cat = cache_lire(cle, 7 * 24 * 3600)
        if cat is None:
            urls = re.findall(r"<loc>([^<]+/fr/sejours/[^<]+)</loc>", self.get("/sitemap.xml", timeout=(10, 60)).text)
            cat = []
            for url in dict.fromkeys(urls):
                slug = url.rstrip("/").rsplit("/", 1)[-1]
                nom = re.sub(r"-(\d)(-?pro|-\d)?$", r" \1*", slug)  # « …-5-3 », « …-3pro » : étoiles et doublons
                cat.append({"nom": slug_en_nom(nom), "url": url, "code": slug})
            if not cat:
                raise RuntimeError("catalogue des Produits introuvable")
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], "", score_nom(nom, p["nom"])) for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _page(self, candidat):
        """Villes, adresse du formulaire et une date de départ valable, lues dans la page Produit."""
        if candidat.code not in self._pages:
            html = self.get(candidat.url).text

            def options(nom):
                bloc = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % nom, html, re.S)
                return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)<', bloc.group(1)) if bloc else []

            def choisi(nom):
                bloc = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % nom, html, re.S)
                m = re.search(r'<option value="([^"]*)" selected', bloc.group(1)) if bloc else None
                return m.group(1) if m else ""

            ville, jour, duree = choisi("lieu_de_depart"), choisi("date_depart"), choisi("duree_depart")
            if not (ville and jour and "/" in duree and "commerce-cart-alter-add-to-cart-alt" in html):
                raise RuntimeError("aucun calendrier de prix : le Produit n'est pas en vente en ce moment")
            # Le formulaire répond à l'adresse complète du Produit (…/<ville>/<date>/<nuits>) ; sans elle : erreur 500
            self._pages[candidat.code] = {"action": f"/fr/sejours/{candidat.code}/{ville}/{jour}/{int(duree.split('/')[1]):02d}",
                                          "villes": dict(options("lieu_de_depart")), "date": jour, "duree": duree}
        return self._pages[candidat.code]

    def _demander(self, candidat, element, ville, duree=""):
        cle = (candidat.code, element, ville, duree)
        memo = self._reponses.get(cle)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        page = self._page(candidat)
        self.attendre()
        r = self.session.post(self.base + page["action"], timeout=DELAI,
                              headers={"X-Requested-With": "XMLHttpRequest", "Referer": candidat.url},
                              data={"element": element, "request-type": "update-availabilities",
                                    "begin_date": page["date"], "city_code": ville,
                                    "duration": duree or page["duree"],  # durée vide : erreur 500 du site
                                    "adults": 2, "children": 0, "infants": 0, "flex": "false"})
        r.raise_for_status()
        data = json.loads(r.text)
        self._reponses[cle] = (time.time(), data)
        return data

    def _durees(self, candidat, ville):
        """Formules de durée proposées depuis cette ville : ["9/7", "8/7", …] (jours/nuits)."""
        data = self._demander(candidat, "edit-lieu-de-depart", ville)
        return re.findall(r'<option value="(\d+/\d+)"', data.get("durations") or "")

    def villes(self, candidat):
        return {code: libelle.strip() for code, libelle in self._page(candidat)["villes"].items()}

    def nuits(self, candidat, ville):
        return sorted({int(d.split("/")[1]) for d in self._durees(candidat, ville)})

    def _calendrier(self, candidat, ville, nuits):
        """{date: prix} pour toutes les formules de `nuits` nuits (« 8 jours / 7 nuits » et « 9 jours / 7 nuits »…) :
        le prix le plus bas par date."""
        import html as H
        res = {}
        for duree in [d for d in self._durees(candidat, ville) if int(d.split("/")[1]) == nuits]:
            bloc = self._demander(candidat, "edit-duree-nuits", ville, duree).get("hidden_product_availabilities") or ""
            texte = re.search(r"<textarea[^>]*>(.*?)</textarea>", bloc, re.S)
            dispo = json.loads(H.unescape(texte.group(1)).strip() or "null") if texte else None
            for jour, info in (dispo or {}).items() if isinstance(dispo, dict) else []:
                prix = int(float(info.get("price") or 0))
                if prix > 0 and (jour not in res or prix < res[jour]):
                    res[jour] = prix
        return res

    def mois(self, candidat, ville, nuits):
        return sorted({(int(j[5:7]), int(j[:4])) for j in self._calendrier(candidat, ville, nuits)},
                      key=lambda m: (m[1], m[0]))

    def prix(self, candidat, ville, nuits, mm, aaaa):
        from datetime import date, timedelta
        res = []
        for jour, prix in sorted(self._calendrier(candidat, ville, nuits).items()):
            if jour.startswith(f"{aaaa}-{mm:02d}"):
                depart = date.fromisoformat(jour)
                res.append({"date": jour, "prix_eur": prix, "meilleur_prix": False, "compagnie": "",
                            "retour": (depart + timedelta(days=nuits)).isoformat(), "voyagiste": self.nom})
        return res

    def adresse_prix(self, candidat):
        return candidat.url


# ---------------------------------------------------------------- Plateforme Speedresa (Top of Travel)

class PlateformeSpeedresa(Connecteur):
    """Sites d'agence sur le moteur Speedresa (Top of Travel) : Produits listés par /rest/search/result/load.json
    (un seul appel) ; villes, durées (« duration-8-7 » = 8 jours, 7 nuits) et calendrier d'une ville et d'une durée
    via /rest/product/package/price.json (un appel, tous les mois). Prix TTC par personne (« TTC/heb »)."""
    plateforme = "speedresa"
    DUREE_MEMOIRE = 120  # s : un calendrier lu sert pour tous les mois d'un même relevé
    THEME_VOLS = "filter-theme-6002"  # « Vols secs » : billets d'avion seuls, pas des séjours

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self.session.headers["X-Requested-With"] = "XMLHttpRequest"
        self._reponses = {}

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "speedresa.com" in accueil_html + produit_html and "/search/result/package/source-" in accueil_html

    def _source(self):
        accueil = self.get("/").text
        m = re.search(r"/search/result/package/source-(\d+)/", accueil)
        if not m:
            raise RuntimeError("moteur de recherche introuvable")
        return m.group(1)

    def catalogue(self):
        cle = f"catalogue_{hote(self.base)}"
        cat = cache_lire(cle, 24 * 3600)
        if cat is None:
            import html as H
            source, cat, page, pages = self._source(), [], 1, 1
            while page <= pages:
                adresse = f"{self.base}/search/result/package/source-{source}/engine-2/"
                self.attendre()
                r = self.session.post(f"{self.base}/rest/search/result/load.json", timeout=(10, 60),
                                      headers={"x-page-url": adresse, "Referer": adresse},
                                      data={"source_id": source, "per_page": 100, "type": "package", "page": page,
                                            "template": "default", "parameter[engine]": 2})
                data = r.json()
                pages = int((data.get("pagination") or {}).get("pages") or 1)
                for produit in (data.get("products") or {}).values():
                    rendu = produit.get("render", "")
                    titre = re.search(r'class="product-title">([^<]+)<', rendu)
                    lien = re.search(r'href="([^"?]+/produit/package/(\d+)-\d+/[^"?]+)"', rendu)
                    if titre and lien and self.THEME_VOLS not in rendu:
                        lieu = re.search(r"</h2>\s*<p>(.*?)</p>", rendu, re.S)
                        pays = re.findall(r"<strong>(?:<b>\s*>\s*</b>)?([^<]+)</strong>", lieu.group(1) if lieu else "")
                        cat.append({"nom": H.unescape(titre.group(1)).strip(), "url": lien.group(1),
                                    "code": lien.group(2), "source": source,
                                    "destination": " > ".join(H.unescape(p).strip() for p in pays[:2])})
                page += 1
            if not cat:
                raise RuntimeError("catalogue des Produits introuvable")
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], p["destination"], score_nom(nom, p["nom"]))
               for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _vue(self, candidat, ville="", jours="", nuits=""):
        """Calendrier HTML d'une ville et d'une durée. Sans précision : la ville et la durée de l'adresse du Produit
        (…/from-PAR/date-…/nights-7) ; le site refuse une demande sans ville."""
        if not ville:
            ville = (re.search(r"/from-([A-Z]{3})/", candidat.url) or [None, ""])[1]
            nuits = (re.search(r"/nights-(\d+)", candidat.url) or [None, ""])[1]
        cle = (candidat.code, ville, jours, nuits)
        memo = self._reponses.get(cle)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        source = re.search(r"/produit/package/\d+-(\d+)/", candidat.url).group(1)
        self.attendre()
        r = self.session.post(f"{self.base}/rest/product/package/price.json", timeout=DELAI,
                              headers={"Referer": candidat.url},
                              data={"source_id": source, "affilie": "", "product_id": candidat.code,
                                    "product_type": "package", "template": "default", "from_parameter": ville,
                                    "date_parameter": "", "night_parameter": nuits, "day_parameter": jours,
                                    "private": 0, "all_days": 0})
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise RuntimeError(data.get("message") or "calendrier de prix indisponible")
        self._reponses[cle] = (time.time(), data.get("view") or "")
        return self._reponses[cle][1]

    def villes(self, candidat):
        bloc = re.search(r'<select id="prices-from"[^>]*>(.*?)</select>', self._vue(candidat), re.S)
        return {v: l.strip() for v, l in re.findall(r'<option[^>]*value="([A-Z]{3})"[^>]*>([^<]*)<', bloc.group(1))} \
            if bloc else {}

    def _durees(self, candidat, ville):
        """[(jours, nuits)] proposées depuis cette ville."""
        bloc = re.search(r'<select id="multi_duration"[^>]*>(.*?)</select>', self._vue(candidat), re.S)
        return [(int(j), int(n)) for j, n, villes in
                re.findall(r'value="duration-(\d+)-(\d+)"[^>]*data-froms="([^"]*)"', bloc.group(1) if bloc else "")
                if ville in villes.split(",")]

    def nuits(self, candidat, ville):
        return sorted({n for _, n in self._durees(candidat, ville)})

    def _calendrier(self, candidat, ville, nuits):
        """{date: prix} pour toutes les formules de `nuits` nuits ; prix le plus bas par date."""
        res = {}
        for jours, n in self._durees(candidat, ville):
            if n != nuits:
                continue
            soupe = BeautifulSoup(self._vue(candidat, ville, jours, nuits), "html.parser")
            for bloc in soupe.find_all(class_="price-period"):
                mois = re.search(r"price-period-filter-(\d{4})-m(\d+)-01", " ".join(bloc.get("class", [])))
                if not mois or "price-period-header" in bloc.get("class", []):
                    continue
                for case in bloc.find_all(class_=f"price-filter-{ville}"):
                    if f"duration-{jours}-{nuits}" not in case.get("class", []):
                        continue
                    jour = case.find_previous_sibling(class_="date")
                    montant = case.find(class_="amount")
                    statut = case.find(class_="day-status")
                    numero = re.search(r"\d+", jour.get_text()) if jour else None
                    if not (numero and montant and statut and "OK" in statut.get("class", [])):
                        continue
                    date = f"{mois.group(1)}-{int(mois.group(2)):02d}-{int(numero.group()):02d}"
                    prix = int(re.sub(r"\D", "", montant.get_text()) or 0)
                    if prix and (date not in res or prix < res[date]):
                        res[date] = prix
        return res

    def mois(self, candidat, ville, nuits):
        return sorted({(int(j[5:7]), int(j[:4])) for j in self._calendrier(candidat, ville, nuits)},
                      key=lambda m: (m[1], m[0]))

    def prix(self, candidat, ville, nuits, mm, aaaa):
        from datetime import date, timedelta
        return [{"date": j, "prix_eur": p, "meilleur_prix": False, "compagnie": "",
                 "retour": (date.fromisoformat(j) + timedelta(days=nuits)).isoformat(), "voyagiste": self.nom}
                for j, p in sorted(self._calendrier(candidat, ville, nuits).items()) if j.startswith(f"{aaaa}-{mm:02d}")]

    def adresse_prix(self, candidat):
        return f"{self.base}/rest/product/package/price.json"


# ---------------------------------------------------------------- Plateforme TUI

class PlateformeTUI(Connecteur):
    """TUI France : Produits listés dans le plan du site (…/nom-du-produit_CODE/) ; paramètres du calendrier
    (« priceTableParams ») lus dans la page Produit ; prix d'un mois via /ProductSheet/Home/RefreshPriceTable/
    (un appel par mois, ville et durée), par personne, en euros, base 2 adultes."""
    plateforme = "tui"
    DUREE_MEMOIRE = 120  # s : une réponse sert pour les villes, durées, mois et prix d'un même relevé

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self._params, self._tableaux = {}, {}

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "/ProductSheet/Home/RefreshPriceTable" in accueil_html + produit_html or \
            "var priceTableParams" in produit_html

    def catalogue(self):
        cle = f"catalogue_{hote(self.base)}"
        cat = cache_lire(cle, 7 * 24 * 3600)
        if cat is None:
            index = self.get("/fstrz/sm/sitemap_index.xml").text
            sources = [u for u in re.findall(r"<loc>([^<]+)</loc>", index) if u.endswith("sitemap-p.xml")]
            cat = []
            for s in sources:
                for url in re.findall(r"<loc>([^<]+)</loc>", self.get(s, timeout=(10, 60)).text):
                    m = re.search(r"/([^/]+)_([A-Z0-9-]+)/?$", url)
                    if m:
                        cat.append({"nom": slug_en_nom(m.group(1)), "url": url, "code": m.group(2)})
            if not cat:
                raise RuntimeError("catalogue des Produits introuvable")
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], "", score_nom(nom, p["nom"])) for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _parametres(self, candidat):
        if candidat.code not in self._params:
            html = self.get(candidat.url).text
            i = html.find("var priceTableParams")
            brut = html[html.find("=", i) + 1:].lstrip() if i >= 0 else "null"
            if brut.startswith("null"):
                raise RuntimeError("aucun calendrier de prix : le Produit n'est pas en vente en ce moment")
            self._params[candidat.code] = json.JSONDecoder().raw_decode(brut)[0]
        return self._params[candidat.code]

    def _tableau(self, candidat, ville=None, nuits=None, mm=None, aaaa=None):
        cle = (candidat.code, ville, nuits, mm, aaaa)
        memo = self._tableaux.get(cle)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        p = dict(self._parametres(candidat), refreshSource="Init")
        if ville:
            p["departureCity"] = ville
        if nuits:
            p["duration"] = nuits
        if mm:
            p.update(departureDate=f"{aaaa}-{mm:02d}-01T00:00:00", refreshSource="Month")
        self.attendre()
        r = self.session.post(f"{self.base}/ProductSheet/Home/RefreshPriceTable/", data=json.dumps({"priceParams": p}),
                              headers={"Content-Type": "application/json; charset=utf-8",
                                       "X-Requested-With": "XMLHttpRequest", "Referer": candidat.url}, timeout=DELAI)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("calendrier de prix indisponible")
        self._tableaux[cle] = (time.time(), data)
        return data

    def villes(self, candidat):
        data = self._tableau(candidat)
        villes = {v: l.strip() for v, l in re.findall(r'<option[^>]*value="([A-Z]{3})"[^>]*>([^<]*)<',
                                                       data.get("cities") or "")}
        defaut = self._parametres(candidat).get("departureCity")
        if defaut and defaut not in villes:  # un seul départ : le site n'affiche pas de liste
            villes[defaut] = {v: k for k, v in IATA_VILLES.items()}.get(defaut, defaut).title()
        return villes

    def nuits(self, candidat, ville):
        data = self._tableau(candidat, ville)
        return sorted({int(n) for n in re.findall(r'<option[^>]*value="(\d+)"', data.get("durations") or "")})

    def mois(self, candidat, ville, nuits):
        data = self._tableau(candidat, ville, nuits)
        return [(int(m), int(a)) for _, m, a in
                re.findall(r'data-departuremonth="(\d+)/(\d+)/(\d+)"', data.get("months") or "")]

    def prix(self, candidat, ville, nuits, mm, aaaa):
        from datetime import date, timedelta
        table = self._tableau(candidat, ville, nuits, mm, aaaa).get("priceTable") or ""
        if f'id="currentMonth" value="01/{mm:02d}/{aaaa}"' not in table:
            return []  # le site renvoie un autre mois quand celui-ci n'a aucun départ
        res = []
        for td in BeautifulSoup(table, "html.parser").find_all("td", attrs={"data-tunnelurl": True}):
            jour, prix = td.get("data-message", ""), td.get("data-price", "")
            if not (re.fullmatch(r"\d{8}", jour) and prix.isdigit() and int(prix) > 0):
                continue
            depart = date(int(jour[4:]), int(jour[2:4]), int(jour[:2]))
            if (depart.month, depart.year) != (mm, aaaa) or f"departurecitycode={ville}" not in td["data-tunnelurl"]:
                continue
            res.append({"date": depart.isoformat(), "prix_eur": int(prix),
                        "meilleur_prix": "meilleur prix" in td.get_text(" ").lower(), "compagnie": "",
                        "retour": (depart + timedelta(days=nuits)).isoformat(), "voyagiste": self.nom})
        return sorted(res, key=lambda r: r["date"])

    def adresse_prix(self, candidat):
        return f"{self.base}/ProductSheet/Home/RefreshPriceTable/"


# ---------------------------------------------------------------- Plateforme Orchestra (Voyamar)

class PlateformeOrchestra(Connecteur):
    """Sites construits sur la plateforme Orchestra (Voyamar, Héliades) : Produits listés par la page de résultats
    /ajax/serp (48 par page) ; villes, durées et prix de tous les mois en un appel à /ajax/bookingEngine/<n°>.
    Prix par personne, en euros, comme affichés sur la fiche du Produit."""
    # Première page de résultats : Voyamar exige le canal de vente, Héliades le refuse
    PREMIERES_PAGES = ("/ajax/serp?s_c.canal=B2C&page=1&byPage={n}", "/ajax/serp?page=1&byPage={n}")
    plateforme = "orchestra"
    PAR_PAGE = 48  # le maximum accepté par le site
    DUREE_MEMOIRE = 120  # s : un calendrier lu sert pour tous les mois d'un même relevé
    PENSION = re.compile(r"pension|compris|inclus|d[ée]jeuner|logement seul|all inclusive", re.I)

    def __init__(self, nom, base):
        super().__init__(nom, base)
        self.session.headers.update({"X-Requested-With": "XMLHttpRequest"})
        self._calendriers = {}
        self.formules_choisies = {}  # interface commune : un Produit Orchestra n'a qu'une formule

    @staticmethod
    def reconnaitre(accueil_html, produit_html):
        return "orchestra-platform.com" in accueil_html + produit_html and "/ajax/bookingEngine/" in produit_html

    def catalogue(self):
        cle = f"catalogue_{hote(self.base)}"
        cat = cache_lire(cle, 7 * 24 * 3600)
        if cat is None:
            cat, vus, page, modele = [], set(), 1, None
            while page <= 200:
                # Héliades : avec son cookie de session, la liste s'arrête à la 3e page ; sans, elle est complète
                self.session.cookies.clear()
                if modele is None:  # page 1 : on garde la forme d'adresse acceptée par le site
                    for modele in self.PREMIERES_PAGES:
                        html = self.get(modele.format(n=self.PAR_PAGE), timeout=(10, 60)).text
                        if "<article" in html:
                            break
                else:  # pages suivantes : même adresse, numéro de page suivant
                    html = self.get(modele.replace("page=1", f"page={page}").format(n=self.PAR_PAGE),
                                    timeout=(10, 60)).text
                cartes = html.split('<article class="card card-serp')[1:]
                for carte in cartes:
                    lien = re.search(r'data-link="(/[^"]+-(\d+)\.html)"', carte)
                    titre = re.search(r'<div class="card-title">\s*<div[^>]*>([^<]+)<', carte)
                    if not (lien and titre) or lien.group(2) in vus:
                        continue
                    vus.add(lien.group(2))
                    dest = re.search(r'<div class="card-destination">\s*([^<]+?)\s*<', carte)
                    cat.append({"nom": BeautifulSoup(titre.group(1), "html.parser").get_text().strip(),
                                "url": self.base + lien.group(1), "code": lien.group(2),
                                "destination": " ".join(dest.group(1).split()) if dest else ""})
                if len(cartes) < self.PAR_PAGE:
                    break
                page += 1
            if not cat:
                raise RuntimeError("catalogue des Produits introuvable")
            cache_ecrire(cle, cat)
        return cat

    def rechercher(self, nom, limite=8):
        res = [Candidat(self.nom, p["nom"], p["url"], p["code"], p["destination"], score_nom(nom, p["nom"]))
               for p in self.catalogue()]
        return sorted([c for c in res if c.score >= 0.5], key=lambda c: -c.score)[:limite]

    def _calendrier(self, candidat):
        memo = self._calendriers.get(candidat.code)
        if memo and time.time() - memo[0] < self.DUREE_MEMOIRE:
            return memo[1]
        r = self.get(f"/ajax/bookingEngine/{candidat.code}", headers={"Referer": candidat.url}, timeout=(10, 60))
        r.raise_for_status()
        data = r.json()
        self._calendriers[candidat.code] = (time.time(), data)
        return data

    def formules(self, candidat):
        """La formule du Produit (« Demi pension »…), lue dans ses prestations incluses."""
        services = ((self._calendrier(candidat).get("terms") or {}).get("services") or {}).get(candidat.code) or []
        return [{"code": s["label"], "libelle": s["label"]} for s in services if self.PENSION.search(s.get("label", ""))]

    def formule_par_defaut(self, candidat):
        return (self.formules(candidat) or [{"code": ""}])[0]["code"]

    def formule(self, candidat):
        return self.formule_par_defaut(candidat)

    def libelle_formule(self, candidat):
        return self.formule(candidat) or "Non précisée"

    def villes(self, candidat):
        # « XXX / Sans Transport » : l'hôtel sans le vol, qui ne se compare pas aux forfaits vol + hôtel
        return {v["code"]: v["label"] for v in (self._calendrier(candidat).get("search") or {}).get("departureCity") or []
                if v["code"] != "XXX"}

    def nuits(self, candidat, ville):
        mois = (self._calendrier(candidat).get("availabilities") or {}).get(ville) or {}
        return sorted({d["nightNb"] for jours in mois.values() for durees in jours.values() for d in durees.values()})

    def mois(self, candidat, ville, nuits):
        mois = (self._calendrier(candidat).get("availabilities") or {}).get(ville) or {}
        res = {(int(k[:2]), int(k[3:])) for k, jours in mois.items()
               if any(d["nightNb"] == nuits for durees in jours.values() for d in durees.values())}
        return sorted(res, key=lambda m: (m[1], m[0]))

    def prix(self, candidat, ville, nuits, mm, aaaa):
        from datetime import date, timedelta
        jours = ((self._calendrier(candidat).get("availabilities") or {}).get(ville) or {}).get(f"{mm:02d}-{aaaa}") or {}
        formule = self.libelle_formule(candidat)
        res = []
        for jour, durees in jours.items():
            offres = [d for d in durees.values()
                      if d.get("nightNb") == nuits and d.get("status") == "Available" and d.get("price")]
            if not offres:
                continue
            o = min(offres, key=lambda d: d["price"])  # « 5 jours / 3 nuits » et « 4 jours / 3 nuits » : la moins chère
            depart = date(aaaa, mm, int(jour))
            res.append({"date": depart.isoformat(), "prix_eur": int(round(o["price"])), "meilleur_prix": False,
                        "compagnie": "", "retour": (depart + timedelta(days=nuits)).isoformat(), "formule": formule,
                        "vol_direct": "Oui" if o.get("directFlightPrice") == o["price"] else "Non",
                        "voyagiste": self.nom})
        return sorted(res, key=lambda r: r["date"])

    def adresse_prix(self, candidat):
        return f"{self.base}/ajax/bookingEngine/{candidat.code}"


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
                                          PlateformePickAlbatros, PlateformeOrchestra, PlateformeTUI, PlateformeMondialTourisme, PlateformeSpeedresa,
                                          ImportNavigateur)}

SITES_INTEGRES = [
    {"nom": "Ovoyages", "base": "https://www.ovoyages.com", "plateforme": "calendrier-json"},
    {"nom": "Exotismes", "base": "https://www.exotismes.fr", "plateforme": "grille-jsf"},
    {"nom": "Fram", "base": "https://www.fram.fr", "plateforme": "catalogue-fram"},
    {"nom": "Pick Albatros", "base": "https://www.pickalbatros.com", "plateforme": "hotel-seul-albatros"},
    {"nom": "Voyamar", "base": "https://www.voyamar-vacances.com", "plateforme": "orchestra"},
    {"nom": "Thalasso N°1", "base": "https://www.thalassonumero1.com", "plateforme": "calendrier-json"},
    {"nom": "Héliades", "base": "https://www.heliades.fr", "plateforme": "orchestra"},
    {"nom": "TUI", "base": "https://www.tui.fr", "plateforme": "tui"},
    {"nom": "Mondial Tourisme", "base": "https://www.mondialtourisme.fr", "plateforme": "mondial-tourisme"},
    {"nom": "Top of Travel", "base": "https://www.topoftravel.fr", "plateforme": "speedresa"},
]
SANS_TRANSPORT = "XXX"  # code de ville des séjours vendus sans le vol (« Sans Transport »)


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
