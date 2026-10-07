import { useEffect, useRef, useState } from 'react';
import type { FlashAnswer, Step } from '../api';
import { digitOf, isConfirmKey, targetControl, useShortcuts } from '../keys';
import { CardBack } from './CardBack';

interface FlashProps {
  step: Step;
  disabled: boolean;
  onAnswer: (answer: FlashAnswer) => void;
}

const GRADES: { answer: FlashAnswer; label: string; tone: string }[] = [
  { answer: 'again', label: 'Again', tone: 'grade-again' },
  { answer: 'remembered', label: 'Got it', tone: 'grade-good' },
];

/**
 * A flash card: recall the answer, flip (click, Enter or Space), then say
 * honestly whether it came back. Keys after the flip: 1 again, 2 got it.
 */
export function Flash({ step, disabled, onAnswer }: FlashProps) {
  const [flipped, setFlipped] = useState(false);
  const back = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // The flip button disappears; put focus on what replaced it so keyboard
    // and screen-reader users land on the answer.
    if (flipped) back.current?.focus();
  }, [flipped]);

  useShortcuts(event => {
    if (!flipped) {
      if (isConfirmKey(event) && !targetControl(event)) {
        event.preventDefault();
        setFlipped(true);
      }
      return;
    }
    const digit = digitOf(event);
    if (digit < 1 || digit > GRADES.length) return;
    event.preventDefault();
    onAnswer(GRADES[digit - 1].answer);
  }, !disabled);

  if (!flipped) {
    return (
      <button type="button" className="flash-front" disabled={disabled} onClick={() => setFlipped(true)}>
        <span className="flash-front-label">Show answer</span>
        <span className="flash-front-sub" aria-hidden="true">Recall it first, then flip</span>
      </button>
    );
  }
  return (
    <>
      <CardBack step={step} ref={back} focusable />
      <p className="grades-question muted">Did you remember it?</p>
      <div className="grades">
        {GRADES.map((grade, index) => (
          <button key={grade.answer} type="button" className={`grade ${grade.tone}`} disabled={disabled}
            aria-keyshortcuts={String(index + 1)} onClick={() => onAnswer(grade.answer)}>
            <kbd className="key" aria-hidden="true">{index + 1}</kbd>
            <span>{grade.label}</span>
          </button>
        ))}
      </div>
    </>
  );
}
