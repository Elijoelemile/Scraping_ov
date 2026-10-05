# Comparateur de prix de séjours

Application locale qui relève les prix d'un même **Produit** (hôtel, formule séjour) sur plusieurs sites de voyagistes et les compare date par date : tableau mensuel, synthèse, graphique, et export **Word, PDF et Excel**.

Elle reprend le calendrier de prix des sites : chaque ligne du calendrier (du lundi au dimanche) est une **semaine** du mois. Par défaut, on suit les tarifs du **mercredi** et du **samedi**.

Sites pris en charge : **Ovoyages** et **Exotismes**, plus tout site ajouté depuis l'application qui remplit les critères de compatibilité (voir [Ajouter un site](#ajouter-un-site)).

---

## Fonctionnalités

- **Recherche par nom de Produit** sur un ou plusieurs sites. Quand un site nomme le Produit autrement, la recherche est relancée avec le nom complet trouvé sur un autre site.
- **Paramètres du relevé** : ville de départ, nombre de nuits et période. Seules les valeurs communes à tous les sites choisis sont proposées.
- **Filtres instantanés** : jours de la semaine, semaines 1 à 6, compagnie aérienne, budget maximum, « Meilleur prix » uniquement, écart minimum entre sites.
- **Résultats** :
  - comparatif par mois, avec le prix le plus bas surligné en vert ;
  - synthèse par site ;
  - graphique d'évolution des prix ;
  - meilleures dates.
- **Exports** :
  - **Word** et **PDF**, au même format que les rapports ;
  - **Excel**, avec 3 onglets : Synthèse, Comparatif (filtrable), Détail.
- **Ajout de sites** depuis l'application, après une analyse automatique.
- **Cache des relevés** : par défaut, un relevé de moins d'une heure est réutilisé pour ne pas interroger les sites inutilement. La durée est réglable.

---

## Prérequis

- **Windows** avec **Python 3.12** (ou plus récent).
- **Microsoft Word**, uniquement pour l'export **PDF**. Les exports Word et Excel n'en ont pas besoin.
- Une connexion Internet : les prix sont lus en direct sur les sites.

## Installation

```bash
git clone https://github.com/Elijoelemile/Scraping_ov.git
cd Scraping_ov
pip install streamlit pandas altair requests beautifulsoup4 python-docx openpyxl
```

Versions testées : streamlit 1.42, pandas 2.2, altair 5.5, requests 2.32, beautifulsoup4 4.13, openpyxl 3.1.

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

---

## Utilisation

1. **Produit** : tapez un nom, par exemple `coral level` ou `bavaro suites`.
2. **Site** : choisissez un ou plusieurs sites, puis cliquez sur **Rechercher**.
3. **Vérifiez le Produit trouvé** sur chaque site. Le lien « Voir la page du Produit » ouvre la page exacte sur le site.
   - Un même nom peut correspondre à plusieurs références. Sur Ovoyages, par exemple, le Bávaro Suites existe sous 3 références avec des prix différents.
   - Si le bon Produit n'apparaît pas, utilisez « Le bon Produit n'est pas là ? » pour chercher un autre nom sur ce site.
4. **Paramètres** : choisissez la ville de départ, le nombre de nuits et la période, puis cliquez sur **Lancer le relevé**. Comptez environ 4 secondes par mois et par site.
5. **Filtres** : ajustez jours, semaines, budget… L'affichage se met à jour sans nouvel appel aux sites.
6. **Export** : onglet « Export Word / PDF / Excel ». Les fichiers reprennent exactement les données et les filtres affichés.

### Lecture des tableaux

| Affichage | Signification |
|---|---|
| Case verte | Prix le plus bas de la date, parmi les sites |
| **Aucun départ** | Le site ne propose pas de départ ce jour-là |
| **Un seul site** (colonne Écart) | Un seul site a un prix ce jour-là : pas d'écart calculable |
| **Égalité** (colonne Moins cher) | Plusieurs sites affichent le même prix le plus bas |
| **« Site » seul** (colonne Moins cher) | Seul ce site propose un départ ce jour-là |
| **Non précisée** (Excel, compagnie) | Le site n'indique pas la compagnie aérienne |

Les prix sont **par personne, sur la base d'une chambre double**. Ils changent souvent, parfois en moins d'une heure. La date et l'heure du relevé figurent au-dessus des résultats et dans chaque export.

