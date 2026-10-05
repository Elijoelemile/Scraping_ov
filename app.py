"""Comparateur de prix de séjours — application locale.

Lancement : double-clic sur lancer_app.bat, bouton ▷ de VS Code, ou   python -m streamlit run app.py
"""
import subprocess
import sys
import time
from datetime import date, datetime

import streamlit as st
from streamlit import runtime

if __name__ == "__main__" and not runtime.exists():
    # Lancé avec « python app.py » (ex. bouton ▷ de VS Code) : on démarre Streamlit à sa place
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", __file__]))

import altair as alt
import pandas as pd

import analyse_site as A
import connecteurs as C
import scrape_ovoyages as ov
from export_excel import construire_xlsx
from export_word import construire_docx, docx_en_pdf
from rapport_ovoyages import MOIS_FR

JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
# Couleur fixe par site (ordre de la liste des sites) : la couleur suit le site, jamais son rang
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
VERT = "#C6EFCE"
# Durée pendant laquelle un relevé est réutilisé au lieu d'interroger de nouveau les sites
DUREES_CACHE = {0: "Jamais (toujours relever)", 15 * 60: "15 min", 30 * 60: "30 min", 3600: "1 h", 3 * 3600: "3 h"}
ICONES = {A.OK: "✅", A.KO: "❌", A.ALERTE: "⚠️"}

st.set_page_config(page_title="Comparateur de prix", page_icon="📊", layout="wide")
ss = st.session_state
ss.setdefault("connecteurs", {})
ss.setdefault("candidats", {})
ss.setdefault("memo", {})

SITES = C.sites_configures()
for _nom, _conf in SITES.items():  # un connecteur par site, créé une fois par session
    if _nom not in ss.connecteurs:
        ss.connecteurs[_nom] = C.creer_connecteur(_conf)
COULEUR_SITE = {s: PALETTE[i % len(PALETTE)] for i, s in enumerate(SITES)}


def memo(cle, fonction):
    """Garde en mémoire (pour la session) les réponses des sites : villes, nuits, mois."""
    if cle not in ss.memo:
        ss.memo[cle] = fonction()
    return ss.memo[cle]


AUCUNE = "Aucun départ"  # case de prix : le site ne propose pas de départ ce jour-là
UN_SEUL = "Un seul site"  # écart : il faut au moins deux sites avec un prix


def fmt_prix(x):
    return AUCUNE if pd.isna(x) else f"{int(x):,} €".replace(",", " ")


def libelle_mois(m):
    return f"{MOIS_FR[m[0]]} {m[1]}"


