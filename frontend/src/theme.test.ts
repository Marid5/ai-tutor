import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { THEME_STORAGE_KEY, applyTheme, readTheme, saveTheme } from './theme';

function themeColors() {
  return [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map(meta => meta.content);
}

beforeEach(() => {
  document.head.innerHTML = `
    <meta name="theme-color" content="#f4f6f8" media="(prefers-color-scheme: light)">
    <meta name="theme-color" content="#0e141a" media="(prefers-color-scheme: dark)">`;
  document.documentElement.removeAttribute('data-theme');
  localStorage.clear();
});

afterEach(() => { vi.restoreAllMocks(); });

describe('readTheme', () => {
  it('follows the system until the learner picks a theme', () => {
    expect(readTheme()).toBe('system');
  });

  it('returns a saved preference', () => {
    saveTheme('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(readTheme()).toBe('dark');
  });

  it('ignores a value it does not know', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'sepia');
    expect(readTheme()).toBe('system');
  });

  it('survives storage that throws (private mode, blocked cookies)', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied'); });
    expect(readTheme()).toBe('system');
    expect(() => saveTheme('light')).not.toThrow();
  });
});

describe('applyTheme', () => {
  it('pins an explicit theme on the root element and the browser chrome', () => {
    applyTheme('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(themeColors()).toEqual(['#0e141a', '#0e141a']);

    applyTheme('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(themeColors()).toEqual(['#f4f6f8', '#f4f6f8']);
  });

  it('hands control back to the system preference', () => {
    applyTheme('dark');
    applyTheme('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(themeColors()).toEqual(['#f4f6f8', '#0e141a']);
  });
});
