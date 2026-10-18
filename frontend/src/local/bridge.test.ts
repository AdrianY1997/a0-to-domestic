import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/error';
import { WorkerBridge, statusForCode } from './bridge';
import type { WorkerEvent, WorkerLike } from './protocol';

class FakeWorker implements WorkerLike {
  onmessage: WorkerLike['onmessage'] = null;
  onerror: WorkerLike['onerror'] = null;
  sent: { message: Record<string, unknown>; transfer?: Transferable[] }[] = [];
  terminated = false;
  postMessage(message: unknown, transfer?: Transferable[]) {
    this.sent.push({ message: message as Record<string, unknown>, transfer });
  }
  terminate() {
    this.terminated = true;
  }
  emit(event: WorkerEvent) {
    this.onmessage?.({ data: event } as MessageEvent<WorkerEvent>);
  }
}

function setup() {
  const workers: FakeWorker[] = [];
  const hooks = { onBoot: vi.fn(), onReady: vi.fn(), onFailed: vi.fn() };
  const bridge = new WorkerBridge(() => {
    const w = new FakeWorker();
    workers.push(w);
    return w;
  }, hooks);
  return { bridge, workers, hooks, worker: () => workers[workers.length - 1] };
}

const ready = { dataDir: '/data', functions: ['ingest'], resources: [] };

describe('WorkerBridge', () => {
  it('reports boot progress and resolves whenReady', async () => {
    const { bridge, worker, hooks } = setup();
    worker().emit({ type: 'boot', stage: 'packages', pct: 40 });
    worker().emit({ type: 'ready', ...ready });
    await expect(bridge.whenReady()).resolves.toEqual(ready);
    expect(hooks.onBoot).toHaveBeenCalledWith('packages', 40);
    expect(hooks.onReady).toHaveBeenCalledWith(ready);
  });

  it('correlates results by request id and parses JSON strings', async () => {
    const { bridge, worker } = setup();
    const a = bridge.call<{ n: number }>('plan', ['x']);
    const b = bridge.call<{ n: number }>('plan', ['y']);
    const [ma, mb] = worker().sent.map((s) => s.message);
    expect(ma).toMatchObject({ type: 'call', fn: 'plan', args: ['x'], progress: false });
    worker().emit({ id: mb.id as number, type: 'result', value: '{"n":2}' });
    worker().emit({ id: ma.id as number, type: 'result', value: '{"n":1}' });
    await expect(a).resolves.toEqual({ n: 1 });
    await expect(b).resolves.toEqual({ n: 2 });
  });

  it('forwards progress events to the caller', async () => {
    const { bridge, worker } = setup();
    const onProgress = vi.fn();
    const p = bridge.call('render', [], {}, { onProgress });
    const id = worker().sent[0].message.id as number;
    expect(worker().sent[0].message.progress).toBe(true);
    worker().emit({
      id,
      type: 'progress',
      stage: 'render',
      pct: 30,
      message_key: 'render.sheet',
      params: { k: 1 },
    });
    worker().emit({ id, type: 'result', value: '{}' });
    await p;
    expect(onProgress).toHaveBeenCalledWith({
      stage: 'render',
      pct: 30,
      message_key: 'render.sheet',
      params: { k: 1 },
    });
  });

  it('maps worker errors to ApiError with the REST status', async () => {
    const { bridge, worker } = setup();
    const p = bridge.call('plan');
    worker().emit({
      id: worker().sent[0].message.id as number,
      type: 'error',
      error: { code: 'OVERLAP_TOO_LARGE', message: 'too much', params: { max: 50 } },
    });
    const err = await p.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: 'OVERLAP_TOO_LARGE', status: 422, params: { max: 50 } });
    expect(statusForCode('NOT_FOUND')).toBe(404);
    expect(statusForCode('PASSWORD_REQUIRED')).toBe(409);
    expect(statusForCode(null)).toBe(500);
  });

  it('sends binary call arguments as transferred buffers behind placeholders', () => {
    const { bridge, worker } = setup();
    const zip = new Uint8Array([80, 75]).buffer;
    void bridge.call('import_document', ['doc-1', zip]);
    const sent = worker().sent[0];
    expect(sent.message).toMatchObject({
      type: 'call',
      fn: 'import_document',
      args: ['doc-1', { __buffer__: 0 }],
      buffers: [zip],
    });
    expect(sent.transfer).toEqual([zip]);
  });

  it('keeps the status and field errors of the engine problem+json', async () => {
    const { bridge, worker } = setup();
    const p = bridge.call('plan');
    worker().emit({
      id: worker().sent[0].message.id as number,
      type: 'error',
      error: {
        code: 'INVALID_SETTINGS',
        message: 'Request validation failed',
        params: {},
        errors: [{ field: 'settings.tiling.overlap_mm', message: 'too big' }],
        status: 422,
      },
    });
    const err = (await p.catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(422);
    expect(err.errors).toEqual([{ field: 'settings.tiling.overlap_mm', message: 'too big' }]);
  });

  it('transfers file buffers instead of copying them', () => {
    const { bridge, worker } = setup();
    const data = new Uint8Array([1, 2, 3]).buffer;
    void bridge.writeFile('/tmp/a.pdf', data);
    expect(worker().sent[0].message).toMatchObject({ type: 'fs.write', path: '/tmp/a.pdf' });
    expect(worker().sent[0].transfer).toEqual([data]);
  });

  it('fails every pending call and whenReady when the boot fails', async () => {
    const { bridge, worker, hooks } = setup();
    const pending = bridge.call('info');
    worker().emit({ type: 'boot-error', message: 'wasm unsupported' });
    await expect(bridge.whenReady()).rejects.toMatchObject({ code: 'ENGINE_UNAVAILABLE' });
    await expect(pending).rejects.toMatchObject({ code: 'ENGINE_UNAVAILABLE' });
    expect(hooks.onFailed).toHaveBeenCalledWith('wasm unsupported');
  });

  it('restart cancels pending work, boots a fresh worker and bumps the generation', async () => {
    const { bridge, workers, worker } = setup();
    const pending = bridge.call('render');
    expect(bridge.generation).toBe(0);
    bridge.restart();
    await expect(pending).rejects.toMatchObject({ code: 'JOB_CANCELLED' });
    expect(workers[0].terminated).toBe(true);
    expect(workers).toHaveLength(2);
    expect(bridge.generation).toBe(1);
    // Events from the old worker are ignored; the new one boots normally.
    workers[0].emit({ type: 'ready', ...ready });
    worker().emit({ type: 'ready', ...ready });
    await expect(bridge.whenReady()).resolves.toEqual(ready);
  });
});
