// FX Navigator service worker
const CACHE = 'fxnavi-v1';
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
  const url = new URL(e.request.url);
  // status.json は常に最新をネットワークから（取れない時だけキャッシュ）
  if (url.pathname.endsWith('status.json')) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  // それ以外はキャッシュ優先（オフラインでもシェル表示）
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
