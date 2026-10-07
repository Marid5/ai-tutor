import { useState } from 'react';
import type { Step, StepKind } from '../api';
import { Assemble } from './Assemble';
import { Choice } from './Choice';
import { Cloze } from './Cloze';
import { Flash } from './Flash';
import { Triage } from './Triage';

/** What the learner is asked to do, per kind. */
export const TASK_LABEL: Record<StepKind, string> = {
  triage: 'Do you know this?',
  flash: 'Recall the answer',
  choice: 'Choose the answer',
  cloze: 'Fill in the gap',
  assemble: 'Build the answer',
};

/** Keyboard help, shown only on devices with a fine pointer (see styles.css). */
const KEYS_HELP: Record<StepKind, string> = {
  triage: '1 don’t know · 2 I know',
  flash: 'Enter flip · then 1 again · 2 got it',
  choice: '1–4 select · Enter answer',
  cloze: '1–4 select · Enter answer',
  assemble: '1–9 add a word · Backspace remove · Enter check',
};

export const CLOSED_KINDS: ReadonlySet<StepKind> = new Set(['choice', 'cloze', 'assemble']);

interface StepViewProps {
  step: Step;
  /** Inputs are locked while an answer is in flight, graded or out of sync. */
  locked: boolean;
  /** The answer the learner gave on this step, once given. */
  chosen: string | null;
  /** The server's verdict on a closed step; null until it arrives. */
  verdict: boolean | null;
  showHintByDefault: boolean;
  onAnswer: (answer: string) => void;
}

/** One exercise, rendered from the server's step payload. */
export function StepView({ step, locked, chosen, verdict, showHintByDefault, onAnswer }: StepViewProps) {
  const [hintShown, setHintShown] = useState(false);
  const known = step.kind in TASK_LABEL;

  return (
    <>
      <div className="step-head">
        <p className="eyebrow">{known ? TASK_LABEL[step.kind] : 'Exercise'}</p>
        {(step.repeat ?? 0) > 0 && <span className="chip chip-warn">Another try</span>}
      </div>
      <h1 tabIndex={-1} className="prompt">{step.prompt}</h1>
      {step.hint && (showHintByDefault || hintShown
        ? <p className="hint"><span className="hint-label">Hint</span> {step.hint}</p>
        : <button type="button" className="link hint-toggle" onClick={() => setHintShown(true)}>Show hint</button>)}

      <div className="step-body">
        {step.kind === 'triage' && <Triage step={step} disabled={locked} onAnswer={onAnswer} />}
        {step.kind === 'flash' && <Flash step={step} disabled={locked} onAnswer={onAnswer} />}
        {step.kind === 'choice' && (
          <Choice options={step.options ?? []} disabled={locked} onAnswer={onAnswer} chosen={chosen} verdict={verdict} />
        )}
        {step.kind === 'cloze' && <Cloze step={step} disabled={locked} onAnswer={onAnswer} chosen={chosen} verdict={verdict} />}
        {step.kind === 'assemble' && <Assemble step={step} disabled={locked} onAnswer={onAnswer} />}
        {!known && <p className="notice">This exercise needs a newer version of the app. Reload the page to update it.</p>}
      </div>
      {known && !locked && <p className="keys-help" aria-hidden="true">{KEYS_HELP[step.kind]}</p>}
    </>
  );
}
