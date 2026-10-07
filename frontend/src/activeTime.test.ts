import { describe, expect, it } from 'vitest';
import { ACTIVE_IDLE_MS, ActiveStepTimer } from './activeTime';

describe('ActiveStepTimer', () => {
  it('counts attention only up to the idle limit and resumes on interaction', () => {
    let now = 0;
    const timer = new ActiveStepTimer(() => now);
    timer.start();

    now = ACTIVE_IDLE_MS + 5_000;
    expect(timer.elapsed()).toBe(ACTIVE_IDLE_MS);
    timer.interact();
    now += 5_000;
    expect(timer.elapsed()).toBe(ACTIVE_IDLE_MS + 5_000);
  });

  it('pauses while the window is blurred or hidden and resumes only after an interaction', () => {
    let now = 0;
    const timer = new ActiveStepTimer(() => now);
    timer.start();
    now = 10_000;
    timer.setFocused(false);
    now = 30_000;
    timer.setFocused(true);
    now = 35_000;
    expect(timer.elapsed()).toBe(10_000);
    timer.interact();
    now = 40_000;
    timer.setVisible(false);
    now = 55_000;
    timer.setVisible(true);
    now = 60_000;
    expect(timer.elapsed()).toBe(15_000);
    timer.interact();
    now = 65_000;

    expect(timer.elapsed()).toBe(20_000);
  });

  it('starts from zero for the next step', () => {
    let now = 0;
    const timer = new ActiveStepTimer(() => now);
    timer.start();
    now = 12_000;
    expect(timer.elapsed()).toBe(12_000);
    timer.reset();
    now = 15_000;
    expect(timer.elapsed()).toBe(3_000);
  });

  it('does not count a step that starts while the page is hidden', () => {
    let now = 0;
    const timer = new ActiveStepTimer(() => now);
    timer.setVisible(false);
    timer.start();
    now = 20_000;
    expect(timer.elapsed()).toBe(0);
  });
});
