import type { Step, TriageAnswer } from '../api';
import { digitOf, useShortcuts } from '../keys';
import { CardBack } from './CardBack';

interface TriageProps {
  step: Step;
  disabled: boolean;
  onAnswer: (answer: TriageAnswer) => void;
}

const CHOICES: { answer: TriageAnswer; label: string; tone: string }[] = [
  { answer: 'dont_know', label: "Don't know", tone: 'grade-again' },
  { answer: 'know', label: 'I know', tone: 'grade-good' },
];

/**
 * First meeting with a card: prompt and answer are both shown, and the
 * learner says whether they already know it. "I know" goes straight to a
 * check; "Don't know" shows the card as a flash card first. Keys: 1, 2.
 */
export function Triage({ step, disabled, onAnswer }: TriageProps) {
  useShortcuts(event => {
    const digit = digitOf(event);
    if (digit < 1 || digit > CHOICES.length) return;
    event.preventDefault();
    onAnswer(CHOICES[digit - 1].answer);
  }, !disabled);

  return (
    <>
      <CardBack step={step} />
      <div className="grades">
        {CHOICES.map((choice, index) => (
          <button key={choice.answer} type="button" className={`grade ${choice.tone}`} disabled={disabled}
            aria-keyshortcuts={String(index + 1)} onClick={() => onAnswer(choice.answer)}>
            <kbd className="key" aria-hidden="true">{index + 1}</kbd>
            <span>{choice.label}</span>
          </button>
        ))}
      </div>
    </>
  );
}
