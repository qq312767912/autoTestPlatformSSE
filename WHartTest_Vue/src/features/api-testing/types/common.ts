/** Common types shared across api-testing feature. */

/** Standard paginated list response from the backend. */
export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/** Unified response wrapper produced by request() utility. */
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  total?: number;
  error?: string;
  message?: string;
}

/** Lightweight user reference embedded in serialized models. */
export interface UserBrief {
  id: number;
  username: string;
}
