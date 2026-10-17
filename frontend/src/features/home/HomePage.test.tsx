import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Providers } from '../../app/Providers';
import i18n from '../../i18n';
import { HomePage } from './HomePage';

class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress?: (e: ProgressEvent) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  status = 0;
  responseText = '';
  body: FormData | null = null;
  constructor() {
    FakeXhr.last = this;
  }
  open() {}
  setRequestHeader() {}
  send(body: FormData) {
    this.body = body;
  }
  abort() {}
}

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

const info = {
  version: '0.1.0',
  limits: {
    max_upload_mb: 100,
    max_pages: 100,
    max_megapixels: 1000,
    max_page_mm: 10000,
    max_sheets: 500,
    preview_max_px: 4096,
    workers: 2,
    retention_hours: 72,
  },
  converters: [
    { id: 'pdf', available: true, formats: ['pdf'] },
    { id: 'raster', available: true, formats: ['png', 'jpeg'] },
    { id: 'cad', available: false, formats: ['dwg'] },
  ],
  auth_mode: 'none',
  default_locale: 'es',
  ipp_enabled: false,
};

const presets = [
  {
    id: 'builtin-real-a4',
    name: 'Tamaño real en A4',
    builtin: true,
    description: '',
    settings: {},
  },
  { id: 'builtin-fit-a3', name: 'Reducido a una A3', builtin: true, description: '', settings: {} },
];

function Where() {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname + loc.search}</p>;
}

function renderHome() {
  return render(
    <Providers>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route index element={<HomePage />} />
          <Route path="documents/:id" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </Providers>,
  );
}

const fetchMock = vi.fn((url: string) => {
  if (url.includes('/info')) return json(info);
  if (url.includes('/presets')) return json(presets);
  if (url.includes('/documents')) return json([]);
  return json({}, 404);
});

beforeEach(async () => {
  await i18n.changeLanguage('es');
  window.localStorage.clear();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
});
afterEach(() => vi.unstubAllGlobals());

describe('home upload flow', () => {
  it('lists the available formats from /info and the built-in presets', async () => {
    renderHome();
    expect(await screen.findByText('PDF')).toBeInTheDocument();
    expect(screen.getByText('PNG')).toBeInTheDocument();
    expect(screen.queryByText('DWG')).not.toBeInTheDocument(); // converter not available
    expect(await screen.findByRole('button', { name: 'Reducido a una A3' })).toBeInTheDocument();
  });

  it('uploads a dropped file with progress and opens the editor with the chosen preset', async () => {
    renderHome();
    fireEvent.click(await screen.findByRole('button', { name: 'Tamaño real en A4' }));
    expect(screen.getByRole('button', { name: 'Tamaño real en A4' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['%PDF-1.7'], 'plano.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByText('plano.pdf');
    await waitFor(() => expect(FakeXhr.last?.body?.get('file')).toBeInstanceOf(File));
    act(() =>
      FakeXhr.last.upload.onprogress?.({
        lengthComputable: true,
        loaded: 1,
        total: 2,
      } as ProgressEvent),
    );
    expect(await screen.findByRole('progressbar', { name: /plano\.pdf/ })).toBeInTheDocument();

    act(() => {
      FakeXhr.last.status = 202;
      FakeXhr.last.responseText = JSON.stringify({ id: 'doc-9', status: 'processing' });
      FakeXhr.last.onload?.();
    });
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent(
        '/documents/doc-9?preset=builtin-real-a4',
      ),
    );
  });

  it('shows a translated server error for a rejected file and stays on the page', async () => {
    renderHome();
    await screen.findByText('PDF');
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['x'], 'raro.xyz')] } });
    await screen.findByText('raro.xyz');
    await waitFor(() => expect(FakeXhr.last?.body).not.toBeNull());
    act(() => {
      FakeXhr.last.status = 415;
      FakeXhr.last.responseText = JSON.stringify({ code: 'UNSUPPORTED_FORMAT' });
      FakeXhr.last.onload?.();
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Formato no soportado');
    expect(screen.queryByTestId('where')).not.toBeInTheDocument();
  });
});
