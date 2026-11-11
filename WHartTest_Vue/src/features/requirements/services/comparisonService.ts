import httpService, { request } from '@/utils/request';

export interface DiffRow {
  kind: 'context' | 'add' | 'delete';
  text: string;
  block_id: string;
  path: string;
  block_kind: 'paragraph' | 'table';
  old_line: number | null;
  new_line: number | null;
}
export interface DiffHunk { id: string; rows: DiffRow[] }
export interface ComparisonSession {
  id: string;
  old_name: string;
  new_name: string;
  iteration_title: string;
  confirmed_hunks: string[];
  confirmed_at: string | null;
  result: {
    old_hash: string; new_hash: string;
    hunks: DiffHunk[]; added: number; deleted: number;
    old_blocks: number; new_blocks: number; warnings: string[];
  };
}

export async function compareFiles(project: string, oldFile: File, newFile: File): Promise<ComparisonSession> {
  const form = new FormData();
  form.append('project', project);
  form.append('old_file', oldFile);
  form.append('new_file', newFile);
  const response = await request<ComparisonSession>({ url: '/requirements/comparisons/', method: 'POST', data: form });
  if (!response.success || !response.data) throw new Error(response.error || response.message || '文档对比失败');
  return response.data;
}

export async function loadComparison(id: string): Promise<ComparisonSession> {
  const response = await request<ComparisonSession>({ url: `/requirements/comparisons/${id}/`, method: 'GET' });
  if (!response.success || !response.data) throw new Error(response.error || response.message || '读取对比失败');
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
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return response.data;
  } catch (error: unknown) {
    if (error instanceof Error) throw error;
    if (error && typeof error === 'object' && 'error' in error && typeof error.error === 'string') {
      throw new Error(error.error);
    }
    throw new Error('提取失败，请检查访问权限或稍后重试。');
  }
}
