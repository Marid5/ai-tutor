import { useState } from 'react';
import type { Step } from '../api';
import { digitOf, targetControl, useShortcuts } from '../keys';

interface AssembleProps {
  step: Step;
  disabled: boolean;
  onAnswer: (answer: string) => void;
}

/**
 * Rebuild the answer from its own shuffled words. Keys: 1-9 take the n-th
 * remaining word, Backspace takes the last one back, Enter checks once every
 * word is placed. The server compares the text; the client only joins words.
 */
export function Assemble({ step, disabled, onAnswer }: AssembleProps) {
  const tiles = step.tiles ?? [];
  const [used, setUsed] = useState<number[]>([]);
  const remaining = tiles.map((_, index) => index).filter(index => !used.includes(index));
  const complete = tiles.length > 0 && remaining.length === 0;
  const built = used.map(index => tiles[index]).join(' ');

  const take = (index: number) => setUsed(order => (order.includes(index) ? order : [...order, index]));
  const undo = () => setUsed(order => order.slice(0, -1));
  const submit = () => {
    if (complete && !disabled) onAnswer(built);
  };

  useShortcuts(event => {
    const digit = digitOf(event);
    if (digit) {
      if (digit <= remaining.length) {
        event.preventDefault();
        take(remaining[digit - 1]);
      }
      return;
    }
    if (event.key === 'Backspace' && used.length) {
      event.preventDefault();
      undo();
    } else if (event.key === 'Enter' && complete && !targetControl(event)) {
      event.preventDefault();
      submit();
    }
  }, !disabled);

  return (
    <>
      <p className={used.length ? 'assembled' : 'assembled is-empty'} aria-live="polite">
        <span className="sr-only">Your answer: </span>
        {used.length ? built : 'Tap the words in order'}
      </p>
      <div className="tiles" role="group" aria-label="Words">
        {remaining.map((index, position) => (
          <button key={index} type="button" className="tile-word" disabled={disabled}
            aria-keyshortcuts={position < 9 ? String(position + 1) : undefined} onClick={() => take(index)}>
            {tiles[index]}
          </button>
        ))}
      </div>
      <div className="assemble-actions">
        <button type="button" className="button secondary" disabled={disabled || !used.length} onClick={undo}
          aria-keyshortcuts="Backspace">
          Remove last word
        </button>
        <button type="button" className="button primary" disabled={disabled || !complete} onClick={submit}
          aria-keyshortcuts="Enter">
          Check
        </button>
      </div>
    </>
  );
}
