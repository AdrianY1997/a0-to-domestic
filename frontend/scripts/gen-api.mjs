// Generates src/api/schema.d.ts from the backend OpenAPI document.
// API_URL defaults to localhost; inside compose it is http://backend:8000.
import { spawnSync } from 'node:child_process';

const base = (process.env.API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const res = spawnSync(
  'npx',
  ['openapi-typescript', `${base}/api/openapi.json`, '-o', 'src/api/schema.d.ts'],
  { stdio: 'inherit', shell: process.platform === 'win32' },
);
process.exit(res.status ?? 1);
