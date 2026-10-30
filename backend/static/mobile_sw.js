const CACHE = 'cimitir-mobile-shell-v6';
const SHELL = [
  '/mobile/',
  '/mobile/manifest.webmanifest',
  '/mobile-assets/mobile_app.css',
  '/mobile-assets/mobile_app.js',
  '/mobile-assets/mobile_offline.js',
  '/mobile-assets/mobile_ext.js',
  '/mobile-assets/mobile_photos.js',
  '/mobile-assets/mobile-icon-192.png',
  '/mobile-assets/mobile-icon-512.png'
];
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  event.respondWith(fetch(event.request).then((response) => {
    if (response.ok && (url.pathname.startsWith('/mobile/') || url.pathname.startsWith('/mobile-assets/'))) {
      const copy = response.clone(); caches.open(CACHE).then((cache) => cache.put(event.request, copy));
    }
    return response;
  }).catch(() => caches.match(event.request).then((r) => r || caches.match('/mobile/'))));
});
