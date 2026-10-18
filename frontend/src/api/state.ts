let csrfToken: string | null = null;

/** CSRF token of the current session (password auth); sent as `X-CSRF-Token` on writes. */
export const setCsrfToken = (token: string | null) => {
  csrfToken = token;
};
export const getCsrfToken = () => csrfToken;

type AuthListener = () => void;
let authListener: AuthListener | null = null;

/** Called when any request is answered with 401 `AUTH_REQUIRED` (the guard then goes to /login). */
export const onAuthRequired = (listener: AuthListener | null) => {
  authListener = listener;
};
export const notifyAuthRequired = () => authListener?.();