---

## Ajouter un site

Dans le panneau de gauche, section **« Ajouter un site »**, saisissez l'adresse du site puis cliquez sur **Analyser le site**.

Le site n'est **ajouté que s'il remplit tous les critères** :

1. accessible sans blocage anti-robot (Cloudflare, DataDome, captcha, erreur 403…) ;
2. plateforme de prix reconnue par l'application ;
3. recherche d'un Produit par son nom ;
4. villes de départ et durées lisibles ;
5. prix date par date, **en euros**.

Sinon, l'analyse affiche la raison du refus. Le fichier `robots.txt` du site est aussi vérifié et signalé par un avertissement, sans bloquer l'ajout.

### Plateformes

Un connecteur correspond à une **plateforme de réservation**, c'est-à-dire au logiciel qui calcule et affiche les prix derrière le site. Tous les sites qui utilisent la même plateforme se lisent de la même façon.

| Plateforme | Exemple | Fonctionnement |
|---|---|---|
| `calendrier-json` | Ovoyages | Produits listés dans le plan du site ; prix via `/pricetable/…` |
| `grille-jsf` | Exotismes (.fr, .be) | Recherche par mots-clés ; prix via la grille `/reservation/grilles.jsf` |

Un site **accessible** dont la plateforme est inconnue est placé dans **« Sites à étudier »**, avec des repères techniques. Pour le rendre compatible, il faut écrire une nouvelle classe de plateforme dans `connecteurs.py` et l'ajouter au dictionnaire `PLATEFORMES`.

---

## Scripts en ligne de commande

Les rapports Word d'origine, qui donnent les tarifs du mercredi et du samedi par semaine, peuvent aussi être produits sans l'application :

```bash
# Ovoyages : Coral Level, départ Paris, d'octobre 2026 à août 2027
python rapport_ovoyages.py --ville PAR --nuits 5 --debut 10-2026 --fin 08-2027

# Exotismes : Iberostar Selection Bávaro Suites
python rapport_exotismes.py --nuits 7 --ville LYS

# Comparatif Ovoyages / Exotismes sur le Coral Level
python comparer_sites.py --nuits 5 --debut 10-2026 --fin 08-2027
```

Options communes : `--ville`, `--nuits`, `--debut` / `--fin` (format `MM-AAAA`), `--pause` (secondes entre deux requêtes, 3 par défaut) et `--sortie` (fichier Word). `--help` affiche l'aide de chaque script.

---

## Structure du projet

| Fichier | Rôle |
|---|---|
| `app.py` | Application Streamlit (interface, filtres, résultats) |
| `connecteurs.py` | Connecteurs par plateforme, registre des sites, cache |
| `analyse_site.py` | Analyse d'un site avant son ajout |
| `export_word.py` | Export Word et conversion PDF (via Microsoft Word) |
| `export_excel.py` | Export Excel (Synthèse, Comparatif, Détail) |
| `scrape_ovoyages.py`, `scrape_exotismes.py` | Lecture des prix d'Ovoyages et d'Exotismes |
| `rapport_ovoyages.py`, `rapport_exotismes.py`, `comparer_sites.py` | Rapports Word en ligne de commande |
| `lancer_app.bat` | Lancement de l'application par double-clic |

### Fichiers créés à l'usage (non versionnés)

| Fichier ou dossier | Contenu |
|---|---|
| `cache/` | Relevés de prix et catalogues mis en cache |
| `sites_ajoutes.json` | Sites ajoutés depuis l'application |
| `sites_a_etudier.json` | Sites accessibles en attente d'un connecteur |
| `*.docx`, `*.pdf`, `*.xlsx` | Rapports générés |

---

## Bonnes pratiques

- **Discrétion** : les scripts attendent environ 3 secondes entre deux requêtes vers un même site et réutilisent les relevés récents. Évitez de réduire la pause ou de lancer des relevés en boucle.
- **Sites protégés** : l'application ne contourne aucune protection anti-robot. Un site bloqué n'est pas ajouté.
- **`robots.txt`** : certains sites demandent aux robots de ne pas lire leurs pages de prix. L'application le signale, et c'est à l'utilisateur de décider de l'usage qu'il en fait.
- **Fiabilité** : si un site modifie son fonctionnement, son connecteur peut cesser de marcher. Le relevé affiche alors un avertissement pour le site concerné, sans bloquer les autres.
