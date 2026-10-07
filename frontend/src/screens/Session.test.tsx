import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnswerEvent, AnswerResult, AnswersResponse, Step, StudySession } from '../api';
import { Session } from './Session';

// ------------------------------------------------------------------ fixtures
const choiceStep: Step = {
  id: 'p1:choice:what-is-a-token:1a2b3c4d:0',
  kind: 'choice',
  card_id: 'what-is-a-token',
  prompt: 'What is a token?',
  answer: 'A token is a word or word piece.',
  hint: 'Think of how "unhappiness" might be split up.',
  note: 'Punctuation and spaces get tokens too.',
  options: ['a whole sentence', 'a word or word piece', 'a full paragraph', 'a line of code'],
};

const triageStep: Step = {
  id: 'triage:rare-word-split:5e6f7a8b:0',
  kind: 'triage',
  card_id: 'rare-word-split',
  prompt: 'What does a tokenizer do with a long, rare word?',
  answer: 'It breaks the word into several smaller tokens.',
  hint: null,
  note: 'Subword pieces let the model spell out any word.',
};

const flashStep: Step = {
  id: 'flash:what-is-a-token:1a2b3c4d:0',
  kind: 'flash',
  card_id: 'what-is-a-token',
  prompt: 'What unit of text does a language model read and write?',
  answer: 'A token is a word or word piece.',
  hint: null,
  note: 'Punctuation and spaces get tokens too.',
};

const clozeStep: Step = {
  id: 'p1:cloze:close-embeddings:9c8d7e6f:0',
  kind: 'cloze',
  card_id: 'close-embeddings',
  prompt: 'What does it mean when two token embeddings are close together?',
  answer: 'The two tokens tend to have similar meanings.',
  hint: null,
  note: null,
  prefix: 'The two tokens tend to have ',
  suffix: '.',
  options: ['similar spellings', 'similar meanings', 'consecutive ids'],
};

const assembleStep: Step = {
  id: 'v2:assemble:what-is-a-token:1a2b3c4d:ev1:0',
  kind: 'assemble',
  card_id: 'what-is-a-token',
  prompt: 'What is a token?',
  answer: 'A token is a word or word piece.',
  hint: null,
  note: null,
  tiles: ['word', 'A', 'piece.', 'token', 'or', 'is', 'a', 'word'],
  repeat: 0,
};

function lesson(steps: Step[], resolved = 0): StudySession {
  return {
    session_id: 'tokens-and-embeddings',
    lesson_id: 'tokens-and-embeddings',
    title: 'Tokens and embeddings',
    mode: 'lesson',
    steps,
    total_cards: 4,
    resolved_cards: resolved,
  };
}

function result(step: Step, overrides: Partial<AnswerResult> = {}): AnswerResult {
  return { step_id: step.id, accepted: true, duplicate: false, correct: null, rejected: null, detail: null, ...overrides };
}

function answers(results: AnswerResult[], session: StudySession | null, stateConflict = false): AnswersResponse {
  return { results, session, state_conflict: stateConflict };
}

// ---------------------------------------------------------------- fake server
type Reply = [number, unknown] | 'network-error';
type Route = (body: unknown) => Reply | Promise<Reply>;

