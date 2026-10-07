import {
  expect, test as base, type Browser, type BrowserContext, type BrowserContextOptions, type Locator, type Page,
  type Response,
} from '@playwright/test';

/** The demo course in content/, as the home screen titles it. */
export const COURSE_TITLE = 'How LLMs work';
/** The demo course's first lesson; its chapter enables every closed exercise kind. */
export const FIRST_LESSON = 'Tokens and embeddings';
export const PASSWORD = 'correct horse battery';

// ------------------------------------------------------------ page guard

/** A failed request the test expects, e.g. the signed-out probe on first load. */
export interface ExpectedFailure {
  status: number;
  path: string;
}

/** Signed out, the app asks for the home board once and is told 401: that is how it knows to show Sign in. */
export const SIGNED_OUT_PROBE: ExpectedFailure = { status: 401, path: '/api/chapters' };

/**
 * Collects everything that should never happen on a page: console errors,
 * uncaught exceptions, Content-Security-Policy violations and documents served
 * without the policy. The browser also logs a failed request as a console
 * error, so only failures listed in `expected` are let through. Judged at the
 * end of the test, so the order in which the events arrive does not matter.
 */
export class PageGuard {
  private readonly checks: (() => string[])[] = [];

  get problems(): string[] {
    return this.checks.flatMap(check => check());
  }

  async watch(page: Page, expected: ExpectedFailure[] = [SIGNED_OUT_PROBE]) {
    const failures = new Set<string>();
    const found: string[] = [];
    const consoleErrors: { text: string; source: string }[] = [];
    page.on('response', response => {
      const url = new URL(response.url());
      if (response.status() >= 400) failures.add(`${response.status()} ${url.pathname}`);
      if (response.request().resourceType() === 'document' && !response.headers()['content-security-policy']) {
        found.push(`document without a Content-Security-Policy: ${url.pathname}`);
      }
    });
    page.on('pageerror', error => found.push(`uncaught: ${error.message}`));
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push({ text: message.text(), source: message.location().url });
    });
    // Chromium reports violations in the console as well; the event is the
    // reliable signal and names the directive.
    await page.addInitScript(() => {
      document.addEventListener('securitypolicyviolation', event => {
        console.error(`CSP violation: ${event.violatedDirective} blocked ${event.blockedURI || 'inline'}`);
      });
    });
    const allowed = new Set(expected.map(item => `${item.status} ${item.path}`));
    this.checks.push(() => [
      ...found,
      ...consoleErrors
        .filter(({ text, source }) => {
          if (!text.startsWith('Failed to load resource') || !source) return true;
          const path = new URL(source).pathname;
          // Expected only if the server really answered that path with that status.
          return ![...allowed].some(key => key.endsWith(` ${path}`) && failures.has(key));
        })
        .map(({ text, source }) => `console error: ${text}${source ? ` (${source})` : ''}`),
    ]);
  }
}

/** Under --single-process (some sandboxes), Chromium survives only one context per browser. */
const SINGLE_PROCESS = (process.env.PW_CHROMIUM_ARGS ?? '').split(/\s+/).includes('--single-process');

interface Fixtures {
  /** A further, independent browser context with the project's options (another device). */
  openContext: () => Promise<BrowserContext>;
  guard: PageGuard;
}

const withContexts = base.extend<Pick<Fixtures, 'openContext'>>({
  openContext: async ({
    browser, playwright, launchOptions, contextOptions, baseURL, viewport, deviceScaleFactor, isMobile, hasTouch,
    colorScheme, userAgent,
  }, use) => {
    const options: BrowserContextOptions = {
      ...contextOptions, baseURL, viewport, deviceScaleFactor, isMobile, hasTouch, colorScheme, userAgent,
    };
    const opened: { context: BrowserContext; own?: Browser }[] = [];
    await use(async () => {
      if (!SINGLE_PROCESS) {
        const context = await browser.newContext(options);
        opened.push({ context });
        return context;
      }
      const own = await playwright.chromium.launch(launchOptions);
      const context = await own.newContext(options);
      opened.push({ context, own });
      return context;
    });
    for (const { context, own } of opened) {
      await context.close();
      await own?.close();
    }
  },
});

