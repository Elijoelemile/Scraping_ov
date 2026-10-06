"""Analyse d'un site avant son ajout à l'application.

Un site n'est ajouté que s'il remplit TOUS les critères :
  1. accessible sans vérification anti-robot ;
  2. plateforme de prix reconnue ;
  3. recherche d'un Produit par son nom ;
  4. villes de départ et durées lisibles ;
  5. prix date par date, en euros.
Le robots.txt est vérifié et signalé, sans bloquer l'ajout (décision de l'utilisateur).
"""
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

import connecteurs as C

OK, KO, ALERTE = "ok", "ko", "alerte"

MARQUEURS_BLOCAGE = {
    "Cloudflare": ["just a moment...", "challenges.cloudflare.com", "cf-chl", "cf_chl"],
    "DataDome": ["captcha-delivery.com", "datadome"],
    "PerimeterX": ["px-captcha", "perimeterx"],
    "Imperva / Incapsula": ["_incapsula_", "incapsula incident"],
    "Akamai": ["akamai bot manager", "/_sec/cp_challenge"],
    "Captcha": ["g-recaptcha", "hcaptcha.com", "please verify you are a human"],
}


def detecter_blocage(reponse):
    """Nom de la protection anti-robot détectée, ou None."""
    texte = reponse.text[:20000].lower()
    if reponse.headers.get("cf-mitigated"):
        return "Cloudflare"
    for nom, marques in MARQUEURS_BLOCAGE.items():
        # un captcha de formulaire (newsletter…) sur une page normale n'est pas un blocage
        if nom == "Captcha" and reponse.status_code == 200 and len(texte) > 15000:
            continue
        if any(m in texte for m in marques):
            return nom
    if reponse.status_code in (401, 403, 429, 503):
        return f"refus du serveur (HTTP {reponse.status_code})"
    return None


def indices_techniques(accueil, produit_html):
    """Quelques repères pour identifier le moteur du site plus tard (générateur, domaines des scripts…)."""
    html = accueil + produit_html
    gen = re.findall(r'<meta[^>]+name="generator"[^>]+content="([^"]+)"', html, re.I)
    scripts = sorted({urlparse(s).netloc or "(même site)"
                      for s in re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)})
    appels = sorted(set(re.findall(r"['\"](/(?:api|ajax|services?|booking|price|prix|tarif)[a-zA-Z0-9_/.-]*)", html)))
    return {"generateur": gen[:3], "domaines_scripts": scripts[:20], "appels_detectes": appels[:20],
            "page_produit_lue": bool(produit_html)}


def normaliser_adresse(adresse):
    adresse = adresse.strip()
    if not re.match(r"https?://", adresse):
        adresse = "https://" + adresse
    u = urlparse(adresse)
    return f"{u.scheme}://{u.netloc}"


def nom_par_defaut(base):
    h = C.hote(base)
    return h[:1].upper() + h[1:]


