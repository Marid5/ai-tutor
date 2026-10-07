import { useEffect, useRef, useState } from 'react';
import type { Step } from '../api';
import { digitOf, targetControl, useShortcuts } from '../keys';

interface AssembleProps {
  step: Step;
  disabled: boolean;
  onAnswer: (answer: string) => void;
}

/** Where keyboard focus goes after the answer line changes. */
type FocusAfter =
  | { to: 'tile'; position: number } // the word now at this place in the pool (or the one before it)
  | { to: 'word'; index: number } // a word that just went back to the pool
  | { to: 'check' };

/**
 * Rebuild the answer from its own shuffled words. Keys: 1-9 take the n-th
 * remaining word, Backspace takes the last one back, Enter checks once every
 * word is placed. The server compares the text; the client only joins words.
 * A picked word leaves the pool, so focus moves to its neighbour (or to
 * "Check" once the answer is complete) instead of falling off the page.
 */
export function Assemble({ step, disabled, onAnswer }: AssembleProps) {
  const tiles = step.tiles ?? [];
  const [used, setUsed] = useState<number[]>([]);
  const remaining = tiles.map((_, index) => index).filter(index => !used.includes(index));
  const complete = tiles.length > 0 && remaining.length === 0;
  const built = used.map(index => tiles[index]).join(' ');

  const tileButtons = useRef(new Map<number, HTMLButtonElement>());
  const checkButton = useRef<HTMLButtonElement>(null);
  const focusAfter = useRef<FocusAfter | null>(null);

  useEffect(() => {
    const target = focusAfter.current;
    focusAfter.current = null;
    if (!target) return;
    if (target.to === 'check' || (target.to === 'tile' && remaining.length === 0)) {
      checkButton.current?.focus();
    } else if (target.to === 'tile') {
      const index = remaining[Math.min(target.position, remaining.length - 1)];
      tileButtons.current.get(index)?.focus();
    } else {
      tileButtons.current.get(target.index)?.focus();
    }
  });

  const take = (index: number) => {
    if (used.includes(index)) return;
    const position = remaining.indexOf(index);
    focusAfter.current = remaining.length === 1 ? { to: 'check' } : { to: 'tile', position };
    setUsed([...used, index]);
  };

  const undo = (fromUndoButton: boolean) => {
    if (!used.length) return;
    const restored = used[used.length - 1];
    // Pressing "Remove last word" again should stay possible from the keyboard;
    // otherwise focus follows the word back into the pool.
    focusAfter.current = fromUndoButton && used.length > 1 ? null : { to: 'word', index: restored };
    setUsed(used.slice(0, -1));
  };

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
      undo(false);
    } else if (event.key === 'Enter' && complete && !targetControl(event)) {
      event.preventDefault();
      submit();
    }
  }, !disabled);

  return (
    <>
      <p className={used.length ? 'assembled' : 'assembled is-empty'} aria-live="polite">
        <span className="visually-hidden">Your answer: </span>
        {used.length ? built : 'Pick the words in order'}
      </p>
      <div className="tiles" role="group" aria-label="Words">
        {remaining.map((index, position) => (
          <button key={index} type="button" className="tile-word" disabled={disabled}
            ref={element => {
              if (element) tileButtons.current.set(index, element);
              else tileButtons.current.delete(index);
            }}
            aria-keyshortcuts={position < 9 ? String(position + 1) : undefined} onClick={() => take(index)}>
            {tiles[index]}
          </button>
        ))}
      </div>
      <div className="assemble-actions">
        <button type="button" className="button secondary" disabled={disabled || !used.length}
          onClick={() => undo(true)} aria-keyshortcuts="Backspace">
          Remove last word
        </button>
        <button type="button" className="button primary" ref={checkButton} disabled={disabled || !complete}
          onClick={submit} aria-keyshortcuts="Enter">
          Check
        </button>
      </div>
    </>
  );
}
