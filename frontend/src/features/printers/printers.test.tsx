import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Providers } from '../../app/Providers';
import i18n from '../../i18n';
import CalibrationPage from './CalibrationPage';
import PrinterFormPage from './PrinterFormPage';
import PrintersPage from './PrintersPage';

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

const margins = { top: 3, right: 3, bottom: 5, left: 3 };
const builtin = {
  id: 'builtin-inkjet',
  name: 'Inyección típica',
  kind: 'inkjet',
  builtin: true,
  color: true,
  borderless: false,
  scale_kx: 1,
  scale_ky: 1,
  default_margins: margins,
  paper_margins: {},
};
const mine = {
  id: 'user-abc',
  name: 'Mi Epson',
  kind: 'inkjet',
  builtin: false,
  color: true,
  borderless: false,
  scale_kx: 1,
  scale_ky: 1,
  default_margins: margins,
  paper_margins: {},
  defaults: { borderless: true, paper_ids: ['A4'], mode: { kind: 'x' } },
};
const papers = [
  {
    id: 'A4',
    name: 'A4',
    width_mm: 210,
    height_mm: 297,
    usage: '',
    width_in: null,
    height_in: null,
    pwg_name: null,
  },
  {
    id: 'LETTER',
    name: 'Carta',
    width_mm: 215.9,
    height_mm: 279.4,
    usage: '',
    width_in: 8.5,
    height_in: 11,
    pwg_name: null,
  },
];

let calls: { url: string; method: string; body: unknown }[] = [];
let calibrationResponse: () => Promise<Response>;

const fetchMock = vi.fn((url: string, init?: RequestInit) => {
  const method = init?.method ?? 'GET';
  const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push({ url, method, body });
  if (url.endsWith('/papers')) return json(papers);
  if (url.endsWith('/printers') && method === 'GET') return json([builtin, mine]);
  if (url.endsWith('/printers') && method === 'POST')
    return json({ ...mine, ...body, id: 'user-new' }, 201);
  if (url.endsWith('/printers/user-abc') && method === 'PUT') return json({ ...mine, ...body });
  if (url.endsWith('/calibration') && method === 'POST') return calibrationResponse();
  return json({}, 404);
});

