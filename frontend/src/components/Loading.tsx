import { describeError } from '../api';

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function LoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="card load-error" role="alert">
      <p>{describeError(error)}</p>
      <button type="button" className="button secondary" onClick={onRetry}>Try again</button>
    </div>
  );
}
