import { expect, test } from '@playwright/test';
import { uploadFixture } from './helpers';

// CA-6 through the UI: wrong password does not block the document; the right one opens it.
test('encrypted PDF: wrong password, then "secret"', async ({ page }) => {
  await uploadFixture(page, 'encrypted.pdf');

  const dialog = page.getByRole('dialog', { name: /protected/i });
  await expect(dialog).toBeVisible();

  await page.getByTestId('password-input').fill('wrong');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('password-invalid')).toBeVisible();
  await expect(dialog).toBeVisible();

  await page.getByTestId('password-input').fill('secret');
  await page.keyboard.press('Enter');
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId('password-invalid')).toBeHidden();
  await expect(page.getByTestId('summary-text')).toContainText(/\d+\s+A\d\s+sheets?,/i);
});
