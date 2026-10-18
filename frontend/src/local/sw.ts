/** Service worker registration (offline use, SPEC 17bis). Never throws. */

export async function registerServiceWorker(): Promise<void> {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  try {
    await navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`, {
      scope: import.meta.env.BASE_URL,
    });
  } catch (e) {
    console.warn('[a0td] service worker registration failed', e);
  }
}

/**
 * The first visit boots Pyodide before the service worker controls the page, so its downloads
 * were not cached: hand it the list of `/py/` URLs the worker fetched.
 */
export function warmServiceWorker(urls: string[]): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator) || urls.length === 0) return;
  void navigator.serviceWorker.ready
    .then((reg) => reg.active?.postMessage({ type: 'warm', urls }))
    .catch(() => {});
}
