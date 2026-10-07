import type { Step } from '../api';
import { Choice } from './Choice';

interface ClozeProps {
  step: Step;
  disabled: boolean;
  onAnswer: (option: string) => void;
  chosen: string | null;
  verdict: boolean | null;
}

/** The answer with its key cut out; the gap is filled from the options (keys 1-9, Enter). */
export function Cloze({ step, disabled, onAnswer, chosen, verdict }: ClozeProps) {
  return (
    <>
      <p className="cloze-text">
        {step.prefix}
        {chosen === null
          ? <span className="gap" role="img" aria-label="Gap" />
          : <span className="gap is-filled" data-state={verdict === true ? 'right' : verdict === false ? 'wrong' : 'chosen'}>{chosen}</span>}
        {step.suffix}
      </p>
      <Choice options={step.options ?? []} disabled={disabled} onAnswer={onAnswer} chosen={chosen} verdict={verdict}
        label="Options for the gap" />
    </>
  );
}
