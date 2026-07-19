import { describe, expect, it, vi } from "vitest";
import {
  THEME_STORAGE_KEY,
  applyTheme,
  isThemeMode,
  resolveInitialTheme,
  toggleTheme,
} from "./theme";

describe("theme composable", () => {
  it("validates theme mode strings", () => {
    expect(isThemeMode("light")).toBe(true);
    expect(isThemeMode("dark")).toBe(true);
    expect(isThemeMode("auto")).toBe(false);
    expect(isThemeMode(null)).toBe(false);
  });

  it("resolves saved theme from localStorage", () => {
    const storage = { getItem: vi.fn(() => "dark") };
    expect(resolveInitialTheme(storage, false)).toBe("dark");
  });

  it("falls back to OS preference when storage empty", () => {
    const storage = { getItem: vi.fn(() => null) };
    expect(resolveInitialTheme(storage, true)).toBe("dark");
    expect(resolveInitialTheme(storage, false)).toBe("light");
  });

  it("toggles between light and dark", () => {
    expect(toggleTheme("light")).toBe("dark");
    expect(toggleTheme("dark")).toBe("light");
  });

  it("applyTheme sets data-theme and persists", () => {
    const root = document.createElement("html");
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", { setItem, getItem: vi.fn() });

    applyTheme("dark", root);

    expect(root.getAttribute("data-theme")).toBe("dark");
    expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, "dark");
    vi.unstubAllGlobals();
  });
});
