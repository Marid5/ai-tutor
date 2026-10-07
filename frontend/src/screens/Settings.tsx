import { useState, type FormEvent } from 'react';
import {
  ApiError, changePassword, describeError, getAccount, getHealth, getSettings, updateSettings,
  type Account, type Health, type Settings as SettingsData,
} from '../api';
import { Field } from '../components/Field';
import { LoadError, Loading } from '../components/Loading';
import { THEME_OPTIONS, applyTheme, readTheme, saveTheme, type ThemePreference } from '../theme';
import { useLoader } from '../useLoader';

const THEME_LABEL: Record<ThemePreference, string> = { system: 'System', light: 'Light', dark: 'Dark' };

interface SettingsBundle {
  settings: SettingsData;
  account: Account;
  health: Health | null;
}

const loadSettings = async (): Promise<SettingsBundle> => {
  const [settings, account, health] = await Promise.all([
    getSettings(),
    getAccount(),
    // Version numbers are informational; the screen works without them.
    getHealth().catch(() => null),
  ]);
  return { settings, account, health };
};

function ThemePicker() {
  const [theme, setTheme] = useState<ThemePreference>(readTheme);
  const choose = (next: ThemePreference) => {
    setTheme(next);
    saveTheme(next);
    applyTheme(next);
  };
  return (
    <fieldset className="segmented">
      <legend className="field-label">Theme</legend>
      <div className="segmented-options">
        {THEME_OPTIONS.map(option => (
          <label key={option}>
            <input type="radio" name="theme" value={option} checked={theme === option} onChange={() => choose(option)} />
            <span>{THEME_LABEL[option]}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function HintSwitch({ settings, onChange }: { settings: SettingsData; onChange: (next: SettingsData) => void }) {
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const on = settings.show_hint_by_default;
  const toggle = async () => {
    setSaving(true);
    setError('');
    try {
      onChange(await updateSettings({ show_hint_by_default: !on }));
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setSaving(false);
    }
  };
  return (
    <>
      <div className="row">
        <span id="hint-label" className="row-label">Show hints by default</span>
        <button type="button" role="switch" className="switch" aria-checked={on} aria-labelledby="hint-label"
          aria-describedby="hint-help" disabled={saving} onClick={() => void toggle()} />
      </div>
      <p id="hint-help" className="muted small">Show a card's hint before you answer. You can always open it during a session.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
    </>
  );
}

function PasswordForm() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState<{ kind: 'error' | 'success'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const bytes = new TextEncoder().encode(next).length;
    if (bytes < 10 || bytes > 72) {
      setMessage({ kind: 'error', text: 'The new password must be 10–72 characters.' });
      return;
    }
    if (next !== confirm) {
      setMessage({ kind: 'error', text: 'The new passwords do not match.' });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await changePassword(current, next);
      setCurrent(''); setNext(''); setConfirm('');
      setMessage({ kind: 'success', text: 'Password changed. Other devices have been signed out.' });
    } catch (failure) {
      const text = failure instanceof ApiError && failure.status === 403 ? 'The current password is incorrect.'
        : failure instanceof ApiError && failure.status === 429 ? 'Too many attempts. Try again in a few minutes.'
          : describeError(failure);
      setMessage({ kind: 'error', text });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-labelledby="password-title">
      <h2 id="password-title" className="card-title">Change password</h2>
      <Field label="Current password" type="password" value={current} onChange={e => setCurrent(e.target.value)}
        autoComplete="current-password" required />
      <Field label="New password" type="password" value={next} onChange={e => setNext(e.target.value)}
        autoComplete="new-password" required hint="At least 10 characters." />
      <Field label="Repeat new password" type="password" value={confirm} onChange={e => setConfirm(e.target.value)}
        autoComplete="new-password" required />
      {message && (
        <p className={message.kind === 'error' ? 'form-error' : 'form-success'} role={message.kind === 'error' ? 'alert' : 'status'}>
          {message.text}
        </p>
      )}
      <button type="submit" className="button secondary" disabled={busy || !current || !next || !confirm}>
        {busy ? 'Saving…' : 'Change password'}
      </button>
    </form>
  );
}

export function Settings({ onSignOut }: { onSignOut: () => void }) {
  const [state, reload, replace] = useLoader(loadSettings);
  return (
    <div className="settings-screen">
      <h1 tabIndex={-1}>Settings</h1>

      <section className="card" aria-label="Appearance">
        <ThemePicker />
      </section>

      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <LoadError error={state.error} onRetry={reload} />}
      {state.status === 'ready' && (
        <>
          <section className="card" aria-label="Study">
            <HintSwitch settings={state.data.settings}
              onChange={settings => replace({ ...state.data, settings })} />
          </section>

          <section className="card">
            <PasswordForm />
          </section>

          <section className="card" aria-label="Account">
            <div className="row"><span>Signed in as</span><b className="wrap">{state.data.account.username}</b></div>
            {state.data.health && (
              <>
                <div className="row"><span>Course version</span><b className="mono">{state.data.health.program_version}</b></div>
                <div className="row"><span>App version</span><b className="mono">{state.data.health.version}</b></div>
              </>
            )}
            <button type="button" className="button secondary danger" onClick={onSignOut}>Sign out</button>
          </section>
        </>
      )}
    </div>
  );
}
