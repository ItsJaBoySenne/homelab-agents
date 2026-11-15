// Service worker for the installable dashboard. It keeps the app shell (page, script, styles, icons) so the app
// opens without a network, and always tries the network first so a deploy shows up on the next load.
// Push notifications (push.py) open the page they name when tapped.
// Everything under /ui/api/ (data, streams, sign-in) goes straight to the server and is never stored.
const CACHE = 'hla-shell-v1';
const SHELL = ['/ui/', '/ui/app.js', '/ui/style.css', '/ui/markdown.js', '/ui/manifest.webmanifest', '/ui/icons/icon-192.png', '/ui/icons/icon.svg', '/ui/fonts/InterVariable.woff2'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith('/ui') || url.pathname.startsWith('/ui/api/')) return;
  event.respondWith(fetch(req).then(res => {
    // Only plain successful answers are kept: not a redirect to the sign-in portal, not an error page.
    if (res.ok && res.type === 'basic' && !res.redirected) {
      const copy = res.clone();
      caches.open(CACHE).then(cache => cache.put(req.mode === 'navigate' ? '/ui/' : req, copy));
    }
    return res;
  }).catch(() => caches.match(req.mode === 'navigate' ? '/ui/' : req, { ignoreSearch: true })
    .then(hit => hit || new Response('Offline: the agent platform is not reachable.', { status: 503, headers: { 'content-type': 'text/plain' } }))));
});

self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || 'Homelab Agents', {
    body: data.body || '', tag: data.tag || undefined, renotify: !!data.tag, data: { url: data.url || '/ui/' },
    icon: '/ui/icons/icon-192.png', badge: '/ui/icons/icon-192.png',
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || '/ui/', self.location.origin);
  if (target.origin !== self.location.origin || !target.pathname.startsWith('/ui')) return;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const open = list.find(c => new URL(c.url).pathname.startsWith('/ui'));
    if (open) { open.postMessage({ type: 'open', url: target.href }); return open.focus(); }
    return self.clients.openWindow(target.href);
  }));
});
