import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import es from './es.json';

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return walk(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

function has(key: string): boolean {
  let cur: unknown = es;
  for (const part of key.split('.')) {
    if (typeof cur !== 'object' || cur === null) return false;
    const obj = cur as Record<string, unknown>;
    if (part in obj) {
      cur = obj[part];
    } else {
      return false;
    }
  }
  return true;
}

describe('i18n keys', () => {
  it('every literal t("...") key used in the source exists in es.json', () => {
    const missing: string[] = [];
    for (const file of walk(join(process.cwd(), 'src'))) {
      const src = readFileSync(file, 'utf-8');
      for (const m of src.matchAll(/\bt\(\s*'([A-Za-z0-9_.]+)'/g)) {
        const key = m[1];
        // Plural forms are stored as key_one / key_other.
        if (!has(key) && !has(`${key}_other`)) missing.push(`${file}: ${key}`);
      }
    }
    expect(missing).toEqual([]);
  }, 30_000);
});
