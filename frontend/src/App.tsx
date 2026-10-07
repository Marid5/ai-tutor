import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from './api';
import type { Chapters, Config, SessionTarget, StudySession } from './api';
import { LoadError, Loading } from './components/Loading';
import { Shell, type Tab } from './components/Shell';
import { Home } from './screens/Home';
import { Progress } from './screens/Progress';
import { Session } from './screens/Session';
import { Settings } from './screens/Settings';
import { SignIn } from './screens/SignIn';

// Screens are plain state, not routes: the app has a handful of them and a
// reload should land on Home (the server restores any session in progress).
type View =
  | { name: 'loading' }
  | { name: 'signin' }
  | { name: Tab }
  | { name: 'session'; session: StudySession };

// After loading home data, stay on the Progress or Settings tab if the learner is
// there, and in a session unless the load was the way out of it: a board that
// arrives after the learner started a lesson must not close that lesson.
const showHome = (leavingSession: boolean) => (current: View): View =>
  current.name === 'progress' || current.name === 'settings' || (current.name === 'session' && !leavingSession)
    ? current
    : { name: 'home' };

export function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [view, setView] = useState<View>({ name: 'loading' });
  const [chapters, setChapters] = useState<Chapters | null>(null);
  const [homeError, setHomeError] = useState<unknown>(null);
  const [notice, setNotice] = useState('');
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);

  /** Load the home board; false when the server sees no session (sign-in shows instead). */
  const loadHome = useCallback(async (leavingSession = false): Promise<boolean> => {
    setHomeError(null);
    try {
      setChapters(await api.getChapters());
      setView(showHome(leavingSession));
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 401) return false; // onUnauthorized shows sign-in
      setHomeError(error);
      setView(showHome(leavingSession));
    }
    return true;
  }, []);

  useEffect(() => {
    api.getConfig().then(setConfig, () => setConfig(null));
    void loadHome();
    return api.onUnauthorized(() => {
      setChapters(null);
      setView({ name: 'signin' });
    });
  }, [loadHome]);

  // When the learner moves to another screen, put focus on its heading so
  // keyboard and screen-reader users start there. The first screen after
  // loading keeps the browser's default focus.
  const shownScreen = useRef(view.name);
  useEffect(() => {
    const previous = shownScreen.current;
    shownScreen.current = view.name;
    if (previous === view.name || previous === 'loading') return;
    document.querySelector<HTMLElement>('main h1')?.focus();
  }, [view.name]);

  useEffect(() => {
    document.title = config?.title ? `${config.title} · AI Tutor` : 'AI Tutor';
  }, [config]);

  const navigate = (tab: Tab) => {
    setNotice('');
    setView({ name: tab });
    if (tab === 'home') void loadHome();
  };

  const start = async (target: SessionTarget) => {
    if (startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    setNotice('');
    try {
      const session = await api.startSession(target);
      setView({ name: 'session', session });
    } catch (error) {
      setNotice(api.describeError(error));
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  };

  const signOut = async () => {
    await api.logout().catch(() => undefined);
    setChapters(null);
    setNotice('');
    setView({ name: 'signin' });
  };

  if (view.name === 'loading') {
    return <Shell><Loading /></Shell>;
  }
  if (view.name === 'signin') {
    return <Shell><SignIn config={config} onSignedIn={loadHome} /></Shell>;
  }
  if (view.name === 'session') {
    return <Shell courseTitle={config?.title}><Session key={view.session.session_id} session={view.session} onExit={() => void loadHome(true)} /></Shell>;
  }

  let screen;
  if (view.name === 'progress') screen = <Progress />;
  else if (view.name === 'settings') screen = <Settings onSignOut={() => void signOut()} />;
  else if (chapters) screen = <Home course={config} chapters={chapters} onStart={start} notice={notice} busy={starting} />;
  else if (homeError) screen = <LoadError error={homeError} onRetry={() => void loadHome()} />;
  else screen = <Loading />;

  return (
    <Shell tab={view.name} onNavigate={navigate} courseTitle={config?.title}>
      {screen}
    </Shell>
  );
}
