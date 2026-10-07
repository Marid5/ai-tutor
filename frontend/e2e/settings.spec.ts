import type { Page } from '@playwright/test';
import { PASSWORD, SIGNED_OUT_PROBE, expect, expectHome, signUp, tab, test } from './helpers';

/** Relative luminance of a computed CSS colour, 0 (black) to 1 (white). */
const luminance = (rgb: string) => {
  const [r, g, b] = (rgb.match(/\d+(\.\d+)?/g) ?? ['0', '0', '0']).map(Number);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
};
const pageColours = (page: Page) =>
  page.evaluate(() => {
    const style = getComputedStyle(document.body);
    return { ground: style.backgroundColor, text: style.color };
  });

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 2, name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test.describe('theme', () => {
  // The system says light, so a dark page can only come from the saved choice.
  test.use({ colorScheme: 'light' });

  test('a chosen theme survives a reload and "System" follows the device again', async ({ page }) => {
    await signUp(page);
    expect(luminance((await pageColours(page)).ground)).toBeGreaterThan(0.8);

    await tab(page, 'Settings').click();
    await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
    const appearance = page.getByRole('group', { name: 'Theme' });
    await expect(appearance.getByRole('radio', { name: 'System' })).toBeChecked();
    await appearance.getByRole('radio', { name: 'Dark' }).check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

    await page.reload();
    await expectHome(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    const dark = await pageColours(page);
    expect(luminance(dark.ground)).toBeLessThan(0.15);
    expect(luminance(dark.text)).toBeGreaterThan(0.75);
    await tab(page, 'Settings').click();
    await expect(appearance.getByRole('radio', { name: 'Dark' })).toBeChecked();

    await appearance.getByRole('radio', { name: 'System' }).check();
    await page.reload();
    await expectHome(page);
    await expect(page.locator('html')).not.toHaveAttribute('data-theme', /.+/);
    expect(luminance((await pageColours(page)).ground)).toBeGreaterThan(0.8);
  });
});

test('the hint setting is saved on the server and shows hints in a lesson', async ({ page }) => {
  await signUp(page);
  await tab(page, 'Settings').click();
  const hints = page.getByRole('switch', { name: 'Show hints by default' });
  await expect(hints).toHaveAttribute('aria-checked', 'false');
  const saved = page.waitForResponse(r => new URL(r.url()).pathname === '/api/settings' && r.request().method() === 'POST');
  await hints.click();
  expect((await (await saved).json()).show_hint_by_default).toBe(true);
  await expect(hints).toHaveAttribute('aria-checked', 'true');

  await page.reload();
  await expectHome(page);
  await tab(page, 'Settings').click();
  await expect(hints).toHaveAttribute('aria-checked', 'true');

  // The first card of the demo course has a hint; it is shown straight away.
  await tab(page, 'Home').click();
  await page.getByRole('region', { name: 'Next lesson' }).getByRole('button', { name: 'Start lesson' }).click();
  await expect(page.locator('.hint')).toContainText('Hint');
  await expect(page.getByRole('button', { name: 'Show hint' })).toHaveCount(0);
});

test('changing the password signs out the other device', async ({ page, openContext, guard }) => {
  const username = await signUp(page);
  const NEW_PASSWORD = 'a different passphrase';

  // The same account on a second device.
  const other = await (await openContext()).newPage();
  await guard.watch(other, [
    SIGNED_OUT_PROBE,
    // The second device's next request after the change is refused.
    { status: 401, path: '/api/progress' },
    // Signing in there with the old password is refused too.
    { status: 401, path: '/api/login' },
  ]);
  await signIn(other, username, PASSWORD);
  await expectHome(other);

  await tab(page, 'Settings').click();
  await page.getByLabel('Current password').fill(PASSWORD);
  await page.getByLabel('New password', { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel('Repeat new password').fill(NEW_PASSWORD);
  await page.getByRole('button', { name: 'Change password' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Password changed' }))
    .toHaveText('Password changed. Other devices have been signed out.');

  // This device stays signed in.
  await tab(page, 'Progress').click();
  await expect(page.getByText('cards ready')).toBeVisible();

  // The other device is sent to Sign in on its next request.
  await tab(other, 'Progress').click();
  await expect(other.getByRole('heading', { level: 2, name: 'Sign in' })).toBeVisible();
  await expect(other.getByRole('navigation', { name: 'Main' })).toHaveCount(0);

  // The old password no longer works there; the new one does.
  await other.getByLabel('Username').fill(username);
  await other.getByLabel('Password').fill(PASSWORD);
  await other.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(other.getByRole('alert')).toHaveText('Wrong username or password.');
  await other.getByLabel('Password').fill(NEW_PASSWORD);
  await other.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expectHome(other);
});
