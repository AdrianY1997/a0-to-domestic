import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobOut } from '../../api/types';
import { Providers } from '../../app/Providers';
import i18n from '../../i18n';
import { PrintToPrinter } from './PrintToPrinter';

// The app shares one QueryClient; tests need an empty cache each time.
const freshClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

const sheet = (id: string, row: number, col: number, idx: number | null) => ({
  id,
  row,
  col,
  print_index: idx,
});
const job = {
  id: 'job-1',
  document_id: 'doc-1',
  kind: 'render',
  status: 'succeeded',
  test_sheet_only: false,
  settings: { target: { printer_id: 'ipp-2' } },
  outputs: [{ name: 'out.pdf', kind: 'pdf', size_bytes: 10, pages: 3 }],
  plans: [{ sheets: [sheet('A1', 0, 0, 1), sheet('A2', 0, 1, 2), sheet('B1', 1, 0, null)] }],
} as unknown as JobOut;

const printer = (id: string, name: string, ipp_uri: string | null) => ({
  id,
  name,
  ipp_uri,
  builtin: false,
});

let ippEnabled = true;
let printersList: unknown[] = [];
let printResult: () => Promise<Response>;
let calls: { url: string; method: string; body: unknown }[] = [];

const fetchMock = vi.fn((url: string, init?: RequestInit) => {
  const method = init?.method ?? 'GET';
  const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push({ url, method, body });
  if (url.endsWith('/info')) return json({ ipp_enabled: ippEnabled });
  if (url.endsWith('/printers')) return json(printersList);
  if (url.endsWith('/print') && method === 'POST') return printResult();
  if (url.endsWith('/jobs/print-1'))
    return json({
      id: 'print-1',
      kind: 'print',
      status: 'succeeded',
      progress: 100,
      error_code: null,
      error_params: {},
      printing: { printer_job_id: 41, printer_job_state: 'completed' },
    });
  return json({}, 404);
});

function renderIt(j: JobOut = job) {
  return render(
    <Providers>
      <QueryClientProvider client={freshClient()}>
        <MemoryRouter>
          <PrintToPrinter job={j} />
        </MemoryRouter>
      </QueryClientProvider>
    </Providers>,
  );
}

const printersFetched = () => calls.filter((c) => c.url.endsWith('/printers')).length;

beforeEach(async () => {
  await i18n.changeLanguage('es');
  window.localStorage.clear();
  calls = [];
  ippEnabled = true;
  printersList = [
    printer('plain', 'Sin IPP', null),
    printer('ipp-1', 'Oficina', 'ipp://a/ipp'),
    printer('ipp-2', 'Taller', 'ipp://b/ipp'),
  ];
  printResult = () => json({ id: 'print-1', kind: 'print', status: 'queued', progress: 0 }, 202);
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('PrintToPrinter', () => {
  it('sends the job to the chosen IPP printer with copies and sheets', async () => {
    renderIt();
    const send = await screen.findByTestId('send-to-printer');
    // the printer of the job is preselected; profiles without an IPP address are not offered
    expect(screen.getByRole('textbox', { name: 'Impresora' })).toHaveValue('Taller');
    fireEvent.change(screen.getByLabelText(/Copias/), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText(/^Hojas/), { target: { value: 'A1, A2' } });
    fireEvent.click(send);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/jobs/job-1/print'))).toBe(true));
    expect(calls.find((c) => c.url.endsWith('/print'))?.body).toEqual({
      printer_id: 'ipp-2',
      copies: 3,
      sheets: ['A1', 'A2'],
    });
    expect(await screen.findByTestId('print-done')).toHaveTextContent('n.º 41');
  });

  it('prints every page when no sheets are typed', async () => {
    renderIt();
    fireEvent.click(await screen.findByTestId('send-to-printer'));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/print'))).toBe(true));
    expect(calls.find((c) => c.url.endsWith('/print'))?.body).toEqual({
      printer_id: 'ipp-2',
      copies: 1,
    });
  });

  it('blocks unknown or unprinted sheets', async () => {
    renderIt();
    const send = await screen.findByTestId('send-to-printer');
    fireEvent.change(screen.getByLabelText(/^Hojas/), { target: { value: 'A1, Z9, B1' } });
    expect(await screen.findByText(/Hojas desconocidas: Z9/)).toBeInTheDocument();
    expect(send).toBeDisabled();
  });

  it('shows the API error when the request is refused', async () => {
    printResult = () =>
      json(
        { type: 'x', title: 't', status: 422, detail: 'd', code: 'INVALID_SETTINGS', params: {} },
        422,
      );
    renderIt();
    fireEvent.click(await screen.findByTestId('send-to-printer'));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('is hidden when IPP is disabled', async () => {
    ippEnabled = false;
    renderIt();
    await waitFor(() => expect(printersFetched()).toBe(1));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/info'))).toBe(true));
    expect(screen.queryByTestId('print-to-printer')).toBeNull();
  });

  it('is hidden when no profile has an IPP address', async () => {
    printersList = [printer('plain', 'Sin IPP', null)];
    renderIt();
    await waitFor(() => expect(printersFetched()).toBe(1));
    expect(screen.queryByTestId('print-to-printer')).toBeNull();
  });

  it('is hidden when the job has no PDF', async () => {
    renderIt({ ...job, outputs: [{ name: 'a.zip', kind: 'zip', size_bytes: 1, pages: null }] });
    await waitFor(() => expect(printersFetched()).toBe(1));
    expect(screen.queryByTestId('print-to-printer')).toBeNull();
  });
});
