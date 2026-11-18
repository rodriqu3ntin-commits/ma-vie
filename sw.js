// Ma vie — lecture des vidéos Google Drive en streaming (v3 : photos mises en cache).
// Les requêtes vers ./gdrive/<id>?s=<taille>&m=<type> sont relayées vers l'API Drive
// avec le jeton de connexion, par morceaux (requêtes Range), pour que les vidéos se lisent
// sur ordinateur comme sur iPhone/Android.
const CHUNK = 4 * 1024 * 1024;
let token = null;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'gtok') token = e.data.tok || null;
});

async function askToken() {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
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

function driveFetch(id, range) {
  const headers = { Authorization: 'Bearer ' + token };
  if (range) headers.Range = range;
  return fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(id) + '?alt=media&acknowledgeAbuse=true', { headers });
}

async function relay(request, id, size, mime) {
  if (!token) token = await askToken();
  if (!token) return new Response('Connexion Google requise', { status: 401 });

  // Plage demandée par le lecteur vidéo.
  let start = 0, end = size ? size - 1 : null;
  const m = /bytes=(\d*)-(\d*)/.exec(request.headers.get('Range') || '');
  const partial = !!m && !!size;
  if (partial) {
    if (m[1] === '' && m[2] !== '') { start = Math.max(0, size - Number(m[2])); end = size - 1; }
    else { start = Number(m[1] || 0); end = m[2] !== '' ? Math.min(Number(m[2]), size - 1) : Math.min(start + CHUNK - 1, size - 1); }
    if (start >= size) return new Response(null, { status: 416, headers: { 'Content-Range': 'bytes */' + size } });
  }

  let r = await driveFetch(id, partial ? 'bytes=' + start + '-' + end : null);
  if (r.status === 401 || r.status === 403) {
    token = await askToken();
    if (!token) return new Response('Connexion Google expirée', { status: 401 });
    r = await driveFetch(id, partial ? 'bytes=' + start + '-' + end : null);
  }
  if (!r.ok) return new Response('Erreur Drive ' + r.status, { status: r.status });

  const h = new Headers({ 'Content-Type': mime || r.headers.get('content-type') || 'video/mp4', 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store' });
  if (partial) {
    h.set('Content-Range', 'bytes ' + start + '-' + end + '/' + size);
    h.set('Content-Length', String(end - start + 1));
    return new Response(r.body, { status: 206, headers: h });
  }
  if (size) h.set('Content-Length', String(size));
  return new Response(r.body, { status: 200, headers: h });
}

self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  const m = u.pathname.match(/\/gdrive\/([\w-]+)$/);
  if (!m) return;
  const size = Number(u.searchParams.get('s')) || 0, mime = u.searchParams.get('m') || '';
  if (mime.startsWith('image/')) { e.respondWith(cachedImage(e.request, m[1], size, mime)); return; }
  e.respondWith(relay(e.request, m[1], size, mime));
});

// Photos des dossiers : gardées sur l'appareil après le premier chargement.
const IMG_CACHE = 'mavie-photos-v1';
async function cachedImage(request, id, size, mime) {
  const key = new URL('./gdrive-img/' + id, self.registration.scope).href;
  const cache = await caches.open(IMG_CACHE);
  const hit = await cache.match(key);
  if (hit) return hit;
  const r = await relay(new Request(request.url), id, size, mime);
  if (r.ok && r.status === 200) {
    const buf = await r.arrayBuffer();
    const res = new Response(buf, { status: 200, headers: { 'Content-Type': mime } });
    try { await cache.put(key, res.clone()); } catch (e) {}
    return res;
  }
  return r;
}