# ------------------------------------------------------------------ Panneau latéral : ajouter un site
with st.sidebar:
    st.header("Ajouter un site")
    st.caption("L'application analyse le site. Il n'est ajouté que s'il remplit tous les critères : "
               "accessible sans blocage anti-robot, plateforme de prix reconnue, recherche d'un Produit par son nom, "
               "villes et durées lisibles, prix date par date en euros.")
    with st.form("ajout_site", clear_on_submit=False):
        adresse = st.text_input("Adresse du site", placeholder="www.exemple.com")
        nom_site = st.text_input("Nom affiché (facultatif)")
        lien_test = st.text_input("Lien d'un Produit du site (facultatif)",
                                  help="Aide l'analyse à reconnaître le site. Sinon, un Produit est choisi automatiquement.")
        analyser = st.form_submit_button("Analyser le site", use_container_width=True)
    if analyser and adresse.strip():
        with st.status("Analyse du site…", expanded=True) as statut:
            res = A.analyser(adresse, lien_test, nom_site.strip(), progression=st.write)
            if res["ajoutable"]:
                C.enregistrer_site(res["conf"])
                statut.update(label=f"Site ajouté : {res['conf']['nom']}", state="complete")
            else:
                statut.update(label="Site non ajouté", state="error")
        ss.derniere_analyse = res
        if res["ajoutable"]:
            st.rerun()  # pour que le nouveau site apparaisse aussitôt dans le champ « Site »
    if ss.get("derniere_analyse"):
        res = ss.derniere_analyse
        for critere, etat, detail in res["etapes"]:
            st.markdown(f"{ICONES[etat]} **{critere}** — {detail}")
        if res["ajoutable"]:
            st.success(f"✅ Site ajouté : **{res['conf']['nom']}**. Il est maintenant proposé dans le champ « Site ».")
            if any(e == A.ALERTE and c == "robots.txt" for c, e, _ in res["etapes"]):
                st.warning("Ce site demande aux robots de ne pas lire ses pages de prix (robots.txt). "
                           "Vous pouvez le retirer ci-dessous si vous préférez ne pas l'utiliser.")
        else:
            st.error(f"❌ Site non ajouté : {res['raison']}.")
            if res["raison"].startswith("plateforme de prix non reconnue"):
                st.info("Ce site est accessible : il a été placé dans « Sites à étudier » ci-dessous. "
                        "Dans une prochaine conversation, dites à Claude « regarde les sites à étudier ».")
    ajoutes = C.sites_ajoutes()
    if ajoutes:
        st.divider()
        st.subheader("Sites ajoutés")
        for s in ajoutes:
            a, b = st.columns([3, 1])
            a.markdown(f"**{s['nom']}**  \n{s['base']}")
            if b.button("Retirer", key=f"retirer_{s['nom']}"):
                C.retirer_site(s["nom"])
                ss.connecteurs.pop(s["nom"], None)
                ss.pop("derniere_analyse", None)
                st.rerun()
    a_etudier = C.sites_a_etudier()
    if a_etudier:
        st.divider()
        st.subheader("Sites à étudier")
        st.caption("Sites accessibles dont la plateforme de prix n'est pas encore connue. Dites à Claude "
                   "« regarde les sites à étudier » pour qu'il apprenne ces plateformes à l'application.")
        for s in a_etudier:
            with st.container(border=True):
                st.markdown(f"**{C.hote(s['base'])}**  \nAnalysé le {s['date']}")
                note = st.text_input("Note", value=s.get("note", ""), key=f"note_{s['base']}",
                                     placeholder="ex. prioritaire", label_visibility="collapsed")
                n1, n2 = st.columns(2)
                if n1.button("Enregistrer la note", key=f"noter_{s['base']}", use_container_width=True):
                    C.annoter_a_etudier(s["base"], note)
                    st.toast("Note enregistrée")
                if n2.button("Retirer", key=f"retirer_etude_{s['base']}", use_container_width=True):
                    C.retirer_a_etudier(s["base"])
                    st.rerun()

# ------------------------------------------------------------------ 1. Recherche du Produit
st.title("Comparateur de prix")
st.caption("Recherchez un Produit par son nom sur un ou plusieurs sites, puis comparez les tarifs date par date.")

with st.form("recherche"):
    c1, c2, c3 = st.columns([3, 3, 1])
    produit = c1.text_input("Produit", value=ss.get("produit", ""), placeholder="ex. Coral Level, Bavaro Suites…")
    defaut = [s for s in ss.get("sites", [s["nom"] for s in C.SITES_INTEGRES]) if s in SITES]
    sites_choisis = c2.multiselect("Site", list(SITES), default=defaut,
                                   help="Pour ajouter un site à cette liste : « Ajouter un site », dans le panneau de gauche.")
    c3.write("")
    c3.write("")
    lancer_recherche = c3.form_submit_button("Rechercher", type="primary", use_container_width=True)

if lancer_recherche:
    if not produit.strip() or not sites_choisis:
        st.warning("Renseignez un Produit et au moins un Site.")
    else:
        ss.produit, ss.sites = produit.strip(), sites_choisis
        ss.pop("releve", None)
        trouves = {}
        with st.spinner("Recherche du Produit sur les sites…"):
            for s in sites_choisis:
                try:
                    trouves[s] = ss.connecteurs[s].rechercher(produit)
                except Exception as e:  # un site en panne ne bloque pas les autres
                    st.error(f"{s} : recherche impossible ({e})")
                    trouves[s] = []
            # Le même Produit porte souvent un autre nom selon le site : si un site n'a pas de correspondance
            # nette, on relance sa recherche avec le nom complet trouvé sur un autre site.
            nets = [c for lst in trouves.values() for c in lst if c.score >= 1]
            if nets:
                reference = max(nets, key=lambda c: c.score).nom
                for s in sites_choisis:
                    if not any(c.score >= 1 for c in trouves[s]):
                        try:
                            extra = ss.connecteurs[s].rechercher(reference)
                        except Exception:
                            extra = []
                        codes = {c.code for c in trouves[s]}
                        trouves[s] = sorted(trouves[s] + [c for c in extra if c.code not in codes],
                                            key=lambda c: -C.score_nom(reference, c.nom))
        ss.candidats = {s: [C.candidat_en_dict(c) for c in lst] for s, lst in trouves.items()}

