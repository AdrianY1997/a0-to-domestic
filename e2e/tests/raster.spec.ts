import { expect, test } from '@playwright/test';
import { uploadFixture } from './helpers';

// CA-4 through the UI: JPEG without DPI -> size dialog; cancelling assumes A0 (120.8 DPI) and warns LOW_DPI.
test('raster without DPI: cancel -> assumed A0, 120.8 DPI, LOW_DPI warning', async ({ page }) => {
  await uploadFixture(page, 'photo_nodpi.jpg');

  const dialog = page.getByRole('dialog', { name: /what size/i });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /cancel/i }).click();
  await expect(dialog).toBeHidden();

  await expect(page.getByTestId('warning-PHYSICAL_SIZE_ASSUMED')).toBeVisible();
  await expect(page.getByTestId('warning-LOW_DPI')).toBeVisible();
  await expect(page.getByTestId('effective-dpi')).toContainText('120.8');
  await expect(page.getByTestId('summary-text')).toContainText(/840[.,]7\s?×\s?1[ ,.]?189/);
});

test('raster: fit to one sheet clears LOW_DPI', async ({ page }) => {
  await uploadFixture(page, 'photo_nodpi.jpg');
  const dialog = page.getByRole('dialog', { name: /what size/i });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /cancel/i }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId('warning-LOW_DPI')).toBeVisible();

  await page.getByTestId('scale-mode-fit_sheet').check({ force: true });
  await expect(page.getByTestId('summary-text')).toContainText(/1\s+A4\s+sheet/i);
  await expect(page.getByTestId('warning-LOW_DPI')).toBeHidden();
});
