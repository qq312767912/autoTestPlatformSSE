import { request } from '@/utils/request';
import { useProjectStore } from '@/store/projectStore';
import type { ApiInterface } from '../types/interface';
import type { ApiModule } from '../types/module';

const base = (projectId: number) => `/projects/${projectId}/api-interfaces`;

export const interfaceService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiInterface[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiInterface>({ url: `${base(projectId)}/${id}/`, method: 'GET' }),

  create: (projectId: number, data: Partial<ApiInterface>) =>
    request<ApiInterface>({ url: `${base(projectId)}/`, method: 'POST', data }),

  update: (projectId: number, id: number, data: Partial<ApiInterface>) =>
    request<ApiInterface>({ url: `${base(projectId)}/${id}/`, method: 'PUT', data }),

  delete: (projectId: number, id: number) =>
    request<void>({ url: `${base(projectId)}/${id}/`, method: 'DELETE' }),

  run: (projectId: number, id: number, data?: { environment_id?: number }) =>
    request<any>({ url: `${base(projectId)}/${id}/run/`, method: 'POST', data }),

  quickDebug: (projectId: number, data: Record<string, any>) =>
    request<any>({ url: `${base(projectId)}/quick_debug/`, method: 'POST', data }),
};

// ---------------------------------------------------------------------------
// Compatibility exports
// ---------------------------------------------------------------------------

function _pid(params?: Record<string, any>): number {
  if (params?.project_id) {
    const pid = Number(params.project_id);
    delete params.project_id;
    return pid;
  }
  return useProjectStore().currentProjectId ?? 0;
}

// Type aliases re-exported for component imports
export type { ApiInterface, ApiModule };
export type PaginatedData<T> = { results: T[]; count: number };
export type KeyValuePair = { key: string; value: string; enabled?: boolean; description?: string; [k: string]: any };
export type ApiValidator = { comparator: string; check: string; expect: any; [k: string]: any };
export type DebugInterfaceRequest = Record<string, any>;
export type QuickDebugInterfaceRequest = Record<string, any>;

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

export async function getInterfaces(params: Record<string, any> = {}) {
  const pid = _pid(params);
  return _wrapList(await interfaceService.list(pid, params));
}

export async function createInterface(data: any) {
  const pid = data.project ? Number(data.project) : (useProjectStore().currentProjectId ?? 0);
  delete data.project;
  return _wrapOne(await interfaceService.create(pid, data));
}

export async function updateInterface(id: number, data: any) {
  const pid = data.project ? Number(data.project) : (useProjectStore().currentProjectId ?? 0);
  delete data.project;
  return _wrapOne(await interfaceService.update(pid, id, data));
}

export async function debugInterface(id: number, data?: any) {
  const pid = useProjectStore().currentProjectId ?? 0;
  return _wrapOne(await interfaceService.run(pid, id, data));
}

export async function quickDebugInterface(data?: any) {
  const pid = useProjectStore().currentProjectId ?? 0;
  return _wrapOne(await interfaceService.quickDebug(pid, data));
}

export async function getInterfaceById(id: number) {
  const pid = useProjectStore().currentProjectId ?? 0;
  return _wrapOne(await interfaceService.get(pid, id));
}

export async function deleteInterface(id: number) {
  const pid = useProjectStore().currentProjectId ?? 0;
  return _wrapOne(await interfaceService.delete(pid, id));
}
