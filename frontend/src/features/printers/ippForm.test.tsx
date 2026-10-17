import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Providers } from '../../app/Providers';
import i18n from '../../i18n';
import PrinterFormPage from './PrinterFormPage';

// The app shares one QueryClient; tests need an empty cache each time.
const freshClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

const m = (n: number) => ({ top: n, right: n, bottom: n, left: n });
const papers = ['A4', 'LETTER'].map((id) => ({
  id,
  name: id,
  width_mm: 210,
  height_mm: 297,
  usage: '',
  width_in: null,
  height_in: null,
  pwg_name: null,
}));
let ippEnabled = true;
let capsResponse: () => Promise<Response>;
let calls: { url: string; method: string; body: unknown }[] = [];

const fetchMock = vi.fn((url: string, init?: RequestInit) => {
  const method = init?.method ?? 'GET';
  const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push({ url, method, body });
  if (url.endsWith('/info')) return json({ ipp_enabled: ippEnabled, version: 'x' });
  if (url.endsWith('/papers')) return json(papers);
  if (url.endsWith('/printers') && method === 'GET') return json([]);
  if (url.endsWith('/printers') && method === 'POST') return json({ id: 'user-new', ...body }, 201);
  if (url.endsWith('/printers/ipp-capabilities')) return capsResponse();
  return json({}, 404);
});

function renderForm() {
  return render(
    <Providers>
      <QueryClientProvider client={freshClient()}>
        <MemoryRouter initialEntries={['/printers/new']}>
          <Routes>
            <Route path="printers/new" element={<PrinterFormPage />} />
            <Route path="printers" element={<div>list</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </Providers>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage('es');
  window.localStorage.clear();
  calls = [];
  ippEnabled = true;
  capsResponse = () =>
    json({
      uri: 'ipp://10.0.0.5:631/ipp/print',
      name: 'Brother HL',
      papers: [
        {
          paper_id: 'A4',
          width_mm: 210,
          height_mm: 297,
          media: 'iso_a4_210x297mm',
          margins_mm: m(4.2),
        },
      ],
      default_margins_mm: m(4.2),
      color: false,
      document_formats: ['application/pdf'],
      accepts_pdf: true,
      accepts_pwg_raster: false,
      page_ranges: true,
    });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('printer form: IPP capabilities (PAP-07)', () => {
  it('reads the capabilities and prefills the profile, then saves the URI', async () => {
    renderForm();
    const uri = await screen.findByLabelText(/Dirección IPP/);
    const read = screen.getByRole('button', { name: 'Leer capacidades' });
    expect(read).toBeDisabled();
    fireEvent.change(uri, { target: { value: 'ipp://10.0.0.5:631/ipp/print' } });
    fireEvent.click(read);
    expect(await screen.findByTestId('ipp-caps-result')).toHaveTextContent('1 tamaño');
    expect(calls.find((c) => c.url.endsWith('/ipp-capabilities'))?.body).toEqual({
      uri: 'ipp://10.0.0.5:631/ipp/print',
    });
    expect(screen.getByLabelText(/Nombre/)).toHaveValue('Brother HL');
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));
    const isCreate = (c: { url: string; method: string }) =>
      c.method === 'POST' && c.url.endsWith('/printers');
    await waitFor(() => expect(calls.some(isCreate)).toBe(true));
    expect(calls.find(isCreate)!.body).toMatchObject({
      name: 'Brother HL',
      color: false,
      supported_papers: ['A4'],
      default_margins: m(4.2),
      paper_margins: { A4: m(4.2) },
      ipp_uri: 'ipp://10.0.0.5:631/ipp/print',
    });
  });

  it('hides the IPP fields when direct printing is disabled', async () => {
    ippEnabled = false;
    renderForm();
    await screen.findByLabelText(/Nombre/);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/info'))).toBe(true));
    expect(screen.queryByLabelText(/Dirección IPP/)).toBeNull();
  });

  it('shows the error when the printer cannot be reached', async () => {
    capsResponse = () =>
      json(
        {
          type: 'x',
          title: 't',
          status: 502,
          detail: 'down',
          code: 'PRINTER_UNREACHABLE',
          params: {},
        },
        502,
      );
    renderForm();
    const uri = await screen.findByLabelText(/Dirección IPP/);
    fireEvent.change(uri, { target: { value: 'ipp://10.0.0.9/ipp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Leer capacidades' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByTestId('ipp-caps-result')).toBeNull();
  });
});