if not ss.candidats:
    st.info("Commencez par rechercher un Produit.")
    st.stop()

# ------------------------------------------------------------------ 2. Choix du Produit sur chaque site
st.subheader(f"Produit « {ss.produit} »")
choix = {}
colonnes = st.columns(len(ss.candidats))
for col, (s, lst) in zip(colonnes, ss.candidats.items()):
    with col:
        st.markdown(f"**{s}**")
        if not lst:
            st.warning("Aucun Produit trouvé sur ce site.")
        else:
            cands = [C.candidat_depuis_dict(d) for d in lst]
            i = st.selectbox("Produit trouvé", range(len(cands)), format_func=lambda k, c=cands: c[k].libelle(),
                             key=f"choix_{s}", label_visibility="collapsed")
            choix[s] = cands[i]
            st.caption(f"[Voir la page du Produit]({cands[i].url})")
        with st.expander("Le bon Produit n'est pas là ?"):
            autre = st.text_input("Chercher un autre nom sur ce site", key=f"autre_{s}")
            if st.button("Chercher", key=f"btn_autre_{s}") and autre.strip():
                with st.spinner("Recherche…"):
                    ss.candidats[s] = [C.candidat_en_dict(c) for c in ss.connecteurs[s].rechercher(autre)]
                ss.pop(f"choix_{s}", None)
                st.rerun()

if not choix:
    st.stop()

# ------------------------------------------------------------------ 3. Paramètres du relevé
st.subheader("Paramètres du relevé")
try:
    with st.spinner("Lecture des villes de départ, durées et mois proposés…"):
        villes_par_site = {s: memo(("villes", s, c.code), lambda s=s, c=c: ss.connecteurs[s].villes(c))
                           for s, c in choix.items()}
        communes = set.intersection(*(set(v) for v in villes_par_site.values()))
        libelles = {}
        for v in villes_par_site.values():
            for code, lib in v.items():
                libelles.setdefault(code, lib)
except Exception as e:
    st.error(f"Impossible de lire les options du Produit : {e}")
    st.stop()

if not communes:
    st.error("Aucune ville de départ commune à ces sites pour ce Produit.")
    st.stop()

p1, p2, p3 = st.columns([2, 1, 3])
villes_triees = sorted(communes, key=lambda c: (c != "PAR", libelles[c]))
ville = p1.selectbox("Ville de départ", villes_triees, format_func=lambda c: f"{libelles[c]} ({c})")

with st.spinner("Lecture des durées proposées…"):
    nuits_communes = sorted(set.intersection(*(
        set(memo(("nuits", s, c.code, ville), lambda s=s, c=c: ss.connecteurs[s].nuits(c, ville)))
        for s, c in choix.items())))
if not nuits_communes:
    st.error("Aucune durée commune à ces sites pour cette ville de départ.")
    st.stop()
nuits = p2.selectbox("Nombre de nuits", nuits_communes, index=0)

with st.spinner("Lecture des mois proposés…"):
    mois_dispo = sorted(set().union(*(
        set(map(tuple, memo(("mois", s, c.code, ville, nuits),
                            lambda s=s, c=c: ss.connecteurs[s].mois(c, ville, nuits))))
        for s, c in choix.items())), key=lambda m: (m[1], m[0]))
if not mois_dispo:
    st.error("Aucun mois de départ proposé avec ces paramètres.")
    st.stop()
debut, fin = p3.select_slider("Période", options=mois_dispo, value=(mois_dispo[0], mois_dispo[min(10, len(mois_dispo) - 1)]),
                              format_func=libelle_mois)
periode =[m for m in mois_dispo if (debut[1], debut[0]) <= (m[1], m[0]) <= (fin[1], fin[0])]

b1, b2, _ = st.columns([1, 1, 2])
duree_cache = b2.selectbox("Réutiliser un relevé de moins de", list(DUREES_CACHE), index=3,
                           format_func=DUREES_CACHE.get,
                           help="Les prix changent souvent (parfois en moins d'une heure). Un relevé récent est "
                                "réutilisé pour ne pas interroger les sites inutilement ; « Jamais » relève tout.")
