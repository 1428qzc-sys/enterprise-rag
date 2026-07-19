export const THEME_STORAGE_KEY = "erag-theme";
export type ThemeMode = "light" | "dark";

export function isThemeMode(value: string | null): value is ThemeMode {
  return value === "light" || value === "dark";
}

/** Prefer saved theme; else match OS; default light. */
export function resolveInitialTheme(
  storage: Pick<Storage, "getItem"> = localStorage,
  prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches
): ThemeMode {
  const saved = storage.getItem(THEME_STORAGE_KEY);
  if (isThemeMode(saved)) return saved;
  return prefersDark ? "dark" : "light";
}

export function applyTheme(theme: ThemeMode, root: HTMLElement = document.documentElement): void {
  root.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}

export function toggleTheme(current: ThemeMode): ThemeMode {
  return current === "dark" ? "light" : "dark";
}
