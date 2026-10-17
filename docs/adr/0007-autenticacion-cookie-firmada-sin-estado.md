# 0007. Autenticación opcional con cookie firmada sin estado

- Estado: aceptada, con limitaciones conocidas
- Fecha: WP-16 (SPEC ADM-03, §14); auditoría de WP-16

## Contexto

La aplicación es de un usuario o de un hogar. La autenticación es opcional y se activa para acceder desde la LAN.
No hay cuentas ni base de datos de sesiones, y la especificación pide cookie `HttpOnly; SameSite=Strict`, token
CSRF y límite de intentos de login.

## Decisión

- `A0TD_AUTH_MODE`: `none` (por defecto), `password` (cookie de sesión para la UI) o `token` (Bearer, solo para API
  y CLI; la UI lo avisa).
- Sesión: cookie firmada con HMAC y sin estado en el servidor, válida 12 h. La clave es `A0TD_SECRET_KEY`
  (≥ 32 bytes); si falta, se genera y se persiste en `/data`.
- Contraseña: hash Argon2 en `A0TD_PASSWORD_HASH`.
- Token CSRF en las escrituras. Límite de intentos de login (`RATE_LIMITED`). Si la aplicación está detrás de un
  proxy, solo se confía en las cabeceras `X-Forwarded-*` de `A0TD_TRUSTED_PROXIES`.
- Una configuración de autenticación incompleta o inválida **impide arrancar** (fallo seguro).
- `/metrics` exige sesión o token cuando la autenticación está activa (`A0TD_METRICS`).

## Alternativas consideradas

- **Sesiones en base de datos**: permitirían invalidar sesiones en el servidor; se descartó por simplicidad para un
  usuario local.
- **Cuentas multiusuario**: fuera de alcance (D4).

## Consecuencias

- **Limitación conocida:** el logout solo borra la cookie. Una cookie copiada sigue siendo válida hasta caducar a las
  12 h. Está marcado para revisar en la auditoría.
- Cambiar `A0TD_SECRET_KEY` invalida todas las sesiones existentes.
- Una contraseña incorrecta devuelve `INVALID_CREDENTIALS`.