duree_estimee = len(choix) * len(periode) * 4
if b1.button(f"Lancer le relevé (≈ {max(1, round(duree_estimee / 60))} min)", type="primary", use_container_width=True):
    lignes, erreurs, ages = [], [], []
    barre = st.progress(0.0, text="Relevé en cours…")
    etapes = len(choix) * len(periode)
    k = 0
    for s, c in choix.items():
        for mm, aaaa in periode:
            k += 1
            barre.progress(k / etapes, text=f"{s} · {libelle_mois((mm, aaaa))}")
            cle = f"prix_{s}_{c.code}_{ville}_{nuits}_{mm:02d}_{aaaa}"
            prix = C.cache_lire(cle, duree_cache) if duree_cache else None
            if prix is None:
                try:
                    prix = ss.connecteurs[s].prix(c, ville, nuits, mm, aaaa)
                    C.cache_ecrire(cle, prix)
                    ages.append(0)
                except Exception as e:
                    erreurs.append(f"{s} · {libelle_mois((mm, aaaa))} : {e}")
                    prix = []
            else:
                ages.append(C.cache_age(cle))
            lignes += [dict(p, site=s) for p in prix]
    barre.empty()
    for e in erreurs:
        st.warning(e)
    ss.releve = {"lignes": lignes, "sites": list(choix), "ville": ville, "libelle_ville": libelles[ville],
                 "nuits": nuits, "periode": periode, "produits": {s: c.libelle() for s, c in choix.items()},
                 # date du plus ancien prix affiché (les prix réutilisés sont plus anciens que le clic)
                 "releve_le": time.time() - max(ages, default=0)}

if "releve" not in ss:
    st.stop()

# ------------------------------------------------------------------ 4. Données et filtres
R = ss.releve
df = pd.DataFrame(R["lignes"])
if df.empty:
    st.warning("Aucun prix trouvé pour ces paramètres.")
    st.stop()
df["date"] = pd.to_datetime(df["date"])
df["mm"], df["aaaa"] = df["date"].dt.month, df["date"].dt.year
df["jour"] = df["date"].dt.weekday.map(lambda i: JOURS[i])
df["semaine"] = df["date"].map(lambda d: ov.semaine_du_mois(d.date()))
sites = [s for s in R["sites"] if s in set(df["site"])]

st.divider()
st.subheader("Filtres")
f1, f2, f3, f4, f5, f6 = st.columns([3, 2, 2, 1.5, 1.5, 2])
jours = f1.multiselect("Jours", JOURS, default=["Mer", "Sam"])
semaines = f2.multiselect("Semaines", list(range(1, 7)), default=list(range(1, 7)),
                          format_func=lambda n: f"Semaine {n}")
compagnies = sorted(x for x in df["compagnie"].dropna().unique() if x)
choix_cies = f3.multiselect("Compagnie aérienne", compagnies, default=compagnies,
                            help="Seul Exotismes indique la compagnie ; les autres sites ne sont pas filtrés.",
                            disabled=not compagnies)
budget = f4.number_input("Budget max (€)", min_value=0, value=0, step=50, help="0 = sans limite")
seul_mp = f5.checkbox("« Meilleur prix » uniquement")
ecart_min = f6.number_input("Écart minimum (€)", min_value=0, value=0, step=10, disabled=len(sites) < 2,
                            help="N'afficher que les dates où l'écart entre sites atteint ce montant.")

filtre = df[df["jour"].isin(jours) & df["semaine"].isin(semaines)]
if compagnies:
    filtre = filtre[(filtre["compagnie"] == "") | filtre["compagnie"].isin(choix_cies)]

# Vue « large » : une ligne par date, une colonne de prix par site
large = filtre.pivot_table(index="date", columns="site", values="prix_eur", aggfunc="min").reindex(columns=sites)
mp = filtre.pivot_table(index="date", columns="site", values="meilleur_prix", aggfunc="max").reindex(columns=sites)
large = large.join(mp.add_suffix("__mp")).reset_index()
large["mm"], large["aaaa"] = large["date"].dt.month, large["date"].dt.year
large["jour"] = large["date"].dt.weekday.map(lambda i: JOURS[i])
large["semaine"] = large["date"].map(lambda d: ov.semaine_du_mois(d.date()))
prix_sites = large[sites]
large["mini"] = prix_sites.min(axis=1)
large["ecart"] = prix_sites.max(axis=1) - large["mini"]
large.loc[prix_sites.count(axis=1) < 2, "ecart"] = float("nan")


