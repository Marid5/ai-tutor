// The colour theme. "system" follows the operating system through CSS media
// queries; "light" and "dark" pin the palette with a data attribute on <html>.
// The preference is applied from the bundle before React renders, because the
// Content-Security-Policy forbids an inline script in index.html.

export type ThemePreference = 'system' | 'light' | 'dark';

export const THEME_STORAGE_KEY = 'ai-tutor.theme';
export const THEME_OPTIONS: readonly ThemePreference[] = ['system', 'light', 'dark'];

// Browser chrome colour (address bar, PWA title bar); matches --bg in styles.css.
const CHROME_COLOR = { light: '#f4f6f8', dark: '#0e141a' } as const;

function isTheme(value: unknown): value is ThemePreference {
  return typeof value === 'string' && (THEME_OPTIONS as readonly string[]).includes(value);
}

/** The saved preference, or "system" when none is saved or storage is unavailable. */
export function readTheme(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(value) ? value : 'system';
  } catch {
    return 'system';
  }
}

/** Remember the preference; silently skipped when storage is unavailable. */
export function saveTheme(theme: ThemePreference): void {
  try {
    if (theme === 'system') window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Private browsing or blocked storage: the choice lasts for this page only.
  }
}

export function applyTheme(theme: ThemePreference, root: HTMLElement = document.documentElement): void {
  if (theme === 'system') delete root.dataset.theme;
  else root.dataset.theme = theme;

  // index.html carries one theme-color per colour scheme; a pinned theme sets both.
  const metas = document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]');
  metas.forEach(meta => {
    const scheme = (meta.getAttribute('media') ?? '').includes('dark') ? 'dark' : 'light';
    meta.setAttribute('content', CHROME_COLOR[theme === 'system' ? scheme : theme]);
  });
}