/** A fake backend keyed by path; `/api/settings` answers by default. */
function serve(routes: Record<string, Route>, hintsByDefault = false) {
  const all: Record<string, Route> = { '/api/settings': () => [200, { show_hint_by_default: hintsByDefault }], ...routes };
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const route = all[input.split('?')[0]];
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const reply = route ? await route(body) : ([404, { detail: 'not found' }] as Reply);
    if (reply === 'network-error') throw new TypeError('Failed to fetch');
    const [status, json] = reply;
    return new Response(JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** A reply the test releases by hand, to look at the screen while the request is in flight. */
function deferred() {
  let release!: (reply: Reply) => void;
  const promise = new Promise<Reply>(resolve => { release = resolve; });
  return { promise, release };
}

function sentEvents(fetchMock: ReturnType<typeof serve>): AnswerEvent[] {
  return fetchMock.mock.calls
    .filter(([path]) => path === '/api/answers')
    .flatMap(([, init]) => (JSON.parse(String(init?.body)) as { events: AnswerEvent[] }).events);
}

const heading = () => screen.getByRole('heading', { level: 1 });
const nextButton = () => screen.queryByRole('button', { name: 'Next' });

beforeEach(() => {
  // jsdom has no layout; moving to the next step scrolls to the top.
  vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// --------------------------------------------------------------------- tests
describe('Session', () => {
  it('shows the server verdict on a correct choice, then renders the continuation', async () => {
    const fetchMock = serve({
      '/api/answers': () => [200, answers([result(choiceStep, { correct: true })], lesson([triageStep], 1))],
    });
    render(<main><Session session={lesson([choiceStep, triageStep])} onExit={vi.fn()} /></main>);
    expect(heading().textContent).toBe('What is a token?');
    expect(screen.getByText('Choose the answer')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'a word or word piece' }));

    const verdict = await screen.findByRole('status', { name: 'Correct' });
    expect(within(verdict).getByText('A token is a word or word piece.')).toBeTruthy();
    expect(within(verdict).getByText('Punctuation and spaces get tokens too.')).toBeTruthy();
    const [event] = sentEvents(fetchMock);
    expect(event).toMatchObject({
      session_id: 'tokens-and-embeddings', step_id: choiceStep.id, card_id: 'what-is-a-token',
      kind: 'choice', answer: 'a word or word piece', timing_version: 1,
    });
    expect(Number.isInteger(event.elapsed_ms) && event.elapsed_ms >= 0).toBe(true);
    expect(event.ts).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$/);
    expect(event).not.toHaveProperty('id');
    expect(event).not.toHaveProperty('correct');

    expect(document.activeElement).toBe(nextButton());
    fireEvent.click(nextButton()!);
    expect(heading().textContent).toBe('What does a tokenizer do with a long, rare word?');
    expect(document.activeElement).toBe(heading());
    expect(screen.getByRole('progressbar', { name: 'Cards done' }).getAttribute('value')).toBe('1');
  });

  it('never grades a closed step itself: the server verdict is what the learner sees', async () => {
    serve({
      '/api/answers': () => [200, answers([result(choiceStep, { correct: false })], lesson([flashStep, choiceStep]))],
    });
    render(<Session session={lesson([choiceStep])} onExit={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'a word or word piece' }));
    const verdict = await screen.findByRole('status', { name: 'Not quite' });
    expect(within(verdict).getByText('A token is a word or word piece.')).toBeTruthy();
    expect(screen.queryByText('Correct')).toBeNull();
  });

  it('never shows Next while an answer is in flight', async () => {
    const reply = deferred();
    serve({ '/api/answers': () => reply.promise });
    render(<Session session={lesson([choiceStep])} onExit={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'a word or word piece' }));
    expect(await screen.findByText('Checking your answer…')).toBeTruthy();
    expect(nextButton()).toBeNull();
    expect(screen.getAllByRole('button', { name: /a (whole|word|full|line)/ }).every(b => (b as HTMLButtonElement).disabled)).toBe(true);
    // Enter does nothing while the request is pending.
    fireEvent.keyDown(document.body, { key: 'Enter' });
    expect(nextButton()).toBeNull();

    await act(async () => reply.release([200, answers([result(choiceStep, { correct: true })], lesson([triageStep], 1))]));
    expect(nextButton()).toBeTruthy();
  });

  it('keeps a self-graded step locked until the server confirms it, then moves on', async () => {
    const reply = deferred();
    const fetchMock = serve({ '/api/answers': () => reply.promise });
    render(<Session session={lesson([triageStep, choiceStep])} onExit={vi.fn()} />);

    fireEvent.keyDown(document.body, { key: '2' });
    expect(await screen.findByText('Saving…')).toBeTruthy();
    expect(nextButton()).toBeNull();
    expect((screen.getByRole('button', { name: 'I know' }) as HTMLButtonElement).disabled).toBe(true);
    expect(sentEvents(fetchMock)[0]).toMatchObject({ kind: 'triage', answer: 'know' });

    await act(async () => reply.release([200, answers([result(triageStep)], lesson([choiceStep]))]));
    expect(heading().textContent).toBe('What is a token?');
  });

  it('offers "Sync and continue" after a failed request and recovers from the server', async () => {
    const fetchMock = serve({
      '/api/answers': () => 'network-error',
      '/api/session': () => [200, lesson([choiceStep])],
    });
    render(<main><Session session={lesson([choiceStep])} onExit={vi.fn()} /></main>);
    fireEvent.click(screen.getByRole('button', { name: 'a word or word piece' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Could not reach the server/);
    expect(nextButton()).toBeNull();
    expect(screen.queryByText('Correct')).toBeNull();
    expect(screen.queryByText('Not quite')).toBeNull();

    expect(document.activeElement).toBe(within(alert).getByRole('button', { name: 'Sync and continue' }));

    // The answer never arrived, so the server hands out the same step again:
    // it is answerable once more, and focus is back on its prompt.
    fireEvent.click(within(alert).getByRole('button', { name: 'Sync and continue' }));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(fetchMock.mock.calls.map(([path]) => path)).toContain('/api/session?session_id=tokens-and-embeddings');
    expect(heading().textContent).toBe('What is a token?');
    expect(document.activeElement).toBe(heading());
    expect((screen.getByRole('button', { name: 'a word or word piece' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('treats an invalid answer or a server error as out of sync too', async () => {
    serve({
      '/api/answers': () => [200, answers([result(choiceStep, { accepted: false, rejected: 'invalid', detail: 'unknown session' })], null)],
      '/api/session': () => [500, { detail: 'server error' }],
    });
    render(<Session session={lesson([choiceStep])} onExit={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'a whole sentence' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Unknown session\./);

    fireEvent.click(within(alert).getByRole('button', { name: 'Sync and continue' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/Server error\./));
    expect(within(screen.getByRole('alert')).getByRole('button', { name: 'Sync and continue' })).toBeTruthy();
  });

  it('swaps in the returned session when the server reports the step as stale', async () => {
    serve({
      '/api/answers': () => [200, answers([result(choiceStep, { accepted: false, rejected: 'stale', detail: 'step is no longer served' })], lesson([clozeStep]))],
    });
    render(<Session session={lesson([choiceStep, triageStep])} onExit={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'a whole sentence' }));

    await waitFor(() => expect(heading().textContent).toBe(clozeStep.prompt));
    expect(screen.queryByText('Not quite')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText(/was updated, so it was skipped/)).toBeTruthy();
  });

  it('does not loop when the server hands back the step that was just answered', async () => {
    serve({
      '/api/answers': () => [200, answers([result(triageStep)], lesson([triageStep]), true)],
      '/api/session': () => [200, lesson([choiceStep])],
    });
    render(<Session session={lesson([triageStep])} onExit={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: "Don't know" }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/same exercise again/);
    fireEvent.click(within(alert).getByRole('button', { name: 'Sync and continue' }));
    await waitFor(() => expect(heading().textContent).toBe('What is a token?'));
  });

  it('hides the hint behind a button unless hints are shown by default', async () => {
    serve({});
    render(<Session session={lesson([choiceStep])} onExit={vi.fn()} />);
    expect(screen.queryByText(choiceStep.hint!)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show hint' }));
    expect(screen.getByText(choiceStep.hint!)).toBeTruthy();
    cleanup();

    serve({}, true);
    render(<Session session={lesson([choiceStep])} onExit={vi.fn()} />);
    expect(await screen.findByText(choiceStep.hint!)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Show hint' })).toBeNull();
  });

  it('flips a flash card with Enter and grades it with the number keys', async () => {
    const fetchMock = serve({ '/api/answers': () => [200, answers([result(flashStep)], lesson([choiceStep]))] });
    render(<Session session={lesson([flashStep])} onExit={vi.fn()} />);
    expect(screen.queryByText(flashStep.answer)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Again' })).toBeNull();

    fireEvent.keyDown(document.body, { key: 'Enter' });
    expect(screen.getByText(flashStep.answer)).toBeTruthy();
    expect(screen.getByText(flashStep.note!)).toBeTruthy();

    fireEvent.keyDown(document.body, { key: '1' });
    await waitFor(() => expect(heading().textContent).toBe('What is a token?'));
    expect(sentEvents(fetchMock)[0]).toMatchObject({ kind: 'flash', answer: 'again' });
  });

  it('fills a cloze gap from the options', async () => {
    const fetchMock = serve({ '/api/answers': () => [200, answers([result(clozeStep, { correct: true })], lesson([]))] });
    render(<Session session={lesson([clozeStep])} onExit={vi.fn()} />);
    expect(screen.getByText('Fill in the gap')).toBeTruthy();
    expect(screen.getByLabelText('Gap')).toBeTruthy();
    fireEvent.keyDown(document.body, { key: '2' });
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' });
    await screen.findByRole('status', { name: 'Correct' });
    expect(sentEvents(fetchMock)[0]).toMatchObject({ kind: 'cloze', answer: 'similar meanings' });
  });

  it('builds an assemble answer from the tiles and sends the words in order', async () => {
    const fetchMock = serve({ '/api/answers': () => [200, answers([result(assembleStep, { correct: true })], lesson([]))] });
    render(<Session session={lesson([assembleStep])} onExit={vi.fn()} />);
    const check = screen.getByRole('button', { name: 'Check' }) as HTMLButtonElement;
    expect(check.disabled).toBe(true);
    const tiles = within(screen.getByRole('group', { name: 'Words' }));
    for (const word of ['A', 'token', 'is', 'a', 'word', 'or', 'word', 'piece.']) {
      fireEvent.click(tiles.getAllByRole('button', { name: word })[0]);
    }
    // A mistake can be taken back.
    fireEvent.click(screen.getByRole('button', { name: 'Remove last word' }));
    fireEvent.click(tiles.getByRole('button', { name: 'piece.' }));
    expect(check.disabled).toBe(false);
    fireEvent.click(check);
    await screen.findByRole('status', { name: 'Correct' });
    expect(sentEvents(fetchMock)[0]).toMatchObject({ kind: 'assemble', answer: 'A token is a word or word piece.' });
  });

  it('ends on the Done screen when the server has no steps left', async () => {
    const onExit = vi.fn();
    serve({ '/api/answers': () => [200, answers([result(choiceStep, { correct: true })], lesson([], 4))] });
    render(<Session session={lesson([choiceStep], 3)} onExit={onExit} />);
    fireEvent.click(screen.getByRole('button', { name: 'a word or word piece' }));
    await screen.findByRole('status', { name: 'Correct' });
    fireEvent.keyDown(document.body, { key: 'Enter' });

    expect(heading().textContent).toBe('Lesson complete');
    expect(screen.getByText('100%')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back to home' }));
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it('shows the Done screen straight away for a session with nothing left to do', () => {
    serve({});
    render(<Session session={{ ...lesson([]), mode: 'scheduled_review', title: 'Review', lesson_id: null }} onExit={vi.fn()} />);
    expect(heading().textContent).toBe('Nothing to review');
  });
});
