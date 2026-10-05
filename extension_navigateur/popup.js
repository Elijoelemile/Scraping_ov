const MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];

// Résumé : par Produit, ville et durée, la liste des mois relevés
function resumer(captures, produits) {
  const groupes = new Map();
  for (const c of captures) {
    const code = c.params.productId || "?";
    const ville = (c.reponse.availabilityForm && c.reponse.availabilityForm.departureCityCode) || c.params.departureCityCode || "?";
    for (const a of c.reponse.availabilityList || []) {
      const cle = `${code}|${ville}|${a.n}`;
      if (!groupes.has(cle)) groupes.set(cle, { code, ville, nuits: a.n, mois: new Set() });
      groupes.get(cle).mois.add(String(a.dt).slice(0, 7));
    }
  }
  const villesLib = {};
  for (const p of Object.values(produits)) for (const v of p.villes || []) villesLib[v.code] = v.libelle;
  return [...groupes.values()].map((g) => ({
    ...g,
    nom: (produits[g.code] && produits[g.code].nom) || `Produit ${g.code}`,
    villeLib: villesLib[g.ville] || g.ville,
    mois: [...g.mois].sort().map((m) => `${MOIS[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`),
  }));
}

async function afficher() {
  const { captures = [], produits = {} } = await chrome.storage.local.get({ captures: [], produits: {} });
  const liste = document.getElementById("liste");
  const groupes = resumer(captures, produits);
  document.getElementById("exporter").disabled = !groupes.length;
  if (!groupes.length) {
    liste.innerHTML = '<div class="vide">Aucun prix relevé pour l\'instant.</div>';
    return;
  }
  liste.innerHTML = "";
  for (const g of groupes) {
    const bloc = document.createElement("div");
    bloc.className = "produit";
    const nom = document.createElement("div");
    nom.className = "nom";
    nom.textContent = g.nom;
    const detail = document.createElement("div");
    detail.className = "ligne";
    detail.textContent = `Départ ${g.villeLib} · ${g.nuits} nuits · ${g.mois.length} mois : ${g.mois.join(", ")}`;
    bloc.append(nom, detail);
    liste.append(bloc);
  }
}

document.getElementById("exporter").addEventListener("click", async () => {
  const { captures = [], produits = {} } = await chrome.storage.local.get({ captures: [], produits: {} });
  const fichier = {
    format: "comparateur-releve-navigateur",
    version: 1,
    site: "Promoséjours",
    base: "https://www.promosejours.com",
    exporte_le: new Date().toISOString(),
    produits,
    captures,
  };
  const blob = new Blob([JSON.stringify(fichier)], { type: "application/json" });
  const lien = document.createElement("a");
  const horodatage = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  lien.href = URL.createObjectURL(blob);
  lien.download = `releve_promosejours_${horodatage}.json`;
  lien.click();
  document.getElementById("message").textContent =
    "Fichier enregistré dans vos Téléchargements. Importez-le dans l'application (panneau de gauche).";
});

document.getElementById("effacer").addEventListener("click", async () => {
  if (!confirm("Effacer tous les prix relevés ?")) return;
  await chrome.storage.local.set({ captures: [], produits: {} });
  document.getElementById("message").textContent = "";
  afficher();
});

afficher();
