// Service worker : l'application reste utilisable quand Internet coupe.
// - les pages sont gardées en cache (réseau d'abord, cache si hors connexion) ;
// - les données consultées (GET) sont gardées pour être relues hors connexion ;
// - les enregistrements (paiements, notes, appel) sont mis en file d'attente par la page elle-même.
const VERSION = "v15";
const COQUILLE = "coquille-" + VERSION;
const DONNEES = "donnees";
const PAGES = ["/", "/espace-parents", "/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png"];
const JAMAIS = ["/docs", "/openapi.json", "/redoc", "/sw.js", "/admin"];   // la console admin n'est jamais gardée sur l'appareil

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(COQUILLE).then((c) => c.addAll(PAGES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((cles) => Promise.all(cles.filter((k) => k.startsWith("coquille-") && k !== COQUILLE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "vider-donnees") e.waitUntil(caches.delete(DONNEES));
});

function horsLigne(message) {
  return new Response(JSON.stringify({ detail: message }), {
    status: 503, headers: { "Content-Type": "application/json", "X-Hors-Ligne": "1" },
  });
}

async function reseauPuisCache(requete, nomCache) {
  try {
    const reponse = await fetch(requete);
    if (reponse.ok) {
      const type = reponse.headers.get("Content-Type") || "";
      if (nomCache !== DONNEES || type.includes("json")) {
        const cache = await caches.open(nomCache);
        cache.put(requete, reponse.clone());
      }
    }
    return reponse;
  } catch (erreur) {
    const enCache = await caches.match(requete, { ignoreVary: true });
    if (enCache) {
      const entetes = new Headers(enCache.headers);
      entetes.set("X-Hors-Ligne", "1");
      return new Response(enCache.body, { status: enCache.status, headers: entetes });
    }
    if (requete.mode === "navigate") return (await caches.match("/")) || horsLigne("Hors connexion");
    return horsLigne("Hors connexion : ces informations n'ont pas encore été consultées sur cet appareil");
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (JAMAIS.some((p) => url.pathname.startsWith(p))) return;
  const page = req.mode === "navigate" || PAGES.includes(url.pathname);
  e.respondWith(reseauPuisCache(req, page ? COQUILLE : DONNEES));
});
