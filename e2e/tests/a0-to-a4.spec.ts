import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { PDFDocument } from 'pdf-lib';
import { downloadResult, FIXTURES, uploadFixture } from './helpers';

// CA-1 through the UI: A0 vector -> 21 A4 landscape sheets -> PDF with 22 pages (21 + assembly plan).
test('A0 vector PDF -> 21 A4 landscape sheets -> 22-page PDF', async ({ page }) => {
  await uploadFixture(page, 'a0_vector_grid.pdf');

  const summary = page.getByTestId('summary-text');
  await expect(summary).toContainText(/21\s+A4\s+sheets,\s+landscape/i);
  await expect(summary).toContainText('3×7');

  await page.getByTestId('generate').click();
  await expect(page).toHaveURL(/\/jobs\//);

  const buf = await downloadResult(page);
  const doc = await PDFDocument.load(buf);
  expect(doc.getPageCount()).toBe(22);
  for (let i = 0; i < 21; i++) {
    const { width, height } = doc.getPage(i).getSize();
    // 297x210 mm (the brief says pt; A4 landscape is 841.89x595.28 pt)
    expect(Math.abs((width * 25.4) / 72 - 297), `page ${i} width`).toBeLessThanOrEqual(0.05);
    expect(Math.abs((height * 25.4) / 72 - 210), `page ${i} height`).toBeLessThanOrEqual(0.05);
  }
  const fixtureSize = fs.statSync(path.join(FIXTURES, 'a0_vector_grid.pdf')).size;
  expect(buf.length).toBeLessThanOrEqual(1.2 * fixtureSize + 200 * 1024);
});