// With one context per browser, every test gets a browser of its own.
const isolated = SINGLE_PROCESS
  ? withContexts.extend({ context: async ({ openContext }, use) => { await use(await openContext()); } })
  : withContexts;

/**
 * `test` with a guard on every page: the default page is watched from the
 * start, further pages are added with `guard.watch(page)`, and the test fails
 * if any of them logged a problem.
 */
export const test = isolated.extend<Pick<Fixtures, 'guard'>>({
  guard: [async ({ page }, use) => {
    const guard = new PageGuard();
    await guard.watch(page);
    await use(guard);
    expect(guard.problems, 'console errors or CSP violations').toEqual([]);
  }, { auto: true }],
});
export { expect };

// --------------------------------------------------------------- accounts

let accounts = 0;

/** A username that is unique across runs and projects. */
export function uniqueName(prefix = 'e2e'): string {
  accounts += 1;
  return `${prefix}${Date.now().toString(36)}${accounts}${Math.random().toString(36).slice(2, 6)}`;
}

/** Sign up a fresh account through the UI and wait for the home board. */
export async function signUp(page: Page, username = uniqueName()): Promise<string> {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: COURSE_TITLE })).toBeVisible();
  await page.getByRole('button', { name: 'Create an account' }).click();
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expectHome(page);
  return username;
}

/** The home board, fully loaded. */
export async function expectHome(page: Page) {
  await expect(page.getByRole('heading', { level: 2, name: 'Lessons' })).toBeVisible();
}

/** A main tab: in the bottom bar on phones, in the header on wide screens. */
export const tab = (page: Page, name: 'Home' | 'Progress' | 'Settings') =>
  page.getByRole('navigation', { name: 'Main' }).getByRole('button', { name });

// ------------------------------------------------------------------ steps

export interface Step {
  id: string;
  kind: 'triage' | 'flash' | 'choice' | 'cloze' | 'assemble';
  card_id: string;
  prompt: string;
  answer: string;
  options?: string[];
  prefix?: string;
  suffix?: string;
  tiles?: string[];
}

interface SessionBody {
  steps?: Step[];
}

const SESSION_PATHS = /^\/api\/(lessons\/[^/]+\/(start|practice)|review\/start|practice\/start|session|answers)$/;

/**
 * Every step the server has sent to this page, by id, read from the API
 * responses as they arrive. The UI never says which option is right (only
 * the server grades), so this is how a test answers right or wrong on purpose:
 * from the same payload the client received, without hard-coding the course.
 */
export class StepLog {
  private readonly steps = new Map<string, Step>();

  constructor(page: Page) {
    page.on('response', response => void this.read(response));
  }

  private async read(response: Response) {
    const path = new URL(response.url()).pathname;
    if (!SESSION_PATHS.test(path) || !response.ok()) return;
    let body: { session?: SessionBody | null } & SessionBody;
    try {
      body = await response.json();
    } catch {
      return; // The page navigated away before the body was read.
    }
    const session = path === '/api/answers' ? body.session : body;
    for (const step of session?.steps ?? []) this.steps.set(step.id, step);
  }

  /** The step on screen, once its payload has been read. */
  async current(page: Page): Promise<Step> {
    const id = await page.locator('.session[data-phase="answering"]').getAttribute('data-step');
    expect(id, 'a step is on screen').toBeTruthy();
    await expect.poll(() => this.steps.has(id!), { message: `payload of ${id}` }).toBe(true);
    return this.steps.get(id!)!;
  }
}

const fold = (text: string) => text.toLowerCase().replace(/\s+/g, ' ').trim();

/** The one choice option the answer contains (the demo course writes them that way). */
export function rightOption(step: Step): string {
  const matches = (step.options ?? []).filter(option => fold(step.answer).includes(fold(option)));
  expect(matches, `exactly one option of ${step.id} is in its answer`).toHaveLength(1);
  return matches[0];
}

export function wrongOption(step: Step): string {
  const right = rightOption(step);
  return (step.options ?? []).find(option => option !== right)!;
}

