import { act, configure, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setTransport, type Transport } from '../api/transport';
import { ErrorAlert } from '../components/ErrorAlert';
import { ApiError } from '../api/error';
import i18n from '../i18n';
import en from '../i18n/en.json';
import { useBootStore } from '../store/boot';
import { Providers } from './Providers';
import { AppRoutes } from './routes';

// The first lazy route load is slow on a busy CI machine.
configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 30_000 });

const info = {
  version: 'web',
  limits: {
    max_upload_mb: 200,
    max_pages: 100,
    max_megapixels: 100,
    max_page_mm: 10000,
    max_sheets: 500,
    preview_max_px: 2400,
    workers: 1,
    retention_hours: 24,
  },
  converters: [{ id: 'pdf', available: true, formats: ['pdf'] }],
  auth_mode: 'none',
  default_locale: 'en',
  ipp_enabled: false,
};

const answers: Record<string, unknown> = {
  '/info': info,
  '/auth/session': { auth_mode: 'none', authenticated: true, csrf_token: null },
  '/presets': [],
  '/documents': [],
  '/printers': [],
  '/settings': { retention_hours: 24, retention_locked: false, default_locale: 'en' },
};

function localTransport(): Transport {
  return {
    kind: 'local',
    request: vi.fn((path: string) => {
      const body = answers[path] ?? [];
      return Promise.resolve(new Response(JSON.stringify(body)));
    }),
    upload: vi.fn(),
    subscribe: vi.fn(() => () => {}),
    resolveAsset: vi.fn(),
  };
}

function renderAt(path: string) {
  return render(
    <Providers>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </Providers>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
  vi.stubEnv('VITE_MODE', 'local');
  setTransport(localTransport());
  useBootStore.setState({ phase: 'idle', stage: 'runtime', pct: 0, error: null });
});
afterEach(() => {
  setTransport(null);
  vi.unstubAllEnvs();
});

describe('web (local) version of the UI', () => {
  it('tells the user that everything stays in the browser and lists Docker-only formats', async () => {
    renderAt('/');
    expect(await screen.findByTestId('local-banner', {}, { timeout: 20_000 })).toHaveTextContent(
      en.local.banner,
    );
    const docker = screen.getByTestId('docker-only-formats');
    expect(docker).toHaveTextContent(en.local.dockerOnly.title);
    expect(docker).toHaveTextContent('DOCX');
    expect(docker).toHaveTextContent('DWG');
    const link = docker.querySelector('a');
    expect(link?.getAttribute('href')).toContain('github.com/AdrianY1997/a0-to-domestic');
  }, 30_000);

  it('shows the engine boot progress and hides it when ready', async () => {
    useBootStore.setState({ phase: 'booting', stage: 'packages', pct: 42 });
    renderAt('/help');
    const status = await screen.findByTestId('boot-status');
    expect(status).toHaveTextContent(en.boot.title);
    expect(status).toHaveTextContent(en.boot.stage.packages);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');

    act(() => useBootStore.setState({ phase: 'ready', pct: 100 }));
    await waitFor(() => expect(screen.queryByTestId('boot-status')).not.toBeInTheDocument());
  });

  it('shows a reload button when the engine fails to start', async () => {
    useBootStore.setState({ phase: 'failed', error: 'no wasm' });
    renderAt('/help');
    const failed = await screen.findByTestId('boot-failed');
    expect(failed).toHaveTextContent(en.boot.failed.title);
    expect(failed).toHaveTextContent('no wasm');
  });

  it('has no login page and no logout button', async () => {
    renderAt('/login');
    expect(await screen.findByText(en.pages.notFound.title)).toBeInTheDocument();
    expect(screen.queryByText(en.header.logout)).not.toBeInTheDocument();
  });

  it('links unsupported-format errors to the Docker version', () => {
    render(
      <Providers>
        <ErrorAlert error={new ApiError(415, { code: 'FORMAT_DOCKER_ONLY' })} />
      </Providers>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(en.errors.FORMAT_DOCKER_ONLY);
    expect(screen.getByRole('link', { name: en.local.dockerLink })).toBeInTheDocument();
  });

  it('explains a full browser storage', () => {
    render(
      <Providers>
        <ErrorAlert error={new ApiError(507, { code: 'STORAGE_QUOTA_EXCEEDED' })} />
      </Providers>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(en.errors.STORAGE_QUOTA_EXCEEDED);
  });
});

describe('Docker version of the UI', () => {
  it('does not show the web-only banner or the Docker-only list', async () => {
    vi.stubEnv('VITE_MODE', '');
    setTransport({ ...localTransport(), kind: 'server' });
    renderAt('/');
    await screen.findByText(en.home.title);
    expect(screen.queryByTestId('local-banner')).not.toBeInTheDocument();
    expect(screen.queryByTestId('docker-only-formats')).not.toBeInTheDocument();
  });
});
