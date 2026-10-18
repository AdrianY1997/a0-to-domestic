import type { SseErrorEvent, StreamHandlers } from '../api/transport';
import type { ProgressEvent } from '../api/types';

type Handlers = Pick<StreamHandlers, 'progress' | 'done' | 'error'>;

/**
 * In-browser stand-in for the server's SSE streams: `progress*` then `done` or `error`, per
 * document or job. The last progress event is replayed to late subscribers.
 */
export class LocalEvents {
  private subs = new Map<string, Set<Handlers>>();
  private last = new Map<string, ProgressEvent>();

  subscribe(key: string, handlers: Handlers): () => void {
    let set = this.subs.get(key);
    if (!set) this.subs.set(key, (set = new Set()));
    set.add(handlers);
    const replay = this.last.get(key);
    if (replay) queueMicrotask(() => set.has(handlers) && handlers.progress(replay));
    return () => {
      set.delete(handlers);
      if (set.size === 0) this.subs.delete(key);
    };
  }

  progress(key: string, event: ProgressEvent) {
    this.last.set(key, event);
    for (const h of [...(this.subs.get(key) ?? [])]) h.progress(event);
  }

  done(key: string, data: unknown) {
    this.last.delete(key);
    for (const h of [...(this.subs.get(key) ?? [])]) h.done(data);
  }

  error(key: string, event: SseErrorEvent) {
    this.last.delete(key);
    for (const h of [...(this.subs.get(key) ?? [])]) h.error(event);
  }
}
