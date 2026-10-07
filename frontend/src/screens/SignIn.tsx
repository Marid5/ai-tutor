import { useState, type FormEvent } from 'react';
import { ApiError, describeError, login, register, type Config } from '../api';

// The server's account rules, checked here first so the learner gets a clear
// message before a round trip. The server enforces them regardless.
const USERNAME_PATTERN = /^[a-z0-9_.-]{3,32}$/;
const PASSWORD_MIN_BYTES = 10;
const PASSWORD_MAX_BYTES = 72;

type Mode = 'signin' | 'signup';

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
  onSignedIn: () => void;
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
      onSignedIn();
    } catch (failure) {
      setError(failureMessage(failure, mode));
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
        <h1>{config?.title || 'AI Tutor'}</h1>
        {config?.description && <p className="lede">{config.description}</p>}
      </section>

      <form className="card form" onSubmit={submit} noValidate aria-labelledby="auth-title">
        <h2 id="auth-title">{signingUp ? 'Create your account' : 'Sign in'}</h2>
        <label className="field">
          <span className="field-label">Username</span>
          <input name="username" value={username} onChange={e => setUsername(e.target.value)}
            autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} required />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input name="password" type="password" value={password} onChange={e => setPassword(e.target.value)}
            autoComplete={signingUp ? 'new-password' : 'current-password'} required />
          {signingUp && <span className="field-hint">At least 10 characters.</span>}
        </label>
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
