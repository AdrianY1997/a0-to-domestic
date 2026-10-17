import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import en from '../i18n/en.json';
import es from '../i18n/es.json';
import { Providers } from './Providers';
import { AppRoutes } from './routes';

function renderAt(path: string) {
  return render(
    <Providers>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </Providers>,
  );
}

function keys(obj: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    typeof v === 'object' && v !== null
      ? keys(v as Record<string, unknown>, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  );
}

describe('app shell', () => {
  it('renders header navigation in Spanish', async () => {
    await i18n.changeLanguage('es');
    renderAt('/help');
    const nav = screen.getByRole('navigation', { name: es.nav.main });
    for (const label of [
      es.nav.home,
      es.nav.printers,
      es.nav.presets,
      es.nav.history,
      es.nav.help,
      es.nav.settings,
    ]) {
      expect(nav).toHaveTextContent(label);
    }
  });

  it('renders the printers route', async () => {
    await i18n.changeLanguage('es');
    renderAt('/printers/abc/calibrate');
    expect(await screen.findByText(es.printers.loading)).toBeInTheDocument();
  });

  it('en.json has the same keys as es.json', () => {
    expect(keys(en).sort()).toEqual(keys(es).sort());
  });
});

describe('token mode', () => {
  it('shows a full-page notice instead of the app', async () => {
    await i18n.changeLanguage('en');
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ auth_mode: 'token', authenticated: false }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    try {
      renderAt('/');
      expect(await screen.findByText(en.tokenMode.title)).toBeInTheDocument();
      expect(screen.queryByRole('navigation', { name: en.nav.main })).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
