/*
 * Service worker of the web version (SPEC 17bis). The Vite plugin in vite.config.ts fills in
 * BUILD_ID and PRECACHE at build time and emits this file as `sw.js`.
 *
 *  - App shell (index.html + hashed assets): precached per build. Pages are network-first (a
 *    deploy is picked up at once; offline they fall back to the cache) and hashed assets are
 *    cache-first. The previous build's cache is kept for tabs that are still open.
 *  - py/ (Pyodide, wheels, a0td.zip, ~38 MB): runtime cache, cache-first, kept across builds.
 *    Files are immutable per URL except py/manifest.json (network-first); a new bundle gets a new
 *    `?v=` URL, and older entries of the same file are dropped.
 */
const BUILD_ID = '__BUILD_ID__';
const PRECACHE = __PRECACHE__;

const SHELL_CACHE = `a0td-shell-${BUILD_ID}`;
const PY_CACHE = 'a0td-py-v1';
const scope = self.registration.scope;
const PY_PREFIX = new URL('py/', scope).pathname;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE.map((p) => new URL(p, scope).href)))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => {
        // Keep the previous build too: a tab opened before the update still needs its chunks.
        const shell = keys.filter((k) => k.startsWith('a0td-shell-'));
        const keep = new Set([SHELL_CACHE, ...shell.filter((k) => k !== SHELL_CACHE).slice(-1)]);
        return Promise.all(shell.filter((k) => !keep.has(k)).map((k) => caches.delete(k)));
      })
      .then(() => self.clients.claim()),
  );
});

async function putPy(cache, request, response) {
  await cache.put(request, response);
  // A new `?v=` replaces the old copy of the same file.
  const url = new URL(request.url);
  if (!url.search) return;
  for (const other of await cache.keys()) {
    const o = new URL(other.url);
    if (o.pathname === url.pathname && o.search !== url.search) await cache.delete(other);
  }
}

async function pyFetch(request) {
  const cache = await caches.open(PY_CACHE);
  const url = new URL(request.url);
  if (url.pathname.endsWith('/manifest.json')) {
    try {
      const fresh = await fetch(request);
      if (fresh.ok) await cache.put(request, fresh.clone());
      return fresh;
    } catch (e) {
      const stale = await cache.match(request);
      if (stale) return stale;
      throw e;
    }
  }
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok && response.status === 200) await putPy(cache, request, response.clone());
  return response;
}

async function remember(request, response) {
  if (response.ok && response.type === 'basic') {
    const cache = await caches.open(SHELL_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

async function shellFetch(request) {
  if (request.mode === 'navigate') {
    try {
      return await remember(request, await fetch(request));
    } catch (e) {
      const cached =
        (await caches.match(request)) || (await caches.match(new URL('index.html', scope).href));
      if (cached) return cached;
      throw e;
    }
  }
  const hit = await caches.match(request);
  if (hit) return hit;
  return remember(request, await fetch(request));
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith(new URL(scope).pathname)) {
    return;
  }
  if (request.headers.has('range')) return;
  if (url.pathname.startsWith(PY_PREFIX)) {
    event.respondWith(pyFetch(request));
  } else {
    event.respondWith(shellFetch(request));
  }
});

// The first visit boots Pyodide before this worker controls the page: the page lists what it
// downloaded and we cache it now (the browser HTTP cache usually serves it again).
self.addEventListener('message', (event) => {
  if (!event.data || event.data.type !== 'warm') return;
  event.waitUntil(
    caches.open(PY_CACHE).then(async (cache) => {
      for (const href of event.data.urls) {
        const url = new URL(href);
        if (url.origin !== self.location.origin || !url.pathname.startsWith(PY_PREFIX)) continue;
        if (await cache.match(href)) continue;
        try {
          const response = await fetch(href);
          if (response.ok && response.status === 200) {
            await putPy(cache, new Request(href), response);
          }
        } catch {
          // Best effort: the next normal request caches it.
        }
      }
    }),
  );
});
