// Reçoit ce que capture.js a recopié dans la page et le range dans le stockage de l'extension.
const MAX_CAPTURES = 1000;
let file = Promise.resolve();  // une écriture à la fois, pour ne rien perdre

window.addEventListener("message", (evenement) => {
  if (evenement.source !== window || !evenement.data || evenement.data.source !== "releve-comparateur") return;
  const { type, data } = evenement.data;
  file = file.then(async () => {
    const st = await chrome.storage.local.get({ captures: [], produits: {} });
    if (type === "capture") {
      st.captures.push(data);
      if (st.captures.length > MAX_CAPTURES) st.captures = st.captures.slice(-MAX_CAPTURES);
    } else if (type === "produit") {
      st.produits[data.code] = data;
    } else {
      return;
    }
    await chrome.storage.local.set(st);
  }).catch(() => {});
});
