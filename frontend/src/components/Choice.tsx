import { useRef, useState } from 'react';
import { digitOf, targetControl, useShortcuts } from '../keys';

interface ChoiceProps {
  options: string[];
  /** Locked while the answer is in flight and once the server has graded it. */
  disabled: boolean;
  onAnswer: (option: string) => void;
  /** The option the learner picked, once answered. */
  chosen?: string | null;
  /** The server's verdict on `chosen`; null while it is still being checked. */
  verdict?: boolean | null;
  /** Accessible name of the group of buttons. */
  label?: string;
}

/**
 * Answer buttons for choice and cloze steps. A click or tap answers at once;
 * on a keyboard, 1-9 selects (and focuses) an option and Enter submits it.
 * Only the chosen option is marked, with the server's verdict: the client
 * never knows which option is right.
 */
export function Choice({ options, disabled, onAnswer, chosen = null, verdict = null, label = 'Options' }: ChoiceProps) {
  const [selected, setSelected] = useState<number | null>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);

  useShortcuts(event => {
    const digit = digitOf(event);
    if (digit && digit <= options.length) {
      event.preventDefault();
      setSelected(digit - 1);
      buttons.current[digit - 1]?.focus();
      return;
    }
    if (event.key !== 'Enter' || selected === null) return;
    const control = targetControl(event);
    // Enter on some other control (say, "Show hint") belongs to that control.
    if (control && !buttons.current.includes(control as HTMLButtonElement)) return;
    // Handled here, so the focused option's own click must not answer again.
    event.preventDefault();
    onAnswer(options[selected]);
  }, !disabled);

  return (
    <div className="options" role="group" aria-label={label}>
      {options.map((option, index) => {
        const isChosen = chosen !== null && option === chosen;
        const state = isChosen ? (verdict === true ? 'right' : verdict === false ? 'wrong' : 'chosen') : undefined;
        return (
          <button key={`${index}:${option}`} type="button"
            ref={element => { buttons.current[index] = element; }}
            className={selected === index && !disabled ? 'option is-selected' : 'option'}
            data-state={state} disabled={disabled} aria-keyshortcuts={String(index + 1)}
            onFocus={() => setSelected(index)}
            onClick={() => onAnswer(option)}>
            <kbd className="key" aria-hidden="true">{index + 1}</kbd>
            <span className="option-text">{option}</span>
          </button>
        );
      })}
    </div>
  );
}
