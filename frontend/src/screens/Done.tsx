import type { SessionMode } from '../api';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { plural } from '../plural';

export interface DoneProps {
  mode: SessionMode;
  /** Cards the server counts as resolved, out of the cards in the session. */
  resolved: number;
  total: number;
  /** Steps answered in this sitting (all kinds). */
  answered: number;
  /** Closed checks graded by the server in this sitting, and how many were right. */
  checks: number;
  correct: number;
  onExit: () => void;
}

const FINISHED: Record<SessionMode, string> = {
  lesson: 'Lesson complete',
  lesson_practice: 'Practice complete',
  mixed_practice: 'Practice complete',
  scheduled_review: 'Review complete',
};

const NOTHING_LEFT: Record<SessionMode, string> = {
  lesson: 'Lesson complete',
  lesson_practice: 'Nothing to practice',
  mixed_practice: 'Nothing to practice',
  scheduled_review: 'Nothing to review',
};

/** The end of a session: what was done in this sitting. Progress is already saved on the server. */
export function Done({ mode, resolved, total, answered, checks, correct, onExit }: DoneProps) {
  const empty = answered === 0;
  return (
    <section className="done">
      <span className="done-mark" aria-hidden="true"><Icon name="check" /></span>
      <h1 tabIndex={-1}>{empty ? NOTHING_LEFT[mode] : FINISHED[mode]}</h1>
      <p className="lede">
        {empty && mode !== 'lesson'
          ? 'You are all caught up. New reviews appear as cards come due.'
          : 'Your progress is saved.'}
      </p>
      {!empty && (
        <div className="stats">
          <Stat value={checks} label={plural(checks, 'check')} />
          <Stat value={checks ? `${Math.round((correct / checks) * 100)}%` : '—'} label="correct" />
          <Stat value={`${Math.min(resolved, total)}/${total}`} label={plural(total, 'card')} />
        </div>
      )}
      <button type="button" className="button primary" onClick={onExit}>Back to home</button>
    </section>
  );
}
