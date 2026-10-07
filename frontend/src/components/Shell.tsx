import type { ReactNode } from 'react';
import { Icon, Logo, type IconName } from './Icon';

export type Tab = 'home' | 'progress' | 'settings';

const TABS: { id: Tab; label: string; icon: IconName }[] = [
  { id: 'home', label: 'Home', icon: 'home' },
  { id: 'progress', label: 'Progress', icon: 'chart' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

interface ShellProps {
  children: ReactNode;
  /** The highlighted tab; omit together with `onNavigate` for screens without navigation. */
  tab?: Tab;
  onNavigate?: (tab: Tab) => void;
  /** Shown next to the brand on wide screens. */
  courseTitle?: string;
}

/** Page frame: brand header, main column and the tab bar (bottom on phones, in the header on wide screens). */
export function Shell({ children, tab, onNavigate, courseTitle }: ShellProps) {
  return (
    <div className={onNavigate ? 'shell has-tabs' : 'shell'}>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <Logo />
            <span className="brand-name">AI Tutor</span>
            {courseTitle && <span className="brand-course">{courseTitle}</span>}
          </div>
          {onNavigate && (
            <nav className="tabs" aria-label="Main">
              {TABS.map(item => (
                <button key={item.id} type="button" className="tab"
                  aria-current={tab === item.id ? 'page' : undefined}
                  onClick={() => onNavigate(item.id)}>
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                </button>
              ))}
            </nav>
          )}
        </div>
      </header>
      <main className="content">{children}</main>
    </div>
  );
}
