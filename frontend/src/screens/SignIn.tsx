import { useState, type FormEvent } from 'react';
import { ApiError, describeError, login, register, type Config } from '../api';
import { Field } from '../components/Field';

// The server's account rules, checked here first so the learner gets a clear
// message before a round trip. The server enforces them regardless.
const USERNAME_PATTERN = /^[a-z0-9_.-]{3,32}$/;
const PASSWORD_MIN_BYTES = 10;
const PASSWORD_MAX_BYTES = 72;

type Mode = 'signin' | 'signup';

const COOKIE_NOT_KEPT =
  'Signed in, but your browser did not keep the session cookie. If you are on plain http, set COOKIE_SECURE=false.';

function signUpProblem(username: string, password: string): string | null {
  if (!USERNAME_PATTERN.test(username)) {
    return 'Usernames are 3–32 characters: lowercase letters, digits, and _ . -';
  }
  const bytes = new TextEncoder().encode(password).length;
  if (bytes < PASSWORD_MIN_BYTES) return `Use a password of at least ${PASSWORD_MIN_BYTES} characters.`;
  if (bytes > PASSWORD_MAX_BYTES) return 'That password is too long; keep it under 72 characters.';
  return null;
}

function failureMessage(error: unknown, mode: Mode): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Wrong username or password.';
    if (error.status === 429) return 'Too many attempts. Try again in a few minutes.';
    if (error.status === 409) return 'That username is taken.';
    if (error.status === 403 && mode === 'signup') return 'Registration is closed on this server.';
    if (error.status === 422) {
      return 'Usernames are 3–32 characters (a–z, 0–9, _ . -); passwords are 10–72 characters.';
    }
  }
  return describeError(error);
}

interface SignInProps {
  config: Config | null;
  /** Load the signed-in app; resolves false when the server still sees no session. */
  onSignedIn: () => Promise<boolean>;
}

export function SignIn({ config, onSignedIn }: SignInProps) {
  const [mode, setMode] = useState<Mode>('signin');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const canRegister = Boolean(config?.registration_open);
  const signingUp = mode === 'signup' && canRegister;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const name = username.trim().toLowerCase();
    const problem = signingUp ? signUpProblem(name, password) : null;
    if (problem) { setError(problem); return; }
    setError('');
    setBusy(true);
    try {
      if (signingUp) await register(name, password);
      else await login(name, password);
      // Loading the home screen is the first request that relies on the new
      // session cookie; if the browser dropped it, say why instead of looping.
      if (!(await onSignedIn())) setError(COOKIE_NOT_KEPT);
    } catch (failure) {
      setError(failureMessage(failure, mode));
    } finally {
      setBusy(false);
    }
  };

  const switchMode = () => {
    setMode(signingUp ? 'signin' : 'signup');
    setError('');
  };

  return (
    <div className="auth">
      <section className="auth-intro">
        <p className="eyebrow">Spaced repetition</p>
        <h1 tabIndex={-1}>{config?.title || 'AI Tutor'}</h1>
        {config?.description && <p className="lede">{config.description}</p>}
      </section>

      <form className="card form" onSubmit={submit} noValidate aria-labelledby="auth-title">
        <h2 id="auth-title">{signingUp ? 'Create your account' : 'Sign in'}</h2>
        <Field label="Username" name="username" value={username} onChange={e => setUsername(e.target.value)}
          autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} required />
        <Field label="Password" name="password" type="password" value={password} onChange={e => setPassword(e.target.value)}
          autoComplete={signingUp ? 'new-password' : 'current-password'} required
          hint={signingUp ? 'At least 10 characters.' : undefined} />
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="button primary" disabled={busy}>
          {busy ? 'One moment…' : signingUp ? 'Create account' : 'Sign in'}
        </button>
      </form>

      {canRegister ? (
        <p className="auth-switch">
          {signingUp ? 'Already have an account?' : 'New here?'}{' '}
          <button type="button" className="link" onClick={switchMode}>
            {signingUp ? 'Sign in' : 'Create an account'}
          </button>
        </p>
      ) : (
        <p className="auth-switch muted">Accounts on this server are created by its administrator.</p>
      )}
    </div>
  );
}
