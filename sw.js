// Ma vie — lecture des vidéos Google Drive en streaming.
// Les requêtes vers ./gdrive/<id> sont relayées vers l'API Drive avec le jeton de connexion.
let token = null;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'gtok') token = e.data.tok || null;
});

async function askToken() {
  const clients = await self.clients.matchAll({ type: 'window' });
  for (const c of clients) {
    const tok = await new Promise((resolve) => {
      const ch = new MessageChannel();
      ch.port1.onmessage = (ev) => resolve(ev.data || null);
      c.postMessage({ type: 'needtok' }, [ch.port2]);
      setTimeout(() => resolve(null), 1500);
    });
    if (tok) return tok;
  }
  return null;
}

async function relay(request, id) {
  if (!token) token = await askToken();
  if (!token) return new Response('Connexion Google requise', { status: 401 });
  const headers = { Authorization: 'Bearer ' + token };
  const range = request.headers.get('Range');
  if (range) headers.Range = range;
  let r = await fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(id) + '?alt=media', { headers });
  if (r.status === 401) {
    token = await askToken();
    if (!token) return new Response('Connexion Google expirée', { status: 401 });
    headers.Authorization = 'Bearer ' + token;
    r = await fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(id) + '?alt=media', { headers });
  }
  const h = new Headers();
  for (const k of ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified']) {
    const v = r.headers.get(k);
    if (v) h.set(k, v);
  }
  if (!h.get('accept-ranges')) h.set('accept-ranges', 'bytes');
  if (!h.get('content-type') || h.get('content-type') === 'application/octet-stream') h.set('content-type', 'video/mp4');
  return new Response(r.body, { status: r.status, statusText: r.statusText, headers: h });
}

self.addEventListener('fetch', (e) => {
  const m = new URL(e.request.url).pathname.match(/\/gdrive\/([\w-]+)$/);
  if (m) e.respondWith(relay(e.request, m[1]));
});