function renderAt(path: string) {
  return render(
    <Providers>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="printers" element={<PrintersPage />} />
          <Route path="printers/new" element={<PrinterFormPage />} />
          <Route path="printers/:id" element={<PrinterFormPage />} />
          <Route path="printers/:id/calibrate" element={<CalibrationPage />} />
        </Routes>
      </MemoryRouter>
    </Providers>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage('es');
  window.localStorage.clear();
  calls = [];
  calibrationResponse = () =>
    json({
      ...mine,
      scale_kx: 0.995,
      paper_margins: { A4: { top: 3.5, right: 3.5, bottom: 3.5, left: 3.5 } },
    });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('printers list', () => {
  it('shows built-in profiles (duplicate only) and user profiles (edit, calibrate)', async () => {
    renderAt('/printers');
    const b = await screen.findByTestId('printer-builtin-inkjet');
    expect(b).toHaveTextContent('Integrado');
    expect(b.querySelector('a')).toBeNull();
    expect(screen.getByRole('button', { name: 'Duplicar' })).toBeInTheDocument();
    const u = screen.getByTestId('printer-user-abc');
    expect(u).toHaveTextContent('Mi Epson');
    expect(u.querySelector('a[href="/printers/user-abc/calibrate"]')).not.toBeNull();
  });
});

describe('printer form', () => {
  it('creates a profile with the typed name, kind and margins', async () => {
    renderAt('/printers/new');
    fireEvent.change(await screen.findByLabelText(/Nombre/), {
      target: { value: 'Brother láser' },
    });
    fireEvent.change(screen.getAllByLabelText(/Superior/)[0], { target: { value: '4.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    const post = calls.find((c) => c.method === 'POST')!;
    expect(post.url).toContain('/printers');
    expect(post.body).toMatchObject({
      name: 'Brother láser',
      kind: 'generic',
      color: true,
      borderless: false,
      default_margins: { top: 4.5, right: 5, bottom: 5, left: 5 },
      paper_margins: {},
    });
  });

  it('does not submit without a name', async () => {
    renderAt('/printers/new');
    expect(await screen.findByRole('button', { name: 'Guardar' })).toBeDisabled();
  });

  it('keeps source defaults when editing a profile', async () => {
    renderAt('/printers/user-abc');
    await screen.findByLabelText(/Nombre/);
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')!.body).toMatchObject({ defaults: mine.defaults });
  });

  it('duplicating a built-in profile prefills the form and creates a user copy', async () => {
    renderAt('/printers/new?from=builtin-inkjet');
    const name = (await screen.findByLabelText(/Nombre/)) as HTMLInputElement;
    expect(name.value).toBe('Inyección típica (copia)');
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')!.body).toMatchObject({
      default_margins: { top: 3, right: 3, bottom: 5, left: 3 },
    });
  });
});

describe('calibration wizard', () => {
  async function toMeasureStep() {
    renderAt('/printers/user-abc/calibrate');
    const link = await screen.findByRole('link', { name: 'Descargar PDF de calibración' });
    expect(link).toHaveAttribute(
      'href',
      expect.stringContaining('/printers/user-abc/calibration.pdf?paper=A4'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByTestId('k-result');
  }

  async function measure(x: string, y: string) {
    fireEvent.change(screen.getByLabelText(/Longitud horizontal medida/), { target: { value: x } });
    fireEvent.change(screen.getByLabelText(/Longitud vertical medida/), { target: { value: y } });
    fireEvent.change(screen.getAllByLabelText(/Superior/)[0], { target: { value: '3' } });
  }

  it('starts empty and keeps Save disabled until lengths and margins are entered', async () => {
    await toMeasureStep();
    const save = screen.getByRole('button', { name: 'Guardar calibración' });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Longitud horizontal medida/), {
      target: { value: '179.1' },
    });
    expect(save).toBeDisabled();
    await measure('179.1', '260');
    expect(save).toBeEnabled();
  });

  it('warns and blocks saving when k is outside 0.97-1.03', async () => {
    await toMeasureStep();
    expect(screen.queryByTestId('k-warning')).toBeNull();
    await measure('150', '260');
    expect(await screen.findByTestId('k-warning')).toHaveTextContent('Ajustar a página');
    expect(screen.getByRole('button', { name: 'Guardar calibración' })).toBeDisabled();
  });

  it('saves measured lengths, margins and shows the stored result', async () => {
    await toMeasureStep();
    await measure('179.1', '260');
    expect(screen.getByTestId('k-result')).toHaveTextContent('0.9950 × 1.0000');
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }));
    await screen.findByRole('status');
    const post = calls.find((c) => c.url.endsWith('/calibration'))!;
    expect(post.body).toMatchObject({
      paper: 'A4',
      measured_x_mm: 179.1,
      nominal_x_mm: 180,
      measured_y_mm: 260,
      nominal_y_mm: 260,
      margins_mm: { top: 3 },
    });
    expect(screen.getByRole('status')).toHaveTextContent('Calibración guardada');
  });

  it('shows the server error when the API rejects the correction', async () => {
    calibrationResponse = () =>
      json(
        {
          code: 'CALIBRATION_OUT_OF_RANGE',
          status: 422,
          detail: 'x',
          params: { kx: 0.9, ky: 1, min: 0.97, max: 1.03 },
        },
        422,
      );
    await toMeasureStep();
    await measure('179.1', '260');
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Ajustar a página');
  });

  it('refuses to calibrate a built-in profile', async () => {
    renderAt('/printers/builtin-inkjet/calibrate');
    expect(await screen.findByText(/no se pueden calibrar/)).toBeInTheDocument();
  });
});
