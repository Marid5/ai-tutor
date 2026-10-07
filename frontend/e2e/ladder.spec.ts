import {
  FIRST_LESSON, StepLog, answerStep, buildAnswer, done, escapeRegExp, expect, gapText, pickOption, rightOption, signUp, test,
  verdict, waitForNextStep, wrongOption,
} from './helpers';

/**
 * A missed check opens the ladder: the card comes back as the other closed
 * exercises its chapter enables (choice, cloze, assemble), then as a flash
 * card, and only then counts as done. The first chapter of the demo course
 * enables all of them, so its first lesson walks the whole ladder.
 */
test('a missed card climbs the ladder through cloze and assemble before it counts as done', async ({ page }) => {
  test.setTimeout(120_000);
  const steps = new StepLog(page);
  await signUp(page);
  await page.getByRole('region', { name: 'Next lesson' }).getByRole('button', { name: 'Start lesson' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

  // Triage every card as known.
  while ((await steps.current(page)).kind === 'triage') await answerStep(page, steps);

  // The first closed check, answered wrongly on purpose.
  const first = await steps.current(page);
  expect(first.kind).toBe('choice');
  await expect(page.locator('.step-head .eyebrow')).toHaveText('Choose the answer');
  const wrong = wrongOption(first);
  await pickOption(page, first, wrong);
  const miss = verdict(page, 'Not quite');
  await expect(miss).toBeVisible();
  // The verdict is the server's: the chosen option is marked wrong, the answer and note are shown.
  const chosen = page.getByRole('group', { name: 'Options' }).locator('[data-state="wrong"]');
  await expect(chosen).toHaveAccessibleName(new RegExp(`^${escapeRegExp(wrong)}\\s*, incorrect$`));
  await expect(miss).toContainText(first.answer);
  await expect(miss).toContainText('This card will come back for another try.');
  await expect(page.getByRole('status').filter({ hasText: 'Not quite.' })).toHaveText(`Not quite. ${first.answer}`);
  await expect(page.locator('.session-count')).toHaveText('0 / 4 cards');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await waitForNextStep(page, first.id);
  // Not re-served at once: another card's check comes first.
  expect((await steps.current(page)).card_id).not.toBe(first.card_id);

  const ladder: string[] = [];
  for (let index = 0; index < 20 && !(await done(page).isVisible()); index += 1) {
    const step = await steps.current(page);
    if (step.card_id !== first.card_id) {
      await answerStep(page, steps);
      continue;
    }
    ladder.push(step.kind);
    if (step.kind === 'choice') {
      await pickOption(page, step, rightOption(step));
    } else if (step.kind === 'cloze') {
      await expect(page.locator('.step-head .eyebrow')).toHaveText('Fill in the gap');
      await expect(page.getByRole('img', { name: 'Gap' })).toBeVisible();
      const prefix = (step.prefix ?? '').trim();
      if (prefix) await expect(page.locator('.session')).toContainText(prefix);
      await pickOption(page, step, gapText(step));
    } else if (step.kind === 'assemble') {
      await expect(page.locator('.step-head .eyebrow')).toHaveText('Build the answer');
      await expect(page.locator('.assembled')).toHaveText('Your answer: Pick the words in order');
      const words = page.getByRole('group', { name: 'Words' }).getByRole('button');
      const total = step.answer.split(/\s+/).length;
      await expect(words).toHaveCount(total);
      // A word picked by mistake goes back to the pool.
      await words.first().click();
      await expect(words).toHaveCount(total - 1);
      await page.getByRole('button', { name: 'Remove last word' }).click();
      await expect(words).toHaveCount(total);
      await buildAnswer(page, step);
    } else if (step.kind === 'flash') {
      await expect(page.locator('.step-head .eyebrow')).toHaveText('Recall the answer');
      // Until the flash card is done, the missed card is not counted.
      await expect(page.locator('.session-count')).toHaveText('3 / 4 cards');
      await page.getByRole('button', { name: 'Show answer' }).click();
      await expect(page.locator('.session')).toContainText(step.answer);
      await page.getByRole('button', { name: 'Got it' }).click();
      await waitForNextStep(page, step.id);
      continue;
    } else {
      throw new Error(`unexpected ${step.kind} step for the missed card`);
    }
    await expect(verdict(page, 'Correct')).toBeVisible();
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await waitForNextStep(page, step.id);
  }

  // Every rung once. The order is the server's queue and not pinned here.
  expect([...ladder].sort()).toEqual(['assemble', 'choice', 'cloze', 'flash']);
  await expect(done(page)).toHaveText('Lesson complete');
  await expect(page.getByText('Your progress is saved: 4 of 4 cards done.')).toBeVisible();
  // Four primary checks and three on the ladder, one of them missed.
  const sitting = page.getByRole('region', { name: 'This sitting' });
  await expect(sitting.locator('.stat-value')).toHaveText(['7', '86%']);
  await page.getByRole('button', { name: 'Back to home' }).click();
  // The missed card is not ready yet; the other three are.
  await expect(page.getByText('3 of 33 cards ready')).toBeVisible();
  await expect(page.locator('.lesson', { hasText: FIRST_LESSON })).toContainText('Done');
});
