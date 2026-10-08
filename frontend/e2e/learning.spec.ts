import type { Page } from '@playwright/test';
import {
  COURSE_TITLE, FIRST_LESSON, StepLog, answerStep, done, expect, expectHome, finishSession, signUp, tab, test,
} from './helpers';

interface ProgressData {
  cards_ready: number;
  cards_total: number;
  retention_30d: number;
  checks_30d: number;
  forecast_7d: { day: string; amount: number }[];
  problem_cards: { id: string; prompt: string; answer: string; again_count: number }[];
  session_minutes: number;
  lessons_total: number;
  lessons_completed: number;
}

const noSideScroll = async (page: Page) => {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'the page scrolls sideways').toBeLessThanOrEqual(0);
};

test('a new learner finishes the first lesson, resumes after a reload and sees the server\'s numbers', async ({ page }) => {
  test.setTimeout(120_000);
  const steps = new StepLog(page);
  await signUp(page);

  // A fresh account: nothing ready, the first lesson is next, review and practice are not open yet.
  await expect(page.getByText('0 of 33 cards ready')).toBeVisible();
  const next = page.getByRole('region', { name: 'Next lesson' });
  await expect(next.getByRole('heading', { name: FIRST_LESSON })).toBeVisible();
  await expect(page.getByRole('button', { name: /Nothing due today/ })).toBeDisabled();
  await expect(page.getByRole('button', { name: /Finish a lesson first/ })).toBeDisabled();
  await noSideScroll(page);

  await next.getByRole('button', { name: 'Start lesson' }).click();
  await expect(page.getByRole('button', { name: 'Leave lesson' })).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Cards done' })).toHaveAttribute('max', '4');
  await noSideScroll(page);

  // Two answers, then a reload in the middle of the lesson.
  await answerStep(page, steps);
  await answerStep(page, steps);
  const before = await steps.current(page);
  await page.reload();

  // A reload lands on Home, which offers to continue; the server serves the very same step.
  const resume = page.getByRole('region', { name: 'Continue lesson' });
  await expect(resume.getByRole('heading', { name: FIRST_LESSON })).toBeVisible();
  await resume.getByRole('button', { name: 'Continue lesson' }).click();
  await expect(page.locator('.session')).toHaveAttribute('data-step', before.id);
  await expect(page.getByRole('heading', { level: 1, name: before.prompt })).toBeVisible();

  // The rest of the lesson, missing the first closed check on purpose.
  const answered = await finishSession(page, steps, { missFirstChoice: true });
  const misses = answered.filter(item => item.correct === false);
  expect(misses, 'exactly one deliberate miss').toHaveLength(1);
  const missed = misses[0].step.card_id;
  // The ladder: the missed card came back on the other closed kinds before the lesson could end.
  const ladder = answered.filter(item => item.step.card_id === missed && item.step.id.startsWith('v2:'));
  expect(ladder.map(item => item.step.kind)).toEqual(expect.arrayContaining(['choice', 'cloze', 'assemble']));
  expect(ladder.every(item => item.correct === true)).toBe(true);

  await expect(done(page)).toHaveText('Lesson complete');
  await expect(page.getByText('Your progress is saved: 4 of 4 cards done.')).toBeVisible();
  await expect(page.getByRole('region', { name: 'This sitting' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to home' }).click();
  await expectHome(page);
  await expect(page.getByRole('heading', { level: 1, name: COURSE_TITLE })).toBeVisible();

  // Progress shows exactly what the server reports.
  const progressResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/progress');
  await tab(page, 'Progress').click();
  const data = (await (await progressResponse).json()) as ProgressData;
  expect(data.lessons_completed).toBe(1);
  expect(data.checks_30d).toBeGreaterThan(0);
  const stats = page.locator('.stat');
  await expect(stats.nth(0)).toHaveText(`${data.cards_ready}/${data.cards_total}cards ready`);
  await expect(stats.nth(1)).toHaveText(`${data.retention_30d}%correct checks`);
  await expect(stats.nth(2)).toHaveText(`${data.session_minutes} minstudied, 30 days`);
  await expect(page.locator('.row', { hasText: 'Lessons completed' })).toContainText(`${data.lessons_completed}/${data.lessons_total}`);
  await expect(page.locator('.row', { hasText: 'Checks in the last 30 days' })).toContainText(String(data.checks_30d));
  await expect(page.locator('.forecast li')).toHaveCount(data.forecast_7d.length);
  await expect(page.locator('.forecast-amount')).toHaveText(data.forecast_7d.map(item => String(item.amount)));
  await expect(page.locator('.problem-prompt')).toHaveText(data.problem_cards.map(card => card.prompt));
  expect(data.problem_cards.map(card => card.id)).toContain(missed);
  await noSideScroll(page);

  // Home: the entry points match the server's board.
  const boardResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chapters');
  await tab(page, 'Home').click();
  const board = (await (await boardResponse).json()) as {
    cards_ready: number; review_due: number; review_session_size: number;
    practice_available: boolean; practice_card_count: number;
  };
  await expectHome(page);
  await expect(page.getByText(`${board.cards_ready} of 33 cards ready`)).toBeVisible();
  expect(board.practice_available).toBe(true);
  const practice = page.getByRole('button', { name: `Practice ${board.practice_card_count} cards from finished lessons` });
  await expect(practice).toBeEnabled();
  // The Review tile follows the server's board. This lesson had a deliberate miss: the card was
  // rated Again and FSRS schedules it for the same day, so cards can legitimately be due now.
  // (practice.spec.ts covers the miss-free lesson, where nothing is due and the tile is shut.)
  const cards = (n: number) => (n === 1 ? '1 card' : `${n} cards`);
  if (board.review_due > 0) {
    const review = page.getByRole('button', { name: `Review ${cards(board.review_due)}` });
    await expect(review).toBeEnabled();
    const started = page.waitForResponse(r => new URL(r.url()).pathname === '/api/review/start' && r.request().method() === 'POST');
    await review.click();
    const session = (await (await started).json()) as { mode: string; steps: unknown[]; total_cards: number };
    // One sitting holds at most the server's per-session limit; the rest stay due for the next one.
    const expected = Math.min(board.review_due, board.review_session_size);
    expect(session.mode).toBe('scheduled_review');
    expect(session.total_cards).toBe(expected);
    expect(session.steps.length, 'the review sitting has something to study').toBeGreaterThan(0);
    await expect(page.getByRole('button', { name: 'Leave review' })).toBeVisible();
    await expect(page.locator('.session')).toHaveAttribute('data-phase', 'answering');
    await expect(page.locator('.session-count')).toHaveText(`0 / ${cards(expected)}`);
    await page.getByRole('button', { name: 'Leave review' }).click();
    await expectHome(page);
  } else {
    await expect(page.getByRole('button', { name: 'Review Nothing due today' })).toBeDisabled();
  }

  // Practice opens a session; leaving it returns home with the board intact.
  await practice.click();
  await expect(page.getByRole('button', { name: 'Leave practice' })).toBeVisible();
  await expect(page.locator('.session')).toHaveAttribute('data-kind', /^(choice|cloze|assemble)$/);
  await page.getByRole('button', { name: 'Leave practice' }).click();
  await expectHome(page);
});
