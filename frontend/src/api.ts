// Typed client for the backend HTTP API. Every call goes to the same origin
// with the session cookie; errors arrive as `{"detail": "..."}` and surface
// here as `ApiError`.

// ------------------------------------------------------------------ payloads
export interface Config {
  title: string;
  description: string;
  language: string;
  registration_open: boolean;
}

export interface Health {
  status: string;
  version: string;
  git_sha: string;
  program_version: string;
  chapters: number;
  lessons: number;
  cards: number;
}

export interface Account {
  username: string;
}

export interface Settings {
  show_hint_by_default: boolean;
}

export type LessonStatus = 'available' | 'completed';

export interface Lesson {
  id: string;
  title: string;
  position: number;
  /** Every lesson can be started at any time; "completed" means it was finished once. */
  status: LessonStatus;
  is_in_progress: boolean;
  /** Some card has no current check yet, e.g. a new card or one whose answer was edited. */
  has_open_work: boolean;
  cards_total: number;
  cards_ready: number;
}

export interface Chapter {
  id: string;
  title: string;
  position: number;
  lessons_total: number;
  lessons_completed: number;
  cards_total: number;
  cards_ready: number;
  lessons: Lesson[];
}

export interface NextLesson {
  id: string;
  title: string;
  chapter_id: string;
  action: 'start' | 'continue';
}

/** GET /api/chapters: the home screen. */
export interface Chapters {
  /** The learning day, YYYY-MM-DD in the course's timezone. */
  day: string;
  cards_total: number;
  cards_ready: number;
  next_lesson: NextLesson | null;
  review_due: number;
  review_sessions_remaining: number;
  review_session_size: number;
  practice_available: boolean;
  practice_card_count: number;
  chapters: Chapter[];
  program_version: string;
}

export interface ForecastDay {
  day: string;
  amount: number;
}

export interface ProblemCard {
  id: string;
  prompt: string;
  answer: string;
  again_count: number;
}

/** GET /api/progress: the progress board. */
export interface Progress {
  cards_ready: number;
  cards_total: number;
  /** Percentage of correct primary checks in lessons and reviews, last 30 days. */
  retention_30d: number;
  checks_30d: number;
  forecast_7d: ForecastDay[];
  problem_cards: ProblemCard[];
  session_minutes: number;
  lessons_total: number;
  lessons_completed: number;
}

export type StepKind = 'triage' | 'flash' | 'choice' | 'cloze' | 'assemble';
export type SessionMode = 'lesson' | 'lesson_practice' | 'scheduled_review' | 'mixed_practice';

export interface Step {
  id: string;
  kind: StepKind;
  card_id: string;
  prompt: string;
  answer: string;
  hint: string | null;
  note: string | null;
  /** choice and cloze: the buttons to pick from. */
  options?: string[];
  /** cloze: the answer text around the gap. */
  prefix?: string;
  suffix?: string;
  /** assemble: the answer's words, shuffled. */
  tiles?: string[];
  /** Ladder steps: the attempt number on this rung. */
  repeat?: number;
}

/** A study session. `steps: []` means it is finished. */
export interface StudySession {
  session_id: string;
  lesson_id: string | null;
  title: string;
  mode: SessionMode;
  steps: Step[];
  total_cards: number;
  resolved_cards: number;
}

export type TriageAnswer = 'know' | 'dont_know';
export type FlashAnswer = 'remembered' | 'again';

/** One answer, as buffered by the client and sent in a batch. The server computes the verdict. */
export interface AnswerEvent {
  session_id: string;
  step_id: string;
  card_id: string;
  kind: StepKind;
  answer: string;
  elapsed_ms: number;
  timing_version?: number;
  /** ISO timestamp with a timezone, when the answer was given. */
  ts?: string;
  /** Stable id for replaying a buffered event: ^[A-Za-z0-9_-]{1,64}$. */
  id?: string;
}

export interface AnswerResult {
  step_id: string | null;
  accepted: boolean;
  duplicate: boolean;
  correct: boolean | null;
  /** "stale": the step is no longer served (content or settings changed); skip it. */
  rejected: 'stale' | 'invalid' | null;
  detail: string | null;
}

export interface AnswersResponse {
  results: AnswerResult[];
  /** The continuation of the last event's session, or null if there is none. */
  session: StudySession | null;
  /** The continuation repeats the step just answered; the client should not loop on it. */
  state_conflict: boolean;
}