/** The text the cloze gap cut out of the answer. */
export function gapText(step: Step): string {
  return step.answer.slice((step.prefix ?? '').length, step.answer.length - (step.suffix ?? '').length);
}

const session = (page: Page) => page.locator('.session');
const doneHeading = (page: Page) => page.locator('.done h1');

/** Wait until the server's next step (or the end screen) replaces `stepId`. */
export async function waitForNextStep(page: Page, stepId: string) {
  const next = page.locator(`.session[data-phase="answering"]:not([data-step="${stepId}"]), .done`);
  await expect(next).toBeVisible();
}

/** The verdict panel of a closed step, after the server graded it. */
export const verdict = (page: Page, title: 'Correct' | 'Not quite') =>
  page.getByRole('region', { name: title });

export async function pickOption(page: Page, step: Step, text: string) {
  const group = page.getByRole('group', { name: step.kind === 'cloze' ? 'Options for the gap' : 'Options' });
  await group.getByRole('button', { name: text, exact: true }).click();
}

export async function buildAnswer(page: Page, step: Step) {
  const words = page.getByRole('group', { name: 'Words' });
  for (const word of step.answer.split(/\s+/)) {
    // A word can occur twice; any copy of it is the same tile.
    await words.getByRole('button', { name: word, exact: true }).first().click();
  }
  await expect(page.locator('.assembled')).toHaveText(`Your answer: ${step.answer}`);
  await page.getByRole('button', { name: 'Check', exact: true }).click();
}

export interface Answered {
  step: Step;
  /** The server's verdict on a closed step; null for self-graded ones. */
  correct: boolean | null;
}

/**
 * Answer the step on screen through the UI: self-graded steps as known, closed
 * steps correctly unless `miss` is set (a choice is then answered wrongly).
 * Closed steps wait for the server's verdict and move on with Next.
 */
export async function answerStep(page: Page, steps: StepLog, { miss = false } = {}): Promise<Answered> {
  const step = await steps.current(page);
  const graded = page.waitForResponse(r => new URL(r.url()).pathname === '/api/answers' && r.request().method() === 'POST');
  switch (step.kind) {
    case 'triage':
      await page.getByRole('button', { name: 'I know', exact: true }).click();
      break;
    case 'flash':
      await page.getByRole('button', { name: 'Show answer' }).click();
      await page.getByRole('button', { name: 'Got it' }).click();
      break;
    case 'choice':
      await pickOption(page, step, miss ? wrongOption(step) : rightOption(step));
      break;
    case 'cloze':
      await pickOption(page, step, gapText(step));
      break;
    case 'assemble':
      await buildAnswer(page, step);
      break;
  }
  const response = await graded;
  expect(response.ok()).toBe(true);
  const { results } = (await response.json()) as { results: { step_id: string; correct: boolean | null }[] };
  expect(results.map(result => result.step_id)).toEqual([step.id]);
  const correct = results[0].correct;

  if (step.kind === 'choice' || step.kind === 'cloze' || step.kind === 'assemble') {
    await expect(verdict(page, correct ? 'Correct' : 'Not quite')).toBeVisible();
    await expect(session(page)).toHaveAttribute('data-phase', 'graded');
    await page.getByRole('button', { name: 'Next', exact: true }).click();
  }
  await waitForNextStep(page, step.id);
  return { step, correct };
}

/** Answer until the session ends; returns every step answered, in order. */
export async function finishSession(page: Page, steps: StepLog, { missFirstChoice = false, limit = 40 } = {}) {
  const answered: Answered[] = [];
  let missPending = missFirstChoice;
  await expect(page.locator('.session, .done')).toBeVisible();
  for (let index = 0; index < limit; index += 1) {
    if (await doneHeading(page).isVisible()) return answered;
    const step = await steps.current(page);
    const miss = missPending && step.kind === 'choice';
    if (miss) missPending = false;
    answered.push(await answerStep(page, steps, { miss }));
  }
  throw new Error(`the session did not end within ${limit} steps`);
}

/** The Done screen's heading. */
export const done = (page: Page): Locator => doneHeading(page);
