import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import type { Chapters, Config, Progress, StudySession } from './api';

const config: Config = { title: 'How LLMs work', description: 'A short course.', language: 'en', registration_open: false };

const chapters: Chapters = {
  day: '2026-10-07', cards_total: 4, cards_ready: 0,
  next_lesson: { id: 'tokens', title: 'Tokens', chapter_id: 'foundations', action: 'start' },
  review_due: 0, review_sessions_remaining: 0, review_session_size: 10,
  practice_available: false, practice_card_count: 0, program_version: 'abc',
  chapters: [{
    id: 'foundations', title: 'Foundations', position: 1, lessons_total: 1, lessons_completed: 0,
    cards_total: 4, cards_ready: 0,
    lessons: [{ id: 'tokens', title: 'Tokens', position: 1, status: 'available', is_in_progress: false, has_open_work: true, cards_total: 4, cards_ready: 0 }],
  }],
};

const progress: Progress = {
  cards_ready: 0, cards_total: 4, retention_30d: 0, checks_30d: 0, forecast_7d: [], problem_cards: [],
  session_minutes: 0, lessons_total: 1, lessons_completed: 0,
};

type Routes = Record<string, () => [number, unknown]>;

/** A fake backend: each path answers with [status, JSON body]. */
function serve(routes: Routes) {
  const fetchMock = vi.fn(async (input: string) => {
    const route = routes[input.split('?')[0]];
    const [status, body] = route ? route() : [404, { detail: 'not found' }];
    return status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const signedOut = (): [number, unknown] => [401, { detail: 'login required' }];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('shows sign-in when the session is missing or expired', async () => {
    serve({ '/api/config': () => [200, config], '/api/chapters': signedOut });
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'How LLMs work' })).toBeTruthy();
  });

  it('explains a session cookie that the browser did not keep', async () => {
    // Sign-in succeeds, but the very next request still has no session: the
    // classic COOKIE_SECURE=true over plain http.
    serve({
      '/api/config': () => [200, config],
      '/api/chapters': signedOut,
      '/api/login': () => [200, { username: 'ada' }],
    });
    render(<App />);
    await screen.findByRole('heading', { name: 'Sign in' });
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'ada' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/did not keep the session cookie.*COOKIE_SECURE=false/);
    expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('moves focus to the new screen heading when the screen changes', async () => {
    serve({
      '/api/config': () => [200, config],
      '/api/chapters': () => [200, chapters],
      '/api/progress': () => [200, progress],
    });
    render(<App />);
    const home = await screen.findByRole('heading', { level: 1, name: 'How LLMs work' });
    // The first screen after loading keeps the browser's default focus.
    expect(document.activeElement).not.toBe(home);

    fireEvent.click(screen.getByRole('button', { name: 'Progress' }));
    const heading = await screen.findByRole('heading', { level: 1, name: 'Your progress' });
    await waitFor(() => expect(document.activeElement).toBe(heading));

    fireEvent.click(screen.getByRole('button', { name: 'Home' }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1, name: 'How LLMs work' })));
  });

  it('stays in a session that started while the home board was still reloading', async () => {
    const session: StudySession = {
      session_id: 'tokens', lesson_id: 'tokens', title: 'Tokens', mode: 'lesson', total_cards: 4, resolved_cards: 0,
      steps: [{
        id: 'triage:what-is-a-token:1a2b3c4d:0', kind: 'triage', card_id: 'what-is-a-token',
        prompt: 'What is a token?', answer: 'A token is a word or word piece.', hint: null, note: null,
      }],
    };
    let release = () => {};
    let board = 0;
    let lateBoardSettled = false;
    const fetchMock = serve({
      '/api/config': () => [200, config],
      '/api/chapters': () => [200, chapters],
      '/api/progress': () => [200, progress],
      '/api/settings': () => [200, { show_hint_by_default: false }],
      '/api/lessons/tokens/start': () => [200, session],
    });
    // The second request for the board (on the way back from Progress) answers late.
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: string) => {
      if (input === '/api/chapters' && ++board === 2) {
        await new Promise<void>(resolve => { release = resolve; });
        const late = await answer(input);
        lateBoardSettled = true;
        return late;
      }
      return answer(input);
    });
    render(<App />);
    await screen.findByRole('heading', { level: 1, name: 'How LLMs work' });
    fireEvent.click(screen.getByRole('button', { name: 'Progress' }));
    await screen.findByRole('heading', { level: 1, name: 'Your progress' });
    fireEvent.click(screen.getByRole('button', { name: 'Home' }));
    // The board from before is still on screen, so the learner can start the lesson at once.
    fireEvent.click(await screen.findByRole('button', { name: /Start lesson/ }));
    await screen.findByRole('heading', { level: 1, name: 'What is a token?' });

    // Let the late board arrive and be applied, all inside act, then wait for the signal that it did.
    await act(async () => { release(); });
    await waitFor(() => expect(lateBoardSettled).toBe(true));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(board).toBe(2);
    expect(screen.getByRole('heading', { level: 1, name: 'What is a token?' })).toBeTruthy();
  });

  it('goes to sign-in when the sign-in expires in the middle of a session', async () => {
    const session: StudySession = {
      session_id: 'tokens', lesson_id: 'tokens', title: 'Tokens', mode: 'lesson', total_cards: 4, resolved_cards: 0,
      steps: [{
        id: 'p1:choice:what-is-a-token:1a2b3c4d:0', kind: 'choice', card_id: 'what-is-a-token',
        prompt: 'What is a token?', answer: 'A token is a word or word piece.', hint: null, note: null,
        options: ['a whole sentence', 'a word or word piece', 'a full paragraph', 'a line of code'],
      }],
    };
    serve({
      '/api/config': () => [200, config],
      '/api/chapters': () => [200, chapters],
      '/api/settings': () => [200, { show_hint_by_default: false }],
      '/api/lessons/tokens/start': () => [200, session],
      '/api/answers': signedOut,
    });
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Start lesson/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'a word or word piece' }));

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Sync and continue' })).toBeNull();
  });
});
