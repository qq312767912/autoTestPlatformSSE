import { request } from '@/utils/request';
import { useProjectStore } from '@/store/projectStore';
import type { ApiTestReport, ApiTestReportDetail } from '../types/testcase';

const base = (projectId: number) => `/projects/${projectId}/api-test-reports`;

export const testReportService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiTestReport[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiTestReport & { details: ApiTestReportDetail[] }>({
      url: `${base(projectId)}/${id}/`, method: 'GET',
    }),
};

// ---------------------------------------------------------------------------
// Compatibility exports
// ---------------------------------------------------------------------------

function _pid(params?: Record<string, any>): number {
  if (params?.project) {
    const pid = Number(params.project);
    delete params.project;
    return pid;
  }
  return useProjectStore().currentProjectId ?? 0;
}

function _wrapList(res: any): any {
  if (!res.success) {
    const err: any = new Error(res.error || res.message || '操作失败');
    err.errors = res.errors;
    throw err;
  }
  return { data: { results: res.data ?? [], count: res.total ?? 0 }, status: 'success', message: '' };
}

function _wrapOne(res: any): any {
  if (!res.success) {
    const err: any = new Error(res.error || res.message || '操作失败');
    err.errors = res.errors;
    throw err;
  }
  return { data: res.data ?? null, status: 'success', message: '' };
}

// Type alias
export type TestReportDetail = ApiTestReport & { details: ApiTestReportDetail[] } & Record<string, any>;

export async function getTestReports(params: Record<string, any> = {}) {
  const pid = _pid(params);
  return _wrapList(await testReportService.list(pid, params));
}

export async function getTestReportDetail(id: number) {
  const pid = useProjectStore().currentProjectId ?? 0;
  return _wrapOne(await testReportService.get(pid, id));
}
