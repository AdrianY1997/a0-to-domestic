export function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage may be unavailable (private mode, blocked); ignore.
  }
}

export function safeRemove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore: see safeSet.
  }
}
