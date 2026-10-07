import type { StudySession } from '../api';

export interface SessionProps {
  /** The session as the server opened it; `steps: []` means there is nothing left to do. */
  session: StudySession;
  /** Leave the session; the home screen reloads its counts. */
  onExit: () => void;
}

/** The study session screen: steps, feedback and the finish screen. */
export function Session({ session, onExit }: SessionProps) {
  return (
    <div className="session">
      <p className="eyebrow">Study session</p>
      <h1 tabIndex={-1}>{session.title}</h1>
      <button type="button" className="button secondary" onClick={onExit}>Back to home</button>
    </div>
  );
}
