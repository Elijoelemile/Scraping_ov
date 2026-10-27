"""Relit le catalogue de chaque site intégré et l'enregistre dans le dossier catalogues/.

L'application part de ces catalogues quand son cache est vide (premier lancement, serveur Streamlit Cloud
redémarré) : la première recherche est alors immédiate, au lieu d'attendre la lecture de chaque site
(environ 11 minutes pour Ovoyages). Les sites sont lus en même temps, chacun avec sa pause entre deux requêtes.

    python mettre_a_jour_catalogues.py            # tous les sites
    python mettre_a_jour_catalogues.py Ovoyages   # un ou plusieurs sites

Ensuite, envoyer le dossier catalogues/ sur GitHub pour que la version en ligne en profite.
"""
import glob
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import connecteurs as C


def mettre_a_jour(conf):
    conn = C.creer_connecteur(conf)
    if not hasattr(conn, "catalogue"):
        return conf["nom"], None, "pas de catalogue (recherche directe sur le site)"
    debut = time.time()
    nom_fichier = f"catalogue*{C.hote(conf['base'])}.json"
    for f in glob.glob(os.path.join(C.DOSSIER_CACHE, nom_fichier)):  # on force une lecture complète
        os.remove(f)
    # Le catalogue fourni est mis de côté pendant la lecture (sinon l'application le reprendrait tel quel),
    # puis remis en place si la lecture échoue
    anciens = glob.glob(os.path.join(C.DOSSIER_CATALOGUES, nom_fichier))
    for f in anciens:
        os.replace(f, f + ".ancien")
    try:
        produits = conn.catalogue()
        # Un site en difficulté peut renvoyer une liste incomplète : on garde l'ancienne si la nouvelle est bien
        # plus petite (moins de 60 %)
        avant = max((len(C.json.load(open(f + ".ancien", encoding="utf-8"))) for f in anciens), default=0)
        if len(produits) < 0.6 * avant:
            raise RuntimeError(f"liste incomplète ({len(produits)} Produits contre {avant} avant) : ancienne gardée")
    except Exception:
        for f in anciens:
            os.replace(f + ".ancien", f)
        raise
    for f in anciens:
        os.remove(f + ".ancien")
    for f in glob.glob(os.path.join(C.DOSSIER_CACHE, nom_fichier)):
        shutil.copyfile(f, os.path.join(C.DOSSIER_CATALOGUES, os.path.basename(f)))
    return conf["nom"], len(produits), f"{time.time() - debut:.0f} s"


def main(noms):
    os.makedirs(C.DOSSIER_CATALOGUES, exist_ok=True)
    sites = [s for s in C.SITES_INTEGRES if not noms or s["nom"] in noms]
    with ThreadPoolExecutor(max_workers=len(sites) or 1) as pool:
        taches = {pool.submit(mettre_a_jour, s): s["nom"] for s in sites}
        for t in as_completed(taches):
            try:
                nom, nombre, detail = t.result()
                print(f"{nom:18} {nombre if nombre is not None else '-':>6} Produits  ({detail})", flush=True)
            except Exception as e:  # un site en panne n'empêche pas les autres
                print(f"{taches[t]:18} ÉCHEC : {e.__class__.__name__} {e}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
