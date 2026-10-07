import { useId, type InputHTMLAttributes } from 'react';

interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  /** Extra guidance, announced after the label rather than as part of it. */
  hint?: string;
}

/** A labelled text input with an optional hint. */
export function Field({ label, hint, ...input }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      <input id={id} aria-describedby={hint ? hintId : undefined} {...input} />
      {hint && <span id={hintId} className="field-hint">{hint}</span>}
    </div>
  );
}