def analyser(adresse, lien_produit="", nom="", progression=lambda texte: None):
    """Renvoie {"ajoutable": bool, "etapes": [(critère, statut, détail)], "conf": dict|None, "raison": str}."""
    etapes = []

    def etape(critere, statut, detail):
        etapes.append((critere, statut, detail))
        progression(f"{critere} : {detail}")

    def resultat(raison=""):
        return {"ajoutable": not raison, "etapes": etapes, "raison": raison,
                "conf": {"nom": nom or nom_par_defaut(base), "base": base, "plateforme": plateforme}
                if not raison else None}

    base, plateforme = normaliser_adresse(adresse), None
    deja = {C.hote(s["base"]): n for n, s in C.sites_configures().items()}
    if C.hote(base) in deja:
        etape("Site", KO, f"déjà dans la liste sous le nom « {deja[C.hote(base)]} »")
        return resultat("site déjà présent dans la liste")
    if nom and nom in C.sites_configures():
        etape("Nom", KO, f"le nom « {nom} » est déjà utilisé")
        return resultat("nom déjà utilisé")

    # 1. Accès
    session = requests.Session()
    session.headers["User-Agent"] = C.UA
    try:
        r = session.get(base + "/", timeout=30)
    except requests.RequestException as e:
        etape("Accès", KO, f"site injoignable ({e.__class__.__name__})")
        return resultat("site injoignable")
    base = normaliser_adresse(r.url)  # suit les redirections (http -> https, ajout de www…)
    blocage = detecter_blocage(r)
    if blocage:
        etape("Accès", KO, f"robot bloqué : {blocage}")
        return resultat(f"Robot bloqué ({blocage})")
    accueil = r.text
    produit_html, lien_test = "", lien_produit.strip()
    if lien_test:
        try:
            rp = session.get(lien_test, timeout=30)
            blocage = detecter_blocage(rp)
            if blocage:
                etape("Accès", KO, f"page Produit bloquée : {blocage}")
                return resultat(f"Robot bloqué ({blocage})")
            produit_html = rp.text
        except requests.RequestException:
            etape("Accès", ALERTE, "le lien du Produit ne répond pas ; analyse sans lui")
            lien_test = ""
    if not produit_html:
        # Sans lien fourni : on ouvre une page Produit trouvée sur l'accueil pour reconnaître la plateforme
        liens = re.findall(r'href="((?:https?://[^"]+)?/voyages/\d+-[^"/]+/\d+-[^"/]+\.jsf)"', accueil) + \
                re.findall(r'href="((?:https?://[^"]+)?/[a-z0-9-]+/[^"?#]*-\d{4,})"', accueil) +                 re.findall(r'href="((?:https?://[^"]+)?/[a-z0-9-]+-\d{4,}\.html)', accueil)
        if liens:
            lien = liens[0] if liens[0].startswith("http") else base + liens[0]
            try:
                rp = session.get(lien, timeout=30)
                if not detecter_blocage(rp):
                    produit_html, lien_test = rp.text, lien
            except requests.RequestException:
                pass
    etape("Accès", OK, "pages accessibles, aucune vérification anti-robot")

    # 2. Plateforme
    for cle, cls in C.PLATEFORMES.items():
        if cls.reconnaitre(accueil, produit_html):
            plateforme = cle
            break
    if plateforme is None:
        etape("Plateforme de prix", KO, "non reconnue par l'application")
        # Site accessible mais moteur inconnu : noté dans « Sites à étudier » pour un futur connecteur
        C.noter_a_etudier({"base": base, "lien_produit": lien_test, "date": time.strftime("%Y-%m-%d %H:%M"),
                           "indices": indices_techniques(accueil, produit_html)})
        etape("Sites à étudier", ALERTE, "site enregistré pour l'apprentissage de sa plateforme")
        return resultat("plateforme de prix non reconnue (un connecteur dédié est nécessaire)")
    etape("Plateforme de prix", OK, f"reconnue : {plateforme}")
    conn = C.PLATEFORMES[plateforme](nom or nom_par_defaut(base), base)

    # 3. Recherche par nom, et choix d'un Produit de test
    # Le nom cherché est celui du Produit de test (tiré de son adresse), sinon des mots courants
    requetes = []
    if lien_test:
        slug = urlparse(lien_test).path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".jsf").removesuffix(".html")
        slug = re.sub(r"^\d+-|-\d{4,}$", "", slug)
        requetes.append(" ".join(m for m in slug.split("-") if not m.isdigit()))
    requetes += ["resort", "beach", "club", "palace"]
    try:
        for requete in requetes:
            candidats = conn.rechercher(requete, limite=6)
            if candidats:
                break
    except Exception as e:
        etape("Recherche par nom", KO, f"impossible ({e})")
        return resultat("pas de recherche d'un Produit par son nom")
    if not candidats:
        etape("Recherche par nom", KO, "aucun Produit trouvé")
        return resultat("pas de recherche d'un Produit par son nom")
    etape("Recherche par nom", OK, f"{len(candidats)} Produit(s) trouvé(s) pour « {requete} »")

    # 4 et 5. Villes, durées, prix date par date — sur le premier Produit de test qui a des prix
    raison_echec = "aucun Produit de test n'a de prix date par date"
    for cand in candidats[:3]:
        try:
            villes = conn.villes(cand)
            if not villes:
                raison_echec = "villes de départ illisibles"
                continue
            ville = "PAR" if "PAR" in villes else next(iter(villes))
            nuits = conn.nuits(cand, ville)
            if not nuits:
                raison_echec = "durées illisibles"
                continue
            mois = conn.mois(cand, ville, nuits[0])
            prix = []
            for mm, aaaa in mois[:2]:
                prix = conn.prix(cand, ville, nuits[0], mm, aaaa)
                if prix:
                    break
            if not prix:
                raison_echec = "pas de prix date par date"
                continue
        except Exception as e:
            raison_echec = str(e)
            continue
        etape("Villes et durées", OK, f"{len(villes)} ville(s), durées {nuits[0]} à {nuits[-1]} nuits "
                                      f"(Produit de test : {cand.nom})")
        etape("Prix date par date", OK, f"{len(prix)} prix en euros sur {mm:02d}/{aaaa} "
                                        f"(ex. {prix[0]['date'][8:]}/{prix[0]['date'][5:7]} : {prix[0]['prix_eur']} €)")
        break
    else:
        etape("Villes, durées et prix", KO, raison_echec)
        return resultat(raison_echec)

    # robots.txt : signalé, non bloquant
    try:
        rob = RobotFileParser()
        rob.parse(session.get(base + "/robots.txt", timeout=30).text.splitlines())
        if rob.can_fetch("*", conn.adresse_prix(cand)):
            etape("robots.txt", OK, "les pages de prix sont autorisées aux robots")
        else:
            etape("robots.txt", ALERTE, "le site demande aux robots de ne pas lire ses pages de prix")
    except requests.RequestException:
        etape("robots.txt", ALERTE, "fichier robots.txt illisible")

    return resultat()
