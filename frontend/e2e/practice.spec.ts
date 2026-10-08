import type { Page } from '@playwright/test';
import { FIRST_LESSON, StepLog, done, expect, expectHome, finishSession, signUp, test } from './helpers';

interface Board {
  review_due: number;
  practice_available: boolean;
  practice_card_count: number;
}

/** Sign up and finish the first lesson without a miss; returns the home board the server sent next. */
async function learnFirstLesson(page: Page, steps: StepLog): Promise<Board> {
  await signUp(page);
  await page.getByRole('region', { name: 'Next lesson' }).getByRole('button', { name: 'Start lesson' }).click();
  await finishSession(page, steps);
  await expect(done(page)).toHaveText('Lesson complete');
  const board = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chapters');
  await page.getByRole('button', { name: 'Back to home' }).click();
  await expectHome(page);
  return (await (await board).json()) as Board;
}

test('practice from the home screen mixes cards from finished lessons', async ({ page }) => {
  test.setTimeout(90_000);
  const steps = new StepLog(page);
  const board = await learnFirstLesson(page, steps);
  expect(board.practice_available).toBe(true);
  expect(board.practice_card_count).toBe(4);

  await page.getByRole('button', { name: 'Practice 4 cards from finished lessons' }).click();
  await expect(page.getByRole('button', { name: 'Leave practice' })).toBeVisible();
  // Practice is checks only: known cards are not triaged again.
  const answered = await finishSession(page, steps);
  expect(answered.length).toBeGreaterThan(0);
  expect(answered.every(item => item.step.kind !== 'triage')).toBe(true);
  expect(answered.every(item => item.correct !== false)).toBe(true);
  await expect(done(page)).toHaveText('Practice complete');
  await page.getByRole('button', { name: 'Back to home' }).click();
  await expectHome(page);
});

test('a finished lesson replays as practice from its row', async ({ page }) => {
  test.setTimeout(90_000);
  const steps = new StepLog(page);
  await learnFirstLesson(page, steps);

  const row = page.locator('.lesson', { hasText: FIRST_LESSON });
  await expect(row).toContainText('Done');
  await expect(row).toContainText('4 cards · 4 ready');
  const opened = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/practice') && r.request().method() === 'POST');
  await row.click();
  expect((await opened).ok()).toBe(true);
  await expect(page.getByRole('button', { name: 'Leave practice' })).toBeVisible();
  await expect(page.locator('.session-title')).toHaveText(`Practice: ${FIRST_LESSON}`);

  const answered = await finishSession(page, steps);
  expect(new Set(answered.map(item => item.step.card_id)).size).toBe(4);
  await expect(done(page)).toHaveText('Practice complete');
});

test('the Review entry point is shut on the day a lesson is learned', async ({ page }) => {
  test.setTimeout(90_000);
  const steps = new StepLog(page);
  const board = await learnFirstLesson(page, steps);

  // Nothing is due on the day a lesson is learned: the tile says so and stays shut.
  expect(board.review_due).toBe(0);
  await expect(page.getByRole('button', { name: 'Review Nothing due today' })).toBeDisabled();
});

// The review entry is covered above through its disabled state on the learning day:
// opening a due review needs a card scheduled for a later day, which a same-day run
// cannot reach. The review flow itself is covered by the backend tests.
