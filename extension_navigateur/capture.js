// S'exécute dans la page Promoséjours. N'envoie AUCUNE requête et ne clique sur rien :
// il recopie seulement les réponses du calendrier que le site envoie déjà au navigateur.
(() => {
  const CIBLE = "/ajax/booking/reloadData.do";  // réponse JSON du calendrier de prix
  const envoyer = (type, data) =>
    window.postMessage({ source: "releve-comparateur", type, data }, location.origin);

  function traiter(url, corps, texte) {
    if (!url || !String(url).includes(CIBLE)) return;
    let reponse;
    try { reponse = JSON.parse(texte); } catch { return; }
    if (!reponse || !Array.isArray(reponse.availabilityList)) return;
    const params = {};
    try {
      new URLSearchParams(typeof corps === "string" ? corps : "").forEach((v, k) => { params[k] = v; });
    } catch { /* corps illisible : on garde la réponse seule */ }
    envoyer("capture", { url: String(url), params, reponse, capture_le: new Date().toISOString() });
  }

  // Le calendrier du site utilise XMLHttpRequest (jQuery)
  const ouvrir = XMLHttpRequest.prototype.open;
  const envoyerXhr = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (methode, url, ...reste) {
    this.__releveUrl = url;
    return ouvrir.call(this, methode, url, ...reste);
  };
  XMLHttpRequest.prototype.send = function (corps) {
    this.addEventListener("load", () => {
      try {
        if (this.responseType === "" || this.responseType === "text") traiter(this.__releveUrl, corps, this.responseText);
      } catch { /* ne jamais gêner le site */ }
    });
    return envoyerXhr.call(this, corps);
  };

  // Au cas où le site passerait un jour à fetch()
  const fetchOrigine = window.fetch;
  window.fetch = async function (entree, init) {
    const reponse = await fetchOrigine.apply(this, arguments);
    try {
      const url = typeof entree === "string" ? entree : entree && entree.url;
      if (url && String(url).includes(CIBLE)) {
        const corps = init && typeof init.body === "string" ? init.body : "";
        reponse.clone().text().then((texte) => traiter(url, corps, texte)).catch(() => {});
      }
    } catch { /* ne jamais gêner le site */ }
    return reponse;
  };

  // Informations du Produit lues dans la page (nom, référence, voyagiste, villes de départ)
  function lireProduit() {
    const valeur = (id) => (document.getElementById(id) || {}).value || "";
    const code = valeur("catalogCode");
    if (!code) return;
    const titre = document.querySelector("h1");
    const villes = [...document.querySelectorAll("#departure-city option")]
      .map((o) => ({ code: o.value, libelle: o.textContent.trim().split(",")[0] }))
      .filter((v) => v.code);
    envoyer("produit", {
      code,
      toCode: valeur("toCode"),
      voyagiste: valeur("provider"),
      nom: titre ? titre.textContent.trim().replace(/\s+/g, " ") : document.title,
      url: location.href.split("?")[0],
      villes,
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", lireProduit);
  else lireProduit();
})();
