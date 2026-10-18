export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  code?: string;
  params?: Record<string, unknown>;
  errors?: unknown[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly params: Record<string, unknown>;
  readonly detail?: string;
  readonly errors?: unknown[];

  constructor(status: number, problem: ProblemDetails = {}) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = problem.code ?? 'UNKNOWN';
    this.params = problem.params ?? {};
    this.detail = problem.detail;
    this.errors = problem.errors;
  }
}
