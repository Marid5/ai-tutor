// Active time on a step: how long the learner was actually looking at it.
// Time counts only while the page is visible and focused, and only up to
// ACTIVE_IDLE_MS after the last interaction, so a step left open over lunch
// does not report an hour of study. An interaction resumes the count.

export const ACTIVE_IDLE_MS = 45_000;

export class ActiveStepTimer {
  private total = 0;
  private activeSince: number | null = null;
  private lastInteraction = 0;
  private visible = true;
  private focused = true;

  constructor(
    private readonly now: () => number = Date.now,
    private readonly idleMs = ACTIVE_IDLE_MS,
  ) {}

  /** Start counting a new step from zero. */
  start() {
    const now = this.now();
    this.total = 0;
    this.lastInteraction = now;
    this.activeSince = this.visible && this.focused ? now : null;
  }

  reset() {
    this.start();
  }

  /** A key press, click or touch: the learner is here. */
  interact() {
    const now = this.now();
    this.sync(now);
    this.lastInteraction = now;
    this.activeSince = this.visible && this.focused ? now : null;
  }

  setFocused(focused: boolean) {
    this.setAttention('focused', focused);
  }

  setVisible(visible: boolean) {
    this.setAttention('visible', visible);
  }

  /** Whole milliseconds of attention since `start`. */
  elapsed() {
    this.sync(this.now());
    return Math.max(0, Math.round(this.total));
  }

  // Losing attention pauses the count; regaining it does not resume it on its
  // own (a window can be focused with nobody in front of it): the next
  // interaction does.
  private setAttention(key: 'focused' | 'visible', enabled: boolean) {
    const now = this.now();
    this.sync(now);
    const changed = this[key] !== enabled;
    this[key] = enabled;
    if (!enabled || changed) {
      this.activeSince = null;
    }
  }

  private sync(now: number) {
    if (this.activeSince === null) return;
    const activeUntil = Math.min(now, this.lastInteraction + this.idleMs);
    this.total += Math.max(0, activeUntil - this.activeSince);
    this.activeSince = activeUntil < now || now >= this.lastInteraction + this.idleMs ? null : now;
  }
}
