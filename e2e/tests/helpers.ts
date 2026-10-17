import { expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { PDFDocument } from 'pdf-lib';

/** Fixtures are mounted read-only at /app/fixtures (compose.dev.yml). */
export const FIXTURES = process.env.E2E_FIXTURES_DIR ?? '/app/fixtures';

/** Uploads a fixture through the home dropzone's hidden file input. */
export async function uploadFixture(page: Page, name: string): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('a0td.lang', 'en');
    } catch {
      /* ignore */
    }
  });
  await page.goto('/');
  const input = page.locator('input[type="file"]').first();
  // The API reuses documents by sha256, so give the encrypted fixture unique trailing bytes per run (state-independent).
  if (name.startsWith('encrypted')) {
    const buf = Buffer.concat([readFileSync(path.join(FIXTURES, name)), Buffer.from(`
% e2e ${Date.now()}
`)]);
    await input.setInputFiles({ name, mimeType: 'application/pdf', buffer: buf });
  } else {
    await input.setInputFiles(path.join(FIXTURES, name));
  }
  await expect(page).toHaveURL(/\/documents\//);
}

export async function pdfPageCount(buf: Buffer): Promise<number> {
  const doc = await PDFDocument.load(buf);
  return doc.getPageCount();
}

/** Waits for the result page to offer a download; fails fast if the job failed. */
export async function downloadResult(page: Page): Promise<Buffer> {
  const download = page.getByTestId('download').first();
  const failed = page.getByTestId('job-failed');
  const winner = await Promise.race([
    download.waitFor({ state: 'visible', timeout: 90_000 }).then(() => 'ok' as const),
    failed.waitFor({ state: 'visible', timeout: 90_000 }).then(() => 'failed' as const),
  ]);
  if (winner === 'failed') {
    throw new Error(`Job failed: ${(await failed.innerText()).replace(/\s+/g, ' ')}`);
  }
  const [dl] = await Promise.all([page.waitForEvent('download'), download.click()]);
  const stream = await dl.createReadStream();
  const chunks: Buffer[] = [];
  for await (const c of stream) chunks.push(c as Buffer);
  return Buffer.concat(chunks);
}

export function jobIdFromUrl(page: Page): string {
  const m = /\/jobs\/([^/?#]+)/.exec(page.url());
  if (!m) throw new Error(`not a job URL: ${page.url()}`);
  return m[1];
}
