import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Config } from '../api';
import { SignIn } from './SignIn';

const config: Config = { title: 'How LLMs work', description: 'A short course.', language: 'en', registration_open: true };

function reply(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

function fill(username: string, password: string) {
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: username } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('SignIn', () => {
  it.each([
    [401, 'invalid username or password', 'Wrong username or password.'],
    [429, 'too many attempts, try again later', 'Too many attempts. Try again in a few minutes.'],
  ])('explains a %i from the server and lets the learner retry', async (status, detail, message) => {
    vi.stubGlobal('fetch', reply(status, { detail }));
    const onSignedIn = vi.fn();
    render(<SignIn config={config} onSignedIn={onSignedIn} />);
    fill('ada', 'long-enough-password');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect((await screen.findByRole('alert')).textContent).toBe(message);
    expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(false);
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it('checks the account rules before signing up', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<SignIn config={config} onSignedIn={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }));

    fill('ada', 'short');
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(screen.getByRole('alert').textContent).toBe('Use a password of at least 10 characters.');

    fill('a b', 'long-enough-password');
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(screen.getByRole('alert').textContent).toMatch(/^Usernames are 3–32 characters/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('hides sign-up when registration is closed', () => {
    render(<SignIn config={{ ...config, registration_open: false }} onSignedIn={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Create an account' })).toBeNull();
    expect(screen.getByText(/created by its administrator/)).toBeTruthy();
  });
});
