# WP-16 audit (auth, presets, settings, history, ING-10)
Tests run: tests/api/test_auth.py, test_presets_settings.py, test_history.py -> 24 passed.
Findings:
- api/auth.py:42-50 load_secret_key: existing empty/short secret.key is accepted -> empty HMAC key (forgeable cookies). Validate length, regenerate.
- api/routers/auth.py:35 _client_ip: request.client.host; behind a reverse proxy all users share one bucket (global lockout DoS). uvicorn trusts X-Forwarded-For only from 127.0.0.1; no A0TD setting. Also successful logins consume attempts.
- api/routers/documents.py:217 dedupe: an upload with no password reuses a document previously unlocked with a password (decrypted content served without password).
- api/auth.py:19-24,142: /api/docs and /api/openapi.json are open (not under /api/v1/).
- api/auth.py:115 _attempts dict mutated from threadpool threads without lock (iteration during cleanup may raise).
- login/logout exempt from CSRF (login CSRF/logout CSRF; SameSite=Strict mitigates). Stateless logout (approved deviation).
- Token mode: SPA has no handling (all calls 401); EventSource/window.open cannot send Bearer. Intended for API/CLI.
- Tests missing: dedupe vs expired/non-ready docs, CSRF on upload, token-mode open paths/CSRF-not-required, X-Forwarded-Proto.
Checked OK: HMAC compare_digest on sig, CSRF and token; expiry check; exp covered by signature; argon2 single fixed hash (no user enumeration); 401 before body read; CSRF header on fetch+XHR; GET/SSE exempt; preset import validated via merge_settings (extra=forbid), document_id stripped, new id always; LIKE escaping; dedupe filters expires_at/status/meta.json.
