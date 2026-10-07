import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import * as api from '../api';
import type { AnswerEvent, AnswersResponse, SessionMode, Step, StudySession } from '../api';
import { ActiveStepTimer } from '../activeTime';
import { CardBack } from '../components/CardBack';
import { Icon } from '../components/Icon';
import { ProgressBar } from '../components/ProgressBar';
import { CLOSED_KINDS, StepView } from '../components/StepView';
import { isConfirmKey, targetControl, useShortcuts } from '../keys';
import { count } from '../plural';
import { Done } from './Done';

export interface SessionProps {
  /** The session as the server opened it; `steps: []` means there is nothing left to do. */
  session: StudySession;
  /** Leave the session; the home screen reloads its counts. */
  onExit: () => void;
}

/** How `elapsed_ms` was measured: active, idle-capped time on the step (see activeTime.ts). */
export const TIMING_VERSION = 1;
/** The server refuses `elapsed_ms` above five minutes. */
const ELAPSED_MS_MAX = 5 * 60 * 1000;

/**
 * Where the step on screen is. The server is the source of truth: the screen
 * only moves on to a step the server sent, and a closed step shows only the
 * verdict the server computed.
 *
 *   answering ──answer──▶ pending ──result──▶ graded (closed kinds) ──Next──▶ answering (server's next step)
 *                            │        └─────▶ answering (self-graded kinds: straight to the server's next step)
 *                            │        └─────▶ answering + notice (stale: the returned session replaces this one)
 *                            └─fail─▶ out-of-sync ──Sync and continue (GET /api/session)──▶ answering
 *                            └─401──▶ signed-out (the app switches to sign-in; "Sign in" is the fallback)
 */
type Phase =
  | { name: 'answering' }
  | { name: 'pending'; answer: string }
  | { name: 'graded'; answer: string; correct: boolean | null; next: StudySession; conflict: boolean }
  | {
      name: 'out-of-sync';
      title: string;
      message: string;
      /** The answer the learner gave, still marked on screen. */
      answer: string | null;
      /** A step the server kept handing back after it was answered; syncing must not land on it again. */
      loopingStep: string | null;
      syncing: boolean;
    }
  | { name: 'signed-out' };

const LEAVE_LABEL: Record<SessionMode, string> = {
  lesson: 'Leave lesson',
  lesson_practice: 'Leave practice',
  mixed_practice: 'Leave practice',
  scheduled_review: 'Leave review',
};

const STALE_NOTICE = 'That exercise was updated, so it was skipped.';
const NOT_SAVED = 'Your answer may not have been saved';
const OUT_OF_SYNC = 'Out of sync';
const CONFLICT_MESSAGE = 'The server returned the same exercise again.';
const NO_CONTINUATION = 'The server did not send the next exercise.';

const VERDICT_TITLE = (correct: boolean | null) => (correct === true ? 'Correct' : correct === false ? 'Not quite' : 'Answer saved');

const isSignedOut = (error: unknown) => error instanceof api.ApiError && error.status === 401;

/**
 * A held-down Enter repeats keydown; on a freshly focused Next or Sync button
 * that would skip the verdict the learner has not read yet.
 */
const ignoreRepeat = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
  if (event.repeat) event.preventDefault();
};

