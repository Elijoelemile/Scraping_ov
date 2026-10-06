# Comparateur de prix de séjours

Application qui relève les prix d'un même **Produit** (hôtel, formule séjour) sur plusieurs sites de voyagistes et les compare date par date : tableau mensuel, synthèse, graphique, et export **Word, PDF et Excel**. Elle fonctionne en local (Windows) ou en ligne (Streamlit Community Cloud).

Elle reprend le calendrier de prix des sites : chaque ligne du calendrier (du lundi au dimanche) est une **semaine** du mois. Par défaut, on suit les tarifs du **mercredi** et du **samedi**.

---

## Sites pris en charge

| Site | Mode | Informations en plus du prix |
|---|---|---|
| **Ovoyages** | Relevé automatique | « Meilleur prix » |
| **Exotismes** | Relevé automatique | « Meilleur prix », compagnie aérienne |
| **Fram** | Relevé automatique | « Meilleur prix », date de retour, formule |
| **Promoséjours** | Relevé fait dans votre navigateur (extension), puis importé | « Meilleur prix du mois », date de retour, formule, vol direct, voyagiste |
| Autres sites | Ajoutés depuis l'application s'ils remplissent les critères | Selon la plateforme |

Voir [Ajouter un site](#ajouter-un-site) et [Sites protégés](#sites-protégés--relevé-dans-le-navigateur-promoséjours).

> Un même Produit peut être vendu par plusieurs sites sous des noms et des références différents. Par exemple, Ovoyages semble revendre des séjours Fram : pour certaines dates, les prix sont identiques.

---

## Fonctionnalités

- **Recherche par nom de Produit** sur un ou plusieurs sites. Quand un site nomme le Produit autrement, la recherche est relancée avec le nom complet trouvé sur un autre site.
- **Paramètres du relevé** : ville de départ, nombre de nuits et période. Seules les valeurs communes à tous les sites choisis sont proposées.
- **Filtres instantanés** : jours de la semaine, semaines 1 à 6, compagnie aérienne, budget maximum, « Meilleur prix » uniquement, écart minimum entre sites.
- **Comparer deux sites** au choix parmi 3 ou plus : l'« Écart » se calcule entre ces deux sites ; « Moins cher » reste le moins cher de la ligne.
- **Résultats** :
  - comparatif par mois, avec le prix le plus bas surligné en vert ;
  - synthèse par site ;
  - graphique d'évolution des prix ;
  - meilleures dates.
- **Exports** : **Word** et **PDF**, au même format que les rapports, et **Excel**, avec 3 onglets (Synthèse, Comparatif filtrable, Détail).
- **Ajout de sites** depuis l'application, après une analyse automatique.
- **Sites protégés** : import des prix relevés dans votre navigateur avec l'extension « Relevé de prix ».
- **Robustesse** : un site en panne est écarté avec un message, et les autres sont comparés normalement. Une coupure pendant le relevé est retentée automatiquement ; si elle persiste, le mois est marqué « Non relevé ».
- **Cache des relevés** : un relevé récent est réutilisé pour ne pas interroger les sites inutilement. La durée est réglable.
- **Date et heure du relevé** affichées partout, à l'heure de Paris.

---

## Prérequis

- **Python 3.12** (ou plus récent), sous Windows en local.
- Pour l'export **PDF** : **Microsoft Word** sous Windows, ou **LibreOffice** (installé automatiquement en ligne). Les exports Word et Excel n'en ont pas besoin.
- Une connexion Internet : les prix sont lus en direct sur les sites.
- Pour Promoséjours : **Chrome** ou **Edge**, avec l'extension du projet.

## Installation

```bash
git clone https://github.com/Elijoelemile/Scraping_ov.git
cd Scraping_ov
pip install -r requirements.txt
```

`requirements.txt` fixe les versions testées. Pour l'export PDF hors Windows, LibreOffice est nécessaire (voir `packages.txt`).

## Lancement

Au choix :

1. **Double-clic** sur `lancer_app.bat`.
2. Bouton **▷ Exécuter** de VS Code, avec `app.py` ouvert.
3. Dans un terminal :

   ```bash
   python -m streamlit run app.py
   ```

L'application s'ouvre dans le navigateur à l'adresse **http://localhost:8501**. Pour l'arrêter, fermez la fenêtre du terminal ou appuyez sur `Ctrl+C`.

> Lancer `python app.py` fonctionne aussi : le script démarre Streamlit tout seul.

## Déploiement sur Streamlit Community Cloud

Sur [share.streamlit.io](https://share.streamlit.io), **Create app** :

| Champ | Valeur |
|---|---|
| Repository | `Elijoelemile/Scraping_ov` |
| Branch | `main` |
| Main file path | `app.py` |
| Paramètres avancés → Python version | `3.12` |

Le serveur installe automatiquement `requirements.txt` (modules Python) et `packages.txt` (LibreOffice et polices, pour l'export PDF). Chaque envoi sur la branche `main` met l'application à jour. Sinon, utilisez **Reboot app**.

À savoir en ligne :
- **Accès** : l'application est publique par défaut. Limitez-la aux personnes invitées dans **Share** pour éviter que n'importe qui lance des relevés.
- **Données éphémères** : le cache, les sites ajoutés depuis le panneau, les sites à étudier et les relevés importés sont **effacés à chaque redémarrage** du serveur. Seuls les sites intégrés au code (Ovoyages, Exotismes, Fram) restent toujours disponibles.
- **Adresse des requêtes** : les relevés partent des serveurs de Streamlit. Un site qui bloque les adresses de serveurs peut être refusé en ligne alors qu'il fonctionne en local.

---

## Utilisation

1. **Produit** : tapez un nom, par exemple `coral level` ou `bavaro suites`.
2. **Site** : choisissez un ou plusieurs sites, puis cliquez sur **Rechercher**.
3. **Vérifiez le Produit trouvé** sur chaque site. Le lien « Voir la page du Produit » ouvre la page exacte sur le site.
   - Un même nom peut correspondre à plusieurs références, avec des prix différents. Par exemple, Ovoyages a 3 références pour le Bávaro Suites (108505, 235436, 235539), et Fram aussi (79150, 49891, 77807).
   - Si le bon Produit n'apparaît pas, utilisez « Le bon Produit n'est pas là ? » pour chercher un autre nom sur ce site.
4. **Paramètres** : choisissez la ville de départ, le nombre de nuits et la période.
   - **Réutiliser un relevé de moins de** : 1 h par défaut. Choisissez « Jamais » pour relever tous les prix à neuf, par exemple juste avant d'envoyer un comparatif.
   - Cliquez sur **Lancer le relevé**. Comptez environ 4 secondes par mois et par site (Fram : un seul appel pour tous les mois).
5. **Filtres** : ajustez jours, semaines, budget… L'affichage se met à jour sans nouvel appel aux sites.
   - **Comparer deux sites** (à partir de 3 sites) : le sélecteur propose tous les sites, on peut en cocher **2 au maximum**. Une fois 2 sites cochés, la colonne **« Écart »** donne la différence de prix **entre ces deux sites** (son titre l'indique, par exemple « Écart (Ovoyages / Fram) »). La colonne **« Moins cher »**, le surlignage vert et la Synthèse restent, eux, **le moins cher de la ligne parmi tous les sites**. Sans choix, ou avec un seul site coché, l'Écart est le prix le plus haut − le prix le plus bas. Avec 2 sites seulement, il n'y a pas de sélecteur.
6. **Export** : onglet « Export Word / PDF / Excel ». Les fichiers reprennent exactement les données et les filtres affichés.

### Lecture des tableaux

| Affichage | Signification |
|---|---|
| Case verte | Prix le plus bas de la date, parmi les sites |
| **Aucun départ** | Le site ne propose pas de départ ce jour-là |
| **Un seul site** (colonne Écart) | Un seul site a un prix ce jour-là : pas d'écart calculable |
| **Égalité** (colonne Moins cher) | Plusieurs sites affichent le même prix le plus bas |
| **« Site » seul** (colonne Moins cher) | Seul ce site propose un départ ce jour-là |
| **Non précisée** (Excel) | Le site ne fournit pas cette information (compagnie, formule…) |
| **Non relevé** (en orange) | Le site n'a pas pu fournir ce mois, même après 3 tentatives (coupure, panne). Ce n'est **pas** une absence de départ |
| **Incomplet** (Moins cher, Écart) | Un des sites n'a pas pu être relevé ce mois-là : la comparaison n'est pas possible |
| **Site injoignable** / **« Site » est écarté** | Le site ne répond pas pour le moment. Les autres sites sont comparés normalement |

Les prix sont **par personne, sur la base d'une chambre double**. Ils changent souvent, parfois en moins d'une heure. La date et l'heure du relevé (heure de Paris) figurent au-dessus des résultats et dans chaque export. Quand des prix réutilisés ou importés sont plus anciens, c'est la date des plus anciens qui est affichée.

### Contenu de l'export Excel

| Onglet | Contenu |
|---|---|
| **Synthèse** | Contexte du relevé (Produit, sites, départ, nuits, période, filtres, date), puis par site : dates avec prix, nombre de fois moins cher, prix moyen, prix le plus bas et sa date (formules Excel) |
| **Comparatif** | Une ligne par date : mois, semaine, jour, date, nombre de nuits, prix de chaque site, moins cher et écart (formules ; l'écart suit les deux sites cochés le cas échéant), avec le prix le plus bas surligné en vert. Filtres Excel actifs |
| **Détail** | Une ligne par site et par date : nombre de nuits, prix, « Meilleur prix », compagnie aérienne et, selon les sites, date de retour, formule, vol direct et voyagiste |

---

## Ajouter un site

Dans le panneau de gauche, section **« Ajouter un site »**, saisissez l'adresse du site puis cliquez sur **Analyser le site**. Le lien d'un Produit du site est facultatif, mais il aide l'analyse.

Le site n'est **ajouté que s'il remplit tous les critères** :

1. accessible sans blocage anti-robot (Cloudflare, DataDome, captcha, erreur 403…) ;
2. plateforme de prix reconnue par l'application ;
3. recherche d'un Produit par son nom ;
4. villes de départ et durées lisibles ;
5. prix date par date, **en euros** (par exemple, exotismes.ch est refusé car ses prix sont en francs suisses).

Sinon, l'analyse affiche la raison du refus. Le fichier `robots.txt` du site est aussi vérifié, et signalé par un avertissement ⚠️ s'il interdit les pages de prix, sans bloquer l'ajout.

### Sites à étudier

Un site **accessible** mais dont la plateforme est inconnue est refusé, puis placé dans la liste **« Sites à étudier »** du panneau de gauche, avec des repères techniques. On peut y ajouter une note, par exemple « prioritaire », ou le retirer. Pour le rendre compatible, il faut étudier son fonctionnement et écrire sa plateforme (voir ci-dessous). C'est ainsi que Fram a été ajouté.

### Plateformes

Un connecteur correspond à une **plateforme de réservation**, c'est-à-dire au logiciel qui calcule et affiche les prix derrière le site. Tous les sites qui utilisent la même plateforme se lisent de la même façon.

| Plateforme | Sites | Fonctionnement |
|---|---|---|
| `calendrier-json` | Ovoyages | Produits listés dans le plan du site ; prix via `/pricetable/…`, un appel par mois |
| `grille-jsf` | Exotismes (.fr, .be) | Recherche par mots-clés du site ; prix via la grille `/reservation/grilles.jsf`, un appel par mois |
| `catalogue-fram` | Fram | Produits listés dans le plan du site ; options dans la page Produit ; prix via `/api/ajax/catalogueProduit/calendriers`, tous les mois en un appel |
| `import-navigateur` | Promoséjours | Prix importés depuis l'extension de navigateur, sans aucune requête de l'application |

**Écrire une plateforme** : créer dans `connecteurs.py` une classe qui hérite de `Connecteur`, implémenter `reconnaitre()`, `rechercher()`, `villes()`, `nuits()`, `mois()`, `prix()` et `adresse_prix()`, puis l'ajouter au dictionnaire `PLATEFORMES`. Pour qu'un site soit **toujours** présent, y compris en ligne, l'ajouter aussi à `SITES_INTEGRES`.

---

## Sites protégés : relevé dans le navigateur (Promoséjours)

Promoséjours bloque les robots (Cloudflare). L'application ne l'interroge donc pas elle-même. C'est **vous** qui consultez le site normalement, et l'extension **« Relevé de prix »** garde les prix que le site vous affiche. Elle ne clique sur rien et n'envoie aucune requête.

### Installer l'extension (une seule fois, Chrome ou Edge)

1. Ouvrez `chrome://extensions` (ou `edge://extensions`).
2. Activez le **Mode développeur** (en haut à droite pour Chrome, à gauche pour Edge).
3. Cliquez sur **Charger l'extension non empaquetée** et choisissez le dossier `extension_navigateur` du projet.
4. Épinglez l'icône « Relevé de prix » dans la barre du navigateur.

### Faire un relevé

1. Ouvrez la page d'un Produit sur Promoséjours, de préférence **sans être connecté** à votre compte.
2. Dans le calendrier, choisissez la ville et la durée, puis **parcourez les mois** voulus. L'icône de l'extension compte les mois relevés.
3. Cliquez sur l'icône puis sur **« Exporter pour l'application »**. Un fichier `releve_promosejours_….json` est enregistré dans vos Téléchargements.
4. Dans l'application, panneau de gauche, section **« Importer un relevé navigateur »**, déposez ce fichier.

Promoséjours apparaît alors dans le champ **Site** et se compare aux autres sites. Plusieurs imports se cumulent : pour une même date, le relevé le plus récent l'emporte. Les prix ne se mettent pas à jour seuls : pour des prix récents, refaites un relevé. Leur âge est affiché au-dessus des résultats. Le bouton **Effacer** de l'extension vide ses relevés une fois exportés.

---

## Scripts en ligne de commande

Les rapports Word d'origine, qui donnent les tarifs du mercredi et du samedi par semaine pour Ovoyages et Exotismes, peuvent aussi être produits sans l'application :

```bash
# Ovoyages : Coral Level, départ Paris, d'octobre 2026 à août 2027
python rapport_ovoyages.py --ville PAR --nuits 5 --debut 10-2026 --fin 08-2027

# Exotismes : Iberostar Selection Bávaro Suites
python rapport_exotismes.py --nuits 7 --ville LYS

# Comparatif Ovoyages / Exotismes sur le Coral Level
python comparer_sites.py --nuits 5 --debut 10-2026 --fin 08-2027
```

Options communes : `--ville`, `--nuits`, `--debut` / `--fin` (format `MM-AAAA`), `--pause` (secondes entre deux requêtes, 3 par défaut) et `--sortie` (fichier Word). `--help` affiche l'aide de chaque script. Pour Fram, Promoséjours et la comparaison de plus de deux sites, utilisez l'application.

---

## Dépannage

| Problème | Solution |
|---|---|
| Le terminal affiche `streamlit run yourscript.py` | Lancez l'application avec `lancer_app.bat`, le bouton ▷ de VS Code ou `python -m streamlit run app.py` |
| `lancer_app.bat` « n'est pas reconnu » dans PowerShell | Tapez `.\lancer_app.bat`, ou double-cliquez dessus dans l'Explorateur Windows |
| L'adresse affichée est `localhost:8502` | Une autre copie de l'application tourne déjà : fermez-la, ou utilisez l'adresse affichée |
| Les prix diffèrent de ceux du site | Vérifiez le **Produit** (référence), le **nombre de nuits** et la **ville** : le site affiche souvent d'autres réglages par défaut. Les prix changent aussi très vite : relancez le relevé avec « Jamais » |
| « Site injoignable » ou « est écarté » | Le site ne répond pas pour le moment (panne ou surcharge). Réessayez plus tard ; les autres sites restent comparés |
| « … : non relevé » au-dessus des résultats | Le site a coupé la connexion pour ce mois malgré 3 tentatives. Relancez le relevé : les mois déjà relevés sont réutilisés, seul le mois manquant est redemandé |
| « Conversion impossible » pour le PDF | Microsoft Word (Windows) ou LibreOffice est nécessaire. Les exports Word et Excel restent disponibles |
| Un site ajouté a disparu en ligne | Les sites ajoutés depuis le panneau s'effacent au redémarrage sur Streamlit Cloud : il faut l'intégrer au code (`SITES_INTEGRES`) |

---

## Structure du projet

| Fichier | Rôle |
|---|---|
| `app.py` | Application Streamlit (interface, filtres, résultats, ajout de sites, import navigateur) |
| `connecteurs.py` | Plateformes (Ovoyages, Exotismes, Fram, import navigateur), registre des sites, cache |
| `analyse_site.py` | Analyse d'un site avant son ajout |
| `export_word.py` | Export Word et conversion PDF (Microsoft Word ou LibreOffice) |
| `export_excel.py` | Export Excel (Synthèse, Comparatif, Détail) |
| `scrape_ovoyages.py`, `scrape_exotismes.py` | Lecture des prix d'Ovoyages et d'Exotismes (scripts d'origine) |
| `rapport_ovoyages.py`, `rapport_exotismes.py`, `comparer_sites.py` | Rapports Word en ligne de commande |
| `extension_navigateur/` | Extension Chrome/Edge « Relevé de prix » (sites protégés) |
| `lancer_app.bat` | Lancement de l'application par double-clic |
| `requirements.txt`, `packages.txt` | Modules Python et paquets système (déploiement en ligne) |

### Fichiers créés à l'usage (non versionnés)

| Fichier ou dossier | Contenu |
|---|---|
| `cache/` | Relevés de prix, catalogues et fiches Produit mis en cache |
| `sites_ajoutes.json` | Sites ajoutés depuis l'application |
| `sites_a_etudier.json` | Sites accessibles en attente d'une plateforme |
| `imports/` | Relevés importés depuis l'extension de navigateur |
| `*.docx`, `*.pdf`, `*.xlsx` | Rapports générés |

---

## Bonnes pratiques

- **Discrétion** : l'application attend environ 3 secondes entre deux requêtes vers un même site et réutilise les relevés récents. Évitez de réduire la pause ou de lancer des relevés en boucle.
- **Sites protégés** : l'application ne contourne aucune protection anti-robot. Un site bloqué n'est pas ajouté automatiquement ; ses prix peuvent être relevés à la main avec l'extension de navigateur.
- **`robots.txt`** : certains sites demandent aux robots de ne pas lire leurs pages de prix (c'est le cas d'Ovoyages et d'Exotismes, pas de Fram). L'application le signale, et c'est à l'utilisateur de décider de l'usage qu'il en fait.
- **Fiabilité** : si un site modifie son fonctionnement, son connecteur peut cesser de marcher. Le relevé affiche alors un avertissement pour le site concerné, sans bloquer les autres.
