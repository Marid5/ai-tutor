// Small stroke icons drawn inline so they follow the text colour (currentColor)
// in both themes. Decorative only: the text next to them carries the meaning.

const PATHS = {
  home: 'M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z',
  chart: 'M5 19V11M12 19V5M19 19v-6M3.5 19.5h17',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.6-2-3.4-2.4 1a7.4 7.4 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.5A7.4 7.4 0 0 0 7 6.5l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 0 0 0 3l-2 1.6 2 3.4 2.4-1a7.4 7.4 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.4 7.4 0 0 0 2.6-1.5l2.4 1 2-3.4z',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  review: 'M20 11a8 8 0 1 0-2.3 5.7M20 4.5V11h-6.5',
  practice: 'M4 8h13M14 4.5 17.5 8 14 11.5M20 16H7M10 12.5 6.5 16l3.5 3.5',
  arrow: 'M5 12h14M13 6l6 6-6 6',
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={className ? `icon ${className}` : 'icon'} viewBox="0 0 24 24" width="20" height="20"
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false">
      <path d={PATHS[name]} />
    </svg>
  );
}

/** The product mark: a card with a check on it. */
export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 64 64" width="28" height="28" aria-hidden="true" focusable="false">
      <rect width="64" height="64" rx="14" className="logo-bg" />
      <rect x="15" y="19" width="30" height="22" rx="4" className="logo-back" />
      <rect x="20" y="24" width="30" height="22" rx="4" className="logo-card" />
      <path d="M28 35.5l4 4 8-8.5" fill="none" className="logo-tick" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
