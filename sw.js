// FX Navi Service Worker
// ネットワーク優先：常に最新の index.html 等を取得し、オフライン時のみキャッシュにフォールバック。
// 更新時は VERSION を上げる（任意）。activate で旧キャッシュを全削除し、即座に新SWへ切替。
const VERSION = 'fxnavi-v2';
const CORE = ['./', './index.html'];

self.addEventListener('install', (e) => {
  self.skipWaiting(); // 新SWを待たせず即適用
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(CORE).catch(() => {})));
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))); // 旧バージョンを一掃
    await self.clients.claim(); // 開いているページを即座に新SWの管理下へ
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return; // POST等はそのまま
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  // 同一オリジンのみSWで処理。Worker/GMO等の外部API（価格・klines）はSWを通さず素通し（古い価格をキャッシュしない）
  if (url.origin !== self.location.origin) return;
  // ネットワーク優先：最新を取得し裏でキャッシュ更新。失敗時のみキャッシュを返す
  e.respondWith((async () => {
    try {
      const fresh = await fetch(req, { cache: 'no-store' });
      try { const c = await caches.open(VERSION); c.put(req, fresh.clone()); } catch (_) {}
      return fresh;
    } catch (err) {
      const cached = await caches.match(req);
      return cached || new Response('offline', { status: 503, statusText: 'offline' });
    }
  })());
});
