// Affiche sur l'icône de l'extension le nombre de mois relevés.
async function majBadge() {
  const { captures = [] } = await chrome.storage.local.get({ captures: [] });
  const mois = new Set();
  for (const c of captures) {
    for (const a of (c.reponse && c.reponse.availabilityList) || []) {
      mois.add(`${c.params.productId}|${a.n}|${String(a.dt).slice(0, 7)}`);
    }
  }
  await chrome.action.setBadgeText({ text: mois.size ? String(mois.size) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#2E3E50" });
}

chrome.storage.onChanged.addListener(majBadge);
chrome.runtime.onStartup.addListener(majBadge);
chrome.runtime.onInstalled.addListener(majBadge);
