import { afterEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import es from '../i18n/es.json';
import {
  capabilitiesFor,
  extensionOf,
  getCapabilities,
  isDockerOnlyFile,
  isLocalMode,
} from './capabilities';
import { errorMessage } from './errors';

afterEach(() => vi.unstubAllEnvs());

describe('capabilities', () => {
  it('the Docker version has every server feature', () => {
    expect(capabilitiesFor('server')).toEqual({
      mode: 'server',
      ipp: true,
      hotFolder: true,
      auth: true,
      metrics: true,
      clientOnly: false,
    });
  });

  it('the web version hides IPP, the watched folder, auth and metrics', () => {
    expect(capabilitiesFor('local')).toEqual({
      mode: 'local',
      ipp: false,
      hotFolder: false,
      auth: false,
      metrics: false,
      clientOnly: true,
    });
  });

  it('follows VITE_MODE at run time', () => {
    expect(isLocalMode()).toBe(false);
    expect(getCapabilities().ipp).toBe(true);
    vi.stubEnv('VITE_MODE', 'local');
    expect(isLocalMode()).toBe(true);
    expect(getCapabilities().ipp).toBe(false);
  });
});

describe('Docker-only formats', () => {
  it('flags Office, ODF, PostScript, DWG and friends by extension, case-insensitively', () => {
    for (const name of ['a.docx', 'b.XLSX', 'c.odt', 'd.eps', 'e.AI', 'f.dwg', 'g.cdr', 'h.djvu']) {
      expect(isDockerOnlyFile(name)).toBe(true);
    }
  });

  it('lets the engine decide for everything else (type comes from the content)', () => {
    for (const name of [
      'a.pdf',
      'b.png',
      'c.JPG',
      'd.tif',
      'e.svg',
      'f.xps',
      'g.dxf',
      'h.heic',
      'noext',
    ]) {
      expect(isDockerOnlyFile(name)).toBe(false);
    }
    expect(extensionOf('archive.tar.gz')).toBe('gz');
    expect(extensionOf('noext')).toBe('');
  });

  it('shows "Disponible en la versión Docker" for unsupported formats only in the web version', async () => {
    await i18n.changeLanguage('es');
    const t = i18n.t.bind(i18n);
    expect(errorMessage(t, 'UNSUPPORTED_FORMAT')).toBe(es.errors.UNSUPPORTED_FORMAT);
    vi.stubEnv('VITE_MODE', 'local');
    expect(errorMessage(t, 'UNSUPPORTED_FORMAT')).toBe(es.errors.FORMAT_DOCKER_ONLY);
    expect(errorMessage(t, 'FORMAT_DOCKER_ONLY')).toBe(es.errors.FORMAT_DOCKER_ONLY);
    expect(es.local.dockerOnly.title).toBe('Disponible en la versión Docker');
  });
});
