import { useCallback, useEffect, useState } from 'react';

export type Loaded<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: unknown };

/** Load data when a screen mounts; `reload` runs the loader again. */
export function useLoader<T>(load: () => Promise<T>): [Loaded<T>, () => void, (data: T) => void] {
  const [state, setState] = useState<Loaded<T>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setState({ status: 'loading' });
    load().then(
      data => { if (active) setState({ status: 'ready', data }); },
      error => { if (active) setState({ status: 'error', error }); },
    );
    return () => { active = false; };
    // The loader is a module-level API call; reloading is explicit via `attempt`.
  }, [attempt]);

  const reload = useCallback(() => setAttempt(n => n + 1), []);
  const replace = useCallback((data: T) => setState({ status: 'ready', data }), []);
  return [state, reload, replace];
}
