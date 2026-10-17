import { expect, test } from '@playwright/test';
import { PDFDocument } from 'pdf-lib';
import { downloadResult, jobIdFromUrl, uploadFixture } from './helpers';

// CA-2 through the UI: fit to one sheet gives 23.78 %, content box 200.0 x 282.8 mm centered.
test('fit to one sheet shows 23.78 % and a centered 200 x 282.8 mm content box', async ({
  page,
}) => {
  await uploadFixture(page, 'a0_vector_grid.pdf');
  await expect(page.getByTestId('summary-text')).toContainText(/21\s+A4/i);

  await page.getByTestId('scale-mode-fit_sheet').check({ force: true });
  const summary = page.getByTestId('summary-text');
  await expect(summary).toContainText(/1\s+A4\s+sheet,\s+portrait/i);
  await expect(summary).toContainText(/23[.,]78\s?%/);

  await page.getByTestId('generate').click();
  await expect(page).toHaveURL(/\/jobs\//);
  const jobId = jobIdFromUrl(page);
  const buf = await downloadResult(page);

  const doc = await PDFDocument.load(buf);
  const { width, height } = doc.getPage(0).getSize();
  expect(Math.abs((width * 25.4) / 72 - 210)).toBeLessThanOrEqual(0.05);
  expect(Math.abs((height * 25.4) / 72 - 297)).toBeLessThanOrEqual(0.05);

  const res = await page.request.get(`/api/v1/jobs/${jobId}`);
  expect(res.ok()).toBeTruthy();
  const job = await res.json();
  const plan = job.plans[0];
  const [aw, ah] = plan.assembled_size_mm as [number, number];
  const [cw, ch] = plan.content_mm as [number, number];
  const [ox, oy] = plan.origin_mm as [number, number];
  expect(aw).toBeCloseTo(200.0, 1);
  expect(ah).toBeCloseTo(282.8, 1);
  expect(Math.abs(ox + (cw - aw) / 2)).toBeLessThan(0.1);
  expect(Math.abs(oy + (ch - ah) / 2)).toBeLessThan(0.1);
});