/** What a learner can start from the home screen. */
export type SessionTarget =
  | { type: 'lesson'; id: string }
  | { type: 'lesson-practice'; id: string }
  | { type: 'review' }
  | { type: 'practice' };

// ------------------------------------------------------------------- transport
export const REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  /** HTTP status, or 0 when the server could not be reached or did not answer in time. */
  readonly status: number;
  /** The server's `detail`, or a generic description. */
  readonly detail: string;
  readonly timedOut: boolean;

  constructor(status: number, detail: string, timedOut = false) {
    super(status ? `${status}: ${detail}` : detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.timedOut = timedOut;
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

/** Called when a signed-in request comes back 401 (session expired or revoked). */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => { unauthorizedListeners.delete(listener); };
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  /** Sign-in calls answer 401 for a wrong password; that is not an expired session. */
  signedIn?: boolean;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string') {
      return (body as { detail: string }).detail;
    }
  } catch {
    // Not JSON: a proxy error page or an empty body.
  }
  return `Request failed (${response.status})`;
}

const TIMEOUT_DETAIL = 'The server took too long to respond.';

async function request<T>(path: string, { method = 'GET', body, signedIn = true }: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  // The timer covers the whole exchange, body included: a server that sends
  // headers and then stalls must not leave the learner waiting forever.
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    let response: Response;
    try {
      response = await fetch(path, {
        method,
        credentials: 'same-origin',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch {
      if (controller.signal.aborted) throw new ApiError(0, TIMEOUT_DETAIL, true);
      throw new ApiError(0, 'Could not reach the server. Check your connection.');
    }

    if (!response.ok) {
      if (response.status === 401 && signedIn) unauthorizedListeners.forEach(listener => listener());
      const detail = await errorDetail(response);
      if (controller.signal.aborted) throw new ApiError(0, TIMEOUT_DETAIL, true);
      throw new ApiError(response.status, detail);
    }
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      if (controller.signal.aborted) throw new ApiError(0, TIMEOUT_DETAIL, true);
      throw new ApiError(response.status, 'The server sent a response the app could not read.');
    }
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------------ endpoints
const post = <T>(path: string, body?: unknown, signedIn = true) => request<T>(path, { method: 'POST', body, signedIn });

export const getHealth = () => request<Health>('/api/health', { signedIn: false });
export const getConfig = () => request<Config>('/api/config', { signedIn: false });

export const register = (username: string, password: string) =>
  post<Account>('/api/register', { username, password }, false);
export const login = (username: string, password: string) =>
  post<Account>('/api/login', { username, password }, false);
export const logout = () => post<void>('/api/logout', undefined, false);

export const getAccount = () => request<Account>('/api/account');
/** 204 on success; the server replaces the session cookie and signs out every other device. */
export const changePassword = (current: string, next: string) =>
  post<void>('/api/password', { current, new: next });

export const getSettings = () => request<Settings>('/api/settings');
export const updateSettings = (patch: Partial<Settings>) => post<Settings>('/api/settings', patch);

export const getChapters = () => request<Chapters>('/api/chapters');
export const getProgress = () => request<Progress>('/api/progress');

export function startSession(target: SessionTarget): Promise<StudySession> {
  switch (target.type) {
    case 'lesson':
      return post<StudySession>(`/api/lessons/${encodeURIComponent(target.id)}/start`);
    case 'lesson-practice':
      return post<StudySession>(`/api/lessons/${encodeURIComponent(target.id)}/practice`);
    case 'review':
      return post<StudySession>('/api/review/start');
    case 'practice':
      return post<StudySession>('/api/practice/start');
  }
}

/** A session by id, or (without an id) the lesson in progress, else today's review. */
export const getSession = (sessionId?: string) =>
  request<StudySession>(sessionId ? `/api/session?session_id=${encodeURIComponent(sessionId)}` : '/api/session');

/** Send 1–100 buffered answers; each gets its own result, and the batch never fails as a whole. */
export const sendAnswers = (events: AnswerEvent[]) => post<AnswersResponse>('/api/answers', { events });

const GENERIC_ERROR = 'Something went wrong. Please try again.';

/** A server detail ("unknown session") as a sentence ("Unknown session."). */
export function asSentence(detail: string | null | undefined): string {
  const text = (detail ?? '').trim();
  if (!text) return GENERIC_ERROR;
  const sentence = text[0].toUpperCase() + text.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

/** A sentence for the learner: the server's detail, or what went wrong with the connection. */
export function describeError(error: unknown): string {
  return error instanceof ApiError ? asSentence(error.detail) : GENERIC_ERROR;
}
