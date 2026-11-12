import httpService, { request } from '@/utils/request';

export interface DiffSegment { text: string; changed: boolean }
export interface DiffImage { rid: string; digest: string }
export interface DiffCell { text: string; span: number }
export interface DiffRow {
  kind: 'context' | 'add' | 'delete';
  text: string;
  block_id: string;
  path: string;
  block_kind: 'paragraph' | 'table';
  old_line: number | null;
  new_line: number | null;
  segments?: DiffSegment[];
  images?: DiffImage[];
  table?: DiffCell[][] | null;
}
export interface DiffPair { old: DiffRow | null; new: DiffRow | null }
export interface DiffHunk { id: string; rows: DiffRow[]; pairs?: DiffPair[] }
export interface ComparisonSession {
  id: string;
  old_name: string;
  new_name: string;
  iteration_title: string;
  confirmed_hunks: string[];
  confirmed_at: string | null;
  result: {
    schema_version?: number; old_hash: string; new_hash: string;
    hunks: DiffHunk[]; added: number; deleted: number;
    old_blocks: number; new_blocks: number; warnings: string[];
    issues?: { side: 'old' | 'new'; block_id: string; path: string; kind: string; message: string }[];
  };
}

export async function compareFiles(project: string, oldFile: File, newFile: File): Promise<ComparisonSession> {
  const form = new FormData();
  form.append('project', project);
  form.append('old_file', oldFile);
  form.append('new_file', newFile);
  const response = await request<ComparisonSession>({ url: '/requirements/comparisons/', method: 'POST', data: form });
  if (!response.success || !response.data) throw new Error(comparisonError(response.error || response.message || '文档对比失败'));
  return response.data;
}

export async function loadComparison(id: string): Promise<ComparisonSession> {
  const response = await request<ComparisonSession>({ url: `/requirements/comparisons/${id}/`, method: 'GET' });
  if (!response.success || !response.data) throw new Error(comparisonError(response.error || response.message || '读取对比失败'));
  return response.data;
}

export async function extractComparison(id: string, title: string, hunkIds: string[]): Promise<Blob> {
  try {
    const response = await httpService.request<Blob>({
      url: `/requirements/comparisons/${id}/confirm-extract/`, method: 'POST',
      data: { title, hunk_ids: hunkIds }, responseType: 'blob',
      // Read validation errors as JSON blobs before the global interceptor discards them.
      validateStatus: (status: number) => status === 200 || status === 400 || status === 404
    });
    if (!(response.data instanceof Blob)) throw new Error('提取失败，请检查所选内容后重试。');
    if (response.status !== 200 || response.data.type.includes('json')) {
      const detail: unknown = JSON.parse(await response.data.text());
      throw new Error(comparisonError(detail));
    }
    return response.data;
  } catch (error: unknown) {
    if (error instanceof Error) throw error;
    if (error && typeof error === 'object' && 'error' in error && typeof error.error === 'string') {
      throw new Error(comparisonError(error.error));
    }
    throw new Error('提取失败，请检查访问权限或稍后重试。');
  }
}

/** Unwrap platform error envelopes without exposing transport JSON to users. */
export function comparisonError(value: unknown): string {
  if (typeof value === 'string') {
    try { return comparisonError(JSON.parse(value)); } catch { return value; }
  }
  if (Array.isArray(value)) return value.map(comparisonError).filter(Boolean).join('；');
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    for (const key of ['errors', 'detail', 'error', 'message']) {
      if (obj[key]) return comparisonError(obj[key]);
    }
    return Object.values(obj).map(comparisonError).filter(Boolean).join('；');
  }
  return '操作失败，请重试。';
}

export async function comparisonImage(id: string, side: 'old' | 'new', rid: string): Promise<Blob> {
  const response = await httpService.request<Blob>({
    url: `/requirements/comparisons/${id}/image/`, method: 'GET',
    params: { side, rid }, responseType: 'blob', validateStatus: (s: number) => s === 200 || s === 400
  });
  if (response.status !== 200 || !(response.data instanceof Blob) || !response.data.type.startsWith('image/')) {
    throw new Error('此图片暂无法预览，原图会保留在导出文档中。');
  }
  return response.data;
}
