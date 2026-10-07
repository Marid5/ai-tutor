import type { Ref } from 'react';
import type { SessionMode } from '../api';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { count, plural } from '../plural';

export interface DoneProps {
  /** Receives the heading, so the session can move focus to it. */
  headingRef?: Ref<HTMLHeadingElement>;
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
export function Done({ headingRef, mode, resolved, total, answered, checks, correct, onExit }: DoneProps) {
  const empty = answered === 0;
  return (
    <section className="done">
      <span className="done-mark" aria-hidden="true"><Icon name="check" /></span>
      <h1 tabIndex={-1} ref={headingRef}>{empty ? NOTHING_LEFT[mode] : FINISHED[mode]}</h1>
      <p className="lede">
        {empty && mode !== 'lesson'
          ? 'You are all caught up. New reviews appear as cards come due.'
          : `Your progress is saved: ${Math.min(resolved, total)} of ${count(total, 'card')} done.`}
      </p>
      {!empty && (
        // Counted in this browser since the session screen opened; a reload
        // starts a new sitting. The server keeps the full history.
        <section className="sitting" aria-labelledby="sitting-title">
          <h2 id="sitting-title" className="eyebrow">This sitting</h2>
          <div className="stats stats-2">
            <Stat value={checks} label={plural(checks, 'check')} />
            <Stat value={checks ? `${Math.round((correct / checks) * 100)}%` : '—'} label="correct" />
          </div>
        </section>
      )}
      <button type="button" className="button primary" onClick={onExit}>Back to home</button>
    </section>
  );
}
