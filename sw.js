// FX Navigator service worker
const CACHE = 'fxnavi-v4';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './icon-180.png', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  // HTML/ナビゲーション・status.json は常に最新を取得（オフライン時のみキャッシュ）
  const fresh = req.mode === 'navigate' || url.pathname.endsWith('.html')
             || url.pathname.endsWith('/') || url.pathname.endsWith('status.json') || url.pathname.endsWith('positions.json');
  if (fresh) {
    e.respondWith(
      fetch(req).then(res => {
        if (res && res.ok && req.method === 'GET') {
          const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
    );
    return;
  }
  // アイコン/manifest等はキャッシュ優先
  e.respondWith(caches.match(req).then(r => r || fetch(req)));
});
