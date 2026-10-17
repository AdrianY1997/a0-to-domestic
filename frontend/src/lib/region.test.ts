import { describe, expect, it } from 'vitest';
import { regionDefaults } from './region';

describe('regionDefaults (PAP-05)', () => {
  it.each([
    ['en-US', 'LETTER', 'in'],
    ['en-CA', 'LETTER', 'in'],
    ['fr-CA', 'LETTER', 'in'],
    ['es-MX', 'LETTER', 'mm'],
    ['es-CO', 'LETTER', 'mm'],
    ['es-VE', 'LETTER', 'mm'],
    ['es-CL', 'LETTER', 'mm'],
    ['es-PE', 'LETTER', 'mm'],
    ['es-ES', 'A4', 'mm'],
    ['de-DE', 'A4', 'mm'],
    ['es', 'A4', 'mm'],
    ['', 'A4', 'mm'],
    ['not a tag!!', 'A4', 'mm'],
  ])('%s -> %s / %s', (tag, paper, unit) => {
    expect(regionDefaults(tag)).toEqual({ paper, unit });
  });
});
