// ReadySetClass Student — Service Worker
// Cache static assets only, never HTML

const CACHE_NAME = 'rsc-student-v1';
const STATIC_ASSETS = [
    '/assets/style.css',
    '/assets/app.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Never cache HTML or API calls
    if (event.request.mode === 'navigate' || url.pathname.startsWith('/api/')) {
        return;
    }

    // Cache-first for static assets
    event.respondWith(
        caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
});