def moins_cher(r):
    dispo = {s: r[s] for s in sites if pd.notna(r[s])}
    if not dispo:
        return ""
    if len(dispo) == 1:
        return f"{next(iter(dispo))} seul" if len(sites) > 1 else ""
    m = min(dispo.values())
    gagnants = [s for s, v in dispo.items() if v == m]
    return "Égalité" if len(gagnants) > 1 else gagnants[0]


large["moins_cher"] = large.apply(moins_cher, axis=1)
if budget:
    large = large[large["mini"] <= budget]
if seul_mp:
    large = large[large[[f"{s}__mp" for s in sites]].fillna(False).any(axis=1)]
if ecart_min:
    large = large[large["ecart"] >= ecart_min]

if large.empty:
    st.warning("Aucune date ne correspond aux filtres.")
    st.stop()

# ------------------------------------------------------------------ 5. Résultats
contexte = [f"Sites : {', '.join(sites)}",
            *[f"{s} : {R['produits'][s]}" for s in sites],
            f"Départ : {R['libelle_ville']} · {R['nuits']} nuits · "
            f"{libelle_mois(R['periode'][0])} à {libelle_mois(R['periode'][-1])}",
            f"Filtres : jours {', '.join(jours) or 'aucun'}"
            + (f" · budget ≤ {budget} €" if budget else "") + (" · « Meilleur prix » uniquement" if seul_mp else "")
            + (f" · écart ≥ {ecart_min} €" if ecart_min else "")]

# Synthèse par site
lignes_synth = []
for s in sites:
    col = large[s].dropna()
    if col.empty:
        continue
    i_min = col.idxmin()
    lignes_synth.append({
        "Site": s,
        "Dates avec prix": len(col),
        "Moins cher (nb dates)": int((large["moins_cher"] == s).sum()),
        "Prix moyen": fmt_prix(col.mean()),
        "Prix le plus bas": fmt_prix(col.min()),
        "Date du prix le plus bas": large.loc[i_min, "date"].strftime("%d/%m/%Y"),
    })
synthese = pd.DataFrame(lignes_synth)

age_min = round((time.time() - R["releve_le"]) / 60)
st.caption(f"Prix relevés le {datetime.fromtimestamp(R['releve_le']):%d/%m/%Y} à {datetime.fromtimestamp(R['releve_le']):%H:%M}"
           + (f" (il y a {age_min} min)" if age_min else " (à l'instant)")
           + ". Relancez le relevé pour actualiser.")
m1, m2, m3 = st.columns(3)
meilleur = large.loc[large["mini"].idxmin()]
m1.metric("Prix le plus bas", fmt_prix(meilleur["mini"]),
          f"{meilleur['date']:%d/%m/%Y} · {meilleur['moins_cher'] or sites[0]}", delta_color="off")
m2.metric("Dates affichées", len(large))
if len(sites) > 1:
    m3.metric("Écart moyen entre sites", fmt_prix(large["ecart"].mean()))

onglets = st.tabs(["Comparatif par mois", "Synthèse", "Graphique", "Meilleures dates", "Export Word / PDF / Excel"])

with onglets[0]:
    for (aaaa, mm), bloc in large.sort_values("date").groupby(["aaaa", "mm"], sort=True):  # ordre chronologique
        st.markdown(f"#### {MOIS_FR[mm]} {aaaa}")
        vue = pd.DataFrame({"Semaine": bloc["semaine"].map(lambda n: f"Semaine {n}"), "Jour": bloc["jour"],
                            "Date": bloc["date"].dt.strftime("%d/%m")})
        for s in sites:
            vue[s] = bloc[s].values
        if len(sites) > 1:
            vue["Moins cher"] = bloc["moins_cher"].values
            vue["Écart"] = bloc["ecart"].values

        # Le navigateur affiche « None » pour toute case vide, quel que soit le format demandé :
        # on envoie donc du texte déjà mis en forme, et on calcule le surlignage sur les vrais prix.
        nombres = vue.reset_index(drop=True)
        texte = nombres.copy()
        for col in sites:
            texte[col] = nombres[col].map(fmt_prix)
        if len(sites) > 1:
            texte["Écart"] = nombres["Écart"].map(lambda x: UN_SEUL if pd.isna(x) else fmt_prix(x))
        if len(sites) > 1:
            texte["Moins cher"] = nombres["Moins cher"].replace("", AUCUNE)

        def surligner(ligne):
            n = nombres.loc[ligne.name]
            vals = [n[s] for s in sites if pd.notna(n[s])]
            m = min(vals) if vals else None
            styles = []
            for c in ligne.index:
                if ligne[c] in (AUCUNE, UN_SEUL):
                    styles.append("color: #9AA5B1")
                elif len(sites) > 1 and c in sites and pd.notna(n[c]) and n[c] == m:
                    styles.append(f"background-color: {VERT}; color: #0b0b0b; font-weight: 600")
                else:
                    styles.append("")
            return styles

        st.dataframe(texte.style.apply(surligner, axis=1), hide_index=True, use_container_width=True)

