import { useEffect, useRef } from 'react';

// Keyboard shortcuts for the study session: digits pick, Enter/Space confirm.
// They listen on the window, so they work wherever focus is, but never steal
// keys typed into a field or combined with a modifier (browser shortcuts).

/** A key press that may act as a shortcut. */
export function isShortcut(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || event.repeat || event.isComposing) return false;
  if (event.altKey || event.ctrlKey || event.metaKey) return false;
  const target = event.target;
  if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) {
    return false;
  }
  return true;
}

/** The 1-based digit of a key press ("1".."9"), or 0. */
export function digitOf(event: KeyboardEvent): number {
  return /^[1-9]$/.test(event.key) ? Number(event.key) : 0;
}

export const isConfirmKey = (event: KeyboardEvent) => event.key === 'Enter' || event.key === ' ';

/**
 * The button (or link) a key press lands on, if any. Enter and Space on a
 * focused button already click it, so a window-level shortcut must leave
 * those alone or the action would run twice.
 */
export function targetControl(event: KeyboardEvent): HTMLElement | null {
  const target = event.target;
  return target instanceof Element ? target.closest<HTMLElement>('button, a[href], [role="button"]') : null;
}

/** Call `handler` for shortcut key presses while `enabled`; the latest handler is always used. */
export function useShortcuts(handler: (event: KeyboardEvent) => void, enabled = true) {
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(() => {
    if (!enabled) return undefined;
    const listener = (event: KeyboardEvent) => {
      if (isShortcut(event)) latest.current(event);
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [enabled]);
}
