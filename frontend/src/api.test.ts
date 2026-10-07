import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from './api';
import { ApiError, REQUEST_TIMEOUT_MS } from './api';

type FetchArgs = [input: string, init: RequestInit];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(impl: (...args: FetchArgs) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal('fetch', fn);
  return fn;
}

/** A fetch that never answers on its own and rejects like the browser does when aborted. */
function hangingFetch() {
  return mockFetch((_input, init) => new Promise<Response>((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new DOMException('The operation was aborted.', 'AbortError')));
  }));
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('request timeout', () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it('aborts a request that has not answered after 10 seconds', async () => {
    const fetchMock = hangingFetch();
    let settled: unknown = 'pending';
    const pending = api.getChapters().then(
      value => { settled = value; },
      error => { settled = error; },
    );

    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect(settled).toBe('pending');
    expect(fetchMock.mock.calls[0][1].signal?.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(REQUEST_TIMEOUT_MS).toBe(10_000);
    expect(fetchMock.mock.calls[0][1].signal?.aborted).toBe(true);
    expect(settled).toBeInstanceOf(ApiError);
    expect((settled as ApiError).status).toBe(0);
    expect((settled as ApiError).timedOut).toBe(true);
  });

  it('clears the timer once the server answers', async () => {
    mockFetch(async () => jsonResponse(200, { username: 'ada' }));
    await expect(api.getAccount()).resolves.toEqual({ username: 'ada' });
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('responses', () => {
  it('turns 204 No Content into undefined', async () => {
    mockFetch(async () => new Response(null, { status: 204 }));
    await expect(api.logout()).resolves.toBeUndefined();
    await expect(api.changePassword('old-password', 'new-password')).resolves.toBeUndefined();
  });

  it('raises ApiError with the server detail', async () => {
    mockFetch(async () => jsonResponse(403, { detail: 'registration is closed' }));
    const error = await api.register('ada', 'long-enough-password').catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(403);
    expect(error.detail).toBe('registration is closed');
    expect(error.timedOut).toBe(false);
  });

  it('falls back to a generic detail when the body is not the JSON error shape', async () => {
    mockFetch(async () => new Response('<html>Bad gateway</html>', { status: 502 }));
    const error = await api.getProgress().catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.detail).toBe('Request failed (502)');
  });

  it('reports a network failure as status 0', async () => {
    mockFetch(async () => { throw new TypeError('Failed to fetch'); });
    const error = await api.getConfig().catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.timedOut).toBe(false);
  });
});

describe('requests', () => {
  it('sends JSON with the session cookie of this origin', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { username: 'ada' }));
    await api.login('ada', 'long-enough-password');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/login');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('same-origin');
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
    expect(JSON.parse(String(init.body))).toEqual({ username: 'ada', password: 'long-enough-password' });
  });

  it('maps each session target to its endpoint', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, {}));
    await api.startSession({ type: 'lesson', id: 'what-is-a-token' });
    await api.startSession({ type: 'lesson-practice', id: 'what-is-a-token' });
    await api.startSession({ type: 'review' });
    await api.startSession({ type: 'practice' });
    await api.getSession('practice-2026-10-07/0');
    expect(fetchMock.mock.calls.map(([url, init]) => `${init.method ?? 'GET'} ${url}`)).toEqual([
      'POST /api/lessons/what-is-a-token/start',
      'POST /api/lessons/what-is-a-token/practice',
      'POST /api/review/start',
      'POST /api/practice/start',
      'GET /api/session?session_id=practice-2026-10-07%2F0',
    ]);
  });

  it('posts answers as a batch and returns per-event results, including stale rejections', async () => {
    const reply: api.AnswersResponse = {
      results: [
        { step_id: 'p1:choice:a:12345678:0', accepted: false, duplicate: false, correct: null, rejected: 'stale', detail: 'step is no longer served' },
        { step_id: 'p1:choice:b:12345678:0', accepted: true, duplicate: false, correct: true, rejected: null, detail: null },
      ],
      session: null,
      state_conflict: false,
    };
    const fetchMock = mockFetch(async () => jsonResponse(200, reply));
    const event: api.AnswerEvent = {
      session_id: 'lesson-a', step_id: 'p1:choice:b:12345678:0', card_id: 'b', kind: 'choice', answer: 'Tokens', elapsed_ms: 1200,
    };
    const result = await api.sendAnswers([event]);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({ events: [event] });
    expect(result.results[0].rejected).toBe('stale');
    expect(result.results[1].accepted).toBe(true);
  });
});

describe('signed-out detection', () => {
  it('tells subscribers when a protected request comes back 401', async () => {
    mockFetch(async () => jsonResponse(401, { detail: 'login required' }));
    const handler = vi.fn();
    const unsubscribe = api.onUnauthorized(handler);
    await api.getChapters().catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    unsubscribe();
    await api.getChapters().catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does not treat a wrong password at sign-in as an expired session', async () => {
    mockFetch(async () => jsonResponse(401, { detail: 'invalid username or password' }));
    const handler = vi.fn();
    const unsubscribe = api.onUnauthorized(handler);
    const error = await api.login('ada', 'wrong-password-1').catch(e => e);
    unsubscribe();
    expect(error.status).toBe(401);
    expect(handler).not.toHaveBeenCalled();
  });
});