with onglets[1]:
    st.dataframe(synthese, hide_index=True, use_container_width=True)
    if len(sites) > 1:
        st.caption(f"Égalités : {int((large['moins_cher'] == 'Égalité').sum())} date(s). "
                   "« Moins cher » ne compte que les dates où au moins deux sites ont un prix.")

with onglets[2]:
    long = large.melt(id_vars=["date", "jour"], value_vars=sites, var_name="Site", value_name="Prix").dropna()
    base = alt.Chart(long).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%d/%m/%y", grid=False)),
        y=alt.Y("Prix:Q", title="Prix par personne (€)", scale=alt.Scale(zero=False)),
        color=alt.Color("Site:N", scale=alt.Scale(domain=sites, range=[COULEUR_SITE[s] for s in sites]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Site:N"), alt.Tooltip("date:T", title="Date", format="%a %d/%m/%Y"),
                 alt.Tooltip("Prix:Q", format=",.0f", title="Prix (€)")],
    )
    graphe = base.mark_line(strokeWidth=2) + base.mark_point(size=64, filled=True)
    st.altair_chart(graphe.properties(height=380).interactive(bind_y=False), use_container_width=True)
    st.caption("Prix des dates filtrées (jours, semaines, budget…). Survolez un point pour le détail.")

with onglets[3]:
    top = long.sort_values("Prix").head(15).copy() if len(long) else long
    top["Date"] = top["date"].dt.strftime("%d/%m/%Y")
    top["Prix"] = top["Prix"].map(fmt_prix)
    st.dataframe(top[["Date", "jour", "Site", "Prix"]].rename(columns={"jour": "Jour"}), hide_index=True,
                 use_container_width=True)

with onglets[4]:
    nom_fichier = "comparatif_" + "_".join(C.normaliser(ss.produit)) + f"_{date.today():%Y%m%d}"
    releve_le = datetime.fromtimestamp(R["releve_le"])
    docx = construire_docx(ss.produit, sites, contexte, large, synthese, releve_le)
    # Détail Excel : les lignes brutes des dates affichées (mêmes filtres que le tableau)
    details = filtre[filtre["date"].isin(large["date"]) & filtre["site"].isin(sites)]
    xlsx = construire_xlsx(ss.produit, sites, contexte, large, details, releve_le, R["nuits"])
    e1, e2, e3 = st.columns(3)
    e1.download_button("Télécharger en Word", docx, file_name=nom_fichier + ".docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       use_container_width=True)
    if e2.button("Préparer le PDF", use_container_width=True):
        with st.spinner("Conversion en PDF avec Word…"):
            ss.pdf, ss.pdf_de = docx_en_pdf(docx), hash(docx)
        if ss.pdf is None:
            st.error("Conversion impossible : Microsoft Word est nécessaire pour créer le PDF.")
    if ss.get("pdf") and ss.get("pdf_de") == hash(docx):  # PDF à jour avec les filtres affichés
        e2.download_button("Télécharger le PDF", ss.pdf, file_name=nom_fichier + ".pdf", mime="application/pdf",
                           use_container_width=True)
    e3.download_button("Télécharger en Excel", xlsx, file_name=nom_fichier + ".xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
    st.caption("Les fichiers reprennent exactement les données et les filtres affichés. Le fichier Excel contient "
               "3 onglets : Synthèse, Comparatif (filtrable, une ligne par date) et Détail (toutes les lignes).")
