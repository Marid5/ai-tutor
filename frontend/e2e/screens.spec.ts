import type { Page } from '@playwright/test';
import {
  StepLog, answerStep, done, expect, expectHome, pickOption, signUp, tab, test, verdict, waitForNextStep, wrongOption,
} from './helpers';

/**
 * Not an assertion suite: walks a first lesson as a new learner would and
 * saves the screens shown in the README to docs/screenshots/. Run with
 * SHOTS=1 (skipped otherwise); phone screens in both themes, the home board
 * once more at desktop width.
 */
const DIR = '../docs/screenshots';
const SCHEMES = ['light', 'dark'] as const;
/** Time spent looking at each step, so the progress screen shows study time. */
const THINK_MS = 20_000;

test.skip(process.env.SHOTS !== '1', 'set SHOTS=1 to refresh docs/screenshots/');

/** The screen as it is now, once fonts are in and nothing is moving. */
async function shoot(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  // Neither the focus ring nor the hover left by the last click is part of the screen.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.mouse.move(0, 0);
  await page.screenshot({ path: `${DIR}/${name}.png`, animations: 'disabled', caret: 'hide' });
}

/** The same screen in the light and the dark theme ("System" follows the device). */
async function shootBoth(page: Page, name: string) {
  for (const colorScheme of SCHEMES) {
    await page.emulateMedia({ colorScheme });
    await shoot(page, `${name}-${colorScheme}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/**
 * The learner looks at the step for THINK_MS before answering. The page clock
 * jumps ahead without firing timers, so nothing waits in real time.
 */
async function think(page: Page) {
  const now = await page.evaluate(() => Date.now());
  await page.clock.setSystemTime(now + THINK_MS);
}

/**
 * Takes `shot` with the viewport stretched just enough that the bottom edge
 * (the phone tab bar, or the window on a wide screen) ends below a whole
 * lesson row instead of cutting one in half.
 */
async function withWholeRows(page: Page, shot: () => Promise<void>) {
  const size = page.viewportSize();
  if (!size) throw new Error('screenshots need a fixed viewport');
  const extra = await page.evaluate(() => {
    const bar = document.querySelector('.tabs');
    const edge = bar && getComputedStyle(bar).position === 'fixed' ? bar.getBoundingClientRect().top : window.innerHeight;
    const cut = Array.from(document.querySelectorAll('.chapter-head, .lesson'), row => row.getBoundingClientRect())
      .find(box => box.top < edge && box.bottom > edge);
    return cut ? Math.ceil(cut.bottom - edge) : 0;
  });
  await page.setViewportSize({ width: size.width, height: size.height + extra });
  await shot();
  await page.setViewportSize(size);
}

/**
 * The first lesson, with one choice missed on purpose so the ladder brings
 * the card back as a cloze; `onChoice` and `onCloze` see those screens before
 * they are answered.
 */
async function learnFirstLesson(page: Page, steps: StepLog, onChoice?: () => Promise<void>, onCloze?: () => Promise<void>) {
  await page.getByRole('region', { name: 'Next lesson' }).getByRole('button', { name: 'Start lesson' }).click();
  while ((await steps.current(page)).kind === 'triage') {
    await think(page);
    await answerStep(page, steps);
  }

  const first = await steps.current(page);
  expect(first.kind).toBe('choice');
  await onChoice?.();
  await think(page);
  await pickOption(page, first, wrongOption(first));
  await expect(verdict(page, 'Not quite')).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await waitForNextStep(page, first.id);

  let clozeSeen = false;
  for (let index = 0; index < 20 && !(await done(page).isVisible()); index += 1) {
    if (!clozeSeen && (await steps.current(page)).kind === 'cloze') {
      clozeSeen = true;
      await onCloze?.();
    }
    await think(page);
    await answerStep(page, steps);
  }
  expect(clozeSeen, 'the missed card came back as a cloze').toBe(true);
  await expect(done(page)).toHaveText('Lesson complete');
  await page.getByRole('button', { name: 'Back to home' }).click();
  await expectHome(page);
}

test('screens for the README', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const steps = new StepLog(page);
  await page.emulateMedia({ colorScheme: 'light' });
  // Before the app loads, so its step timer reads the controllable clock.
  await page.clock.install();
  await signUp(page);

  if (testInfo.project.name === 'desktop') {
    await learnFirstLesson(page, steps);
    await expect(page.getByText('3 of 33 cards ready')).toBeVisible();
    await withWholeRows(page, () => shoot(page, 'home-desktop'));
    return;
  }

  const answering = async () => {
    await expect(page.locator('.session')).toHaveAttribute('data-phase', 'answering');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  };
  await learnFirstLesson(
    page,
    steps,
    async () => { await answering(); await shootBoth(page, 'session-choice'); },
    async () => {
      await answering();
      await expect(page.getByRole('img', { name: 'Gap' })).toBeVisible();
      await shootBoth(page, 'session-cloze');
    },
  );

  await expect(page.getByText('3 of 33 cards ready')).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await withWholeRows(page, () => shootBoth(page, 'home'));

  await tab(page, 'Progress').click();
  await expect(page.getByRole('heading', { level: 1, name: 'Your progress' })).toBeVisible();
  await expect(page.locator('.problem-prompt')).not.toHaveCount(0);
  await expect(page.locator('.stat-value').getByText(/^[1-9]\d* min$/)).toBeVisible();
  await shootBoth(page, 'progress');
});