/** The study session: one step at a time, as the server hands them out, then the Done screen. */
export function Session({ session: opened, onExit }: SessionProps) {
  const [session, setSession] = useState(opened);
  const [phase, setPhaseState] = useState<Phase>({ name: 'answering' });
  const [notice, setNotice] = useState('');
  // null until /api/settings answers: no hint and no "Show hint" button yet,
  // so the hint never flickers in and out.
  const [hintsByDefault, setHintsByDefault] = useState<boolean | null>(null);
  // Bumped on every move, so a step the server serves again under the same id
  // (after a sync) starts fresh: new inputs, new clock.
  const [attempt, setAttempt] = useState(0);
  const [tally, setTally] = useState({ answered: 0, checks: 0, correct: 0 });

  // The phase is also kept in a ref: a second click or key press in the same
  // tick must see that an answer is already on its way.
  const phaseRef = useRef<Phase>(phase);
  const setPhase = (next: Phase) => {
    phaseRef.current = next;
    setPhaseState(next);
  };

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const timerRef = useRef<ActiveStepTimer | null>(null);
  if (timerRef.current === null) timerRef.current = new ActiveStepTimer();
  const timer = timerRef.current;

  const heading = useRef<HTMLHeadingElement>(null);
  const nextButton = useRef<HTMLButtonElement>(null);
  const syncButton = useRef<HTMLButtonElement>(null);
  const feedbackTitle = useId();

  const step: Step | undefined = session.steps[0];
  const stepKey = step ? `${step.id}:${attempt}` : 'done';

  useEffect(() => {
    let cancelled = false;
    api.getSettings().then(
      settings => { if (!cancelled) setHintsByDefault(settings.show_hint_by_default); },
      () => { if (!cancelled) setHintsByDefault(false); }, // hints stay behind the button
    );
    return () => { cancelled = true; };
  }, []);

  // Active time: only while the page is visible and focused, idle-capped.
  useEffect(() => {
    const visibility = () => timer.setVisible(!document.hidden);
    const focus = () => timer.setFocused(true);
    const blur = () => timer.setFocused(false);
    const interact = () => timer.interact();
    visibility();
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('focus', focus);
    window.addEventListener('blur', blur);
    window.addEventListener('pointerdown', interact, true);
    window.addEventListener('keydown', interact, true);
    return () => {
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('focus', focus);
      window.removeEventListener('blur', blur);
      window.removeEventListener('pointerdown', interact, true);
      window.removeEventListener('keydown', interact, true);
    };
  }, [timer]);

  // A new step restarts its clock.
  useEffect(() => {
    timer.start();
  }, [stepKey, timer]);

  // Focus follows the session: the prompt of the step the server sent (or the
  // Done heading) after moving on, so screen readers announce it and Enter
  // works from there; the Next or Sync button when one appears. The app
  // itself focuses the first heading when the session opens.
  const focusHeading = useRef(false);
  useEffect(() => {
    if (phase.name === 'graded') nextButton.current?.focus();
    if (phase.name === 'out-of-sync' && !phase.syncing) syncButton.current?.focus();
    if (phase.name === 'answering' && focusHeading.current) {
      focusHeading.current = false;
      heading.current?.focus();
    }
  }, [phase, session]);

  const advance = (next: StudySession) => {
    focusHeading.current = true;
    setAttempt(n => n + 1);
    setSession(next);
    setPhase({ name: 'answering' });
    window.scrollTo(0, 0);
  };

  const outOfSync = (message: string, answer: string | null, loopingStep: string | null = null, title = OUT_OF_SYNC) => {
    setPhase({ name: 'out-of-sync', title, message, answer, loopingStep, syncing: false });
  };

  const handleResponse = (answered: Step, answer: string, response: AnswersResponse) => {
    const outcome = response.results[0];
    const next = response.session;
    if (outcome?.rejected === 'stale') {
      // The step is no longer served (its card or the course settings
      // changed). The server already sent the session as it is now.
      if (next && next.steps[0]?.id !== answered.id) {
        advance(next);
        setNotice(STALE_NOTICE);
      } else {
        outOfSync(api.asSentence(outcome.detail), answer, answered.id);
      }
      return;
    }
    if (!outcome || !outcome.accepted) {
      outOfSync(api.asSentence(outcome?.detail), answer);
      return;
    }
    if (!next) {
      outOfSync(NO_CONTINUATION, answer);
      return;
    }
    const conflict = response.state_conflict || next.steps[0]?.id === answered.id;
    const graded = CLOSED_KINDS.has(answered.kind);
    setTally(t => ({
      answered: t.answered + 1,
      checks: t.checks + (graded && outcome.correct !== null ? 1 : 0),
      correct: t.correct + (graded && outcome.correct === true ? 1 : 0),
    }));
    if (graded) setPhase({ name: 'graded', answer, correct: outcome.correct, next, conflict });
    else if (conflict) outOfSync(CONFLICT_MESSAGE, answer, answered.id);
    else advance(next);
  };

  const submit = async (answer: string) => {
    if (!step || phaseRef.current.name !== 'answering') return;
    setNotice('');
    setPhase({ name: 'pending', answer });
    const event: AnswerEvent = {
      session_id: session.session_id,
      step_id: step.id,
      card_id: step.card_id,
      kind: step.kind,
      answer,
      elapsed_ms: Math.min(ELAPSED_MS_MAX, timer.elapsed()),
      timing_version: TIMING_VERSION,
      ts: new Date().toISOString(),
    };
    let response: AnswersResponse;
    try {
      response = await api.sendAnswers([event]);
    } catch (error) {
      if (!mounted.current) return;
      if (isSignedOut(error)) setPhase({ name: 'signed-out' });
      else outOfSync(api.describeError(error), answer, null, NOT_SAVED);
      return;
    }
    if (mounted.current) handleResponse(step, answer, response);
  };

  const goNext = () => {
    const current = phaseRef.current;
    if (current.name !== 'graded') return;
    if (current.conflict) outOfSync(CONFLICT_MESSAGE, current.answer, step?.id ?? null);
    else advance(current.next);
  };

  /** Ask the server where the session stands and continue from there. */
  const sync = async () => {
    const current = phaseRef.current;
    if (current.name !== 'out-of-sync' || current.syncing) return;
    setPhase({ ...current, syncing: true });
    try {
      const fresh = await api.getSession(session.session_id);
      if (!mounted.current) return;
      if (current.loopingStep && fresh.steps[0]?.id === current.loopingStep) {
        setPhase({ ...current, message: CONFLICT_MESSAGE, syncing: false });
        return;
      }
      setNotice('');
      advance(fresh);
    } catch (error) {
      if (!mounted.current) return;
      if (isSignedOut(error)) setPhase({ name: 'signed-out' });
      else setPhase({ ...current, message: api.describeError(error), syncing: false });
    }
  };

  // Enter or Space moves on from a graded step (the focused Next button
  // handles its own keys).
  useShortcuts(event => {
    if (!isConfirmKey(event) || targetControl(event)) return;
    event.preventDefault();
    goNext();
  }, phase.name === 'graded');

  if (!step) {
    return (
      <Done headingRef={heading} mode={session.mode} resolved={session.resolved_cards} total={session.total_cards}
        answered={tally.answered} checks={tally.checks} correct={tally.correct} onExit={onExit} />
    );
  }

  const closed = CLOSED_KINDS.has(step.kind);
  // Once graded, the bar already shows where the server says the session is.
  const progress = phase.name === 'graded' ? phase.next : session;
  const total = progress.total_cards;
  const resolved = Math.min(progress.resolved_cards, total);
  const chosen = phase.name === 'pending' || phase.name === 'graded' || phase.name === 'out-of-sync' ? phase.answer : null;
  // One live region, always mounted, so assistive tech announces each change.
  let announcement = '';
  if (phase.name === 'pending') announcement = closed ? 'Checking your answer…' : 'Saving…';
  else if (phase.name === 'graded') announcement = `${VERDICT_TITLE(phase.correct)}. ${step.answer}`;
  else if (phase.name === 'answering') announcement = notice;
  const verdict = phase.name === 'graded' ? phase.correct : null;

  return (
    <div className="session" data-step={step.id} data-card={step.card_id} data-kind={step.kind}
      data-phase={phase.name}>
      <div className="session-bar">
        <button type="button" className="icon-button" aria-label={LEAVE_LABEL[session.mode]} onClick={onExit}>
          <Icon name="close" />
        </button>
        <span className="session-count">{resolved} / {count(total, 'card')}</span>
        <ProgressBar value={resolved} max={total} label="Cards done" size="sm" />
      </div>
      <p className="session-title"><span className="chip">{session.title}</span></p>
      <p className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{announcement}</p>
      {notice && <p className="notice">{notice}</p>}

      <StepView key={stepKey} headingRef={heading} step={step} locked={phase.name !== 'answering'} chosen={chosen} verdict={verdict}
        showHintByDefault={hintsByDefault} onAnswer={answer => void submit(answer)} />

      {phase.name === 'pending' && (
        <p className="pending">
          <span className="spinner" aria-hidden="true" />
          <span>{closed ? 'Checking your answer…' : 'Saving…'}</span>
        </p>
      )}

      {phase.name === 'graded' && (
        <section className={`feedback ${phase.correct === true ? 'is-right' : phase.correct === false ? 'is-wrong' : ''}`}
          aria-labelledby={feedbackTitle}>
          <h2 id={feedbackTitle} className="feedback-title">{VERDICT_TITLE(phase.correct)}</h2>
          <CardBack step={step} />
          {phase.correct === false && <p className="feedback-text">This card will come back for another try.</p>}
          <button type="button" className="button primary" ref={nextButton} onClick={goNext}
            onKeyDown={ignoreRepeat} aria-describedby={feedbackTitle}>
            <span>Next</span>
            <Icon name="arrow" />
          </button>
        </section>
      )}

      {phase.name === 'out-of-sync' && (
        <section className="feedback is-error" role="alert">
          <h2 className="feedback-title">{phase.title}</h2>
          <p className="feedback-text">{phase.message}</p>
          <button type="button" className="button primary" ref={syncButton} disabled={phase.syncing}
            onKeyDown={ignoreRepeat} onClick={() => void sync()}>
            Sync and continue
          </button>
          <button type="button" className="button secondary" onClick={onExit}>Back to home</button>
        </section>
      )}

      {phase.name === 'signed-out' && (
        <section className="feedback is-error" role="alert">
          <h2 className="feedback-title">You are signed out</h2>
          <p className="feedback-text">Sign in again to continue. Answers the server confirmed are saved.</p>
          <button type="button" className="button primary" onClick={onExit}>Sign in</button>
        </section>
      )}
    </div>
  );
}
