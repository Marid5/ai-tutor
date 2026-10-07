import { forwardRef } from 'react';
import type { Step } from '../api';

/** The back of a card: the full answer and, when the card has one, its note. */
export const CardBack = forwardRef<HTMLDivElement, { step: Step; focusable?: boolean }>(function CardBack({ step, focusable }, ref) {
  return (
    <div className="card-back" ref={ref} tabIndex={focusable ? -1 : undefined}>
      <p className="card-answer">{step.answer}</p>
      {step.note && <p className="card-note">{step.note}</p>}
    </div>
  );
});
