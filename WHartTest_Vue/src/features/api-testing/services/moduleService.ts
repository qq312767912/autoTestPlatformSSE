import { request } from '@/utils/request';
import { useProjectStore } from '@/store/projectStore';
import type { ApiModule } from '../types/module';

export interface ApiModuleDeleteResult {
  deleted_interface_ids: number[];
  deleted_interface_count: number;
}

const base = (projectId: number) => `/projects/${projectId}/api-modules`;

export const moduleService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiModule[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiModule>({ url: `${base(projectId)}/${id}/`, method: 'GET' }),

  create: (projectId: number, data: Partial<ApiModule>) =>
    request<ApiModule>({ url: `${base(projectId)}/`, method: 'POST', data }),

  update: (projectId: number, id: number, data: Partial<ApiModule>) =>
    request<ApiModule>({ url: `${base(projectId)}/${id}/`, method: 'PUT', data }),

  delete: (projectId: number, id: number) =>
    request<ApiModuleDeleteResult>({ url: `${base(projectId)}/${id}/`, method: 'DELETE' }),

  tree: (projectId: number) =>
    request<ApiModule[]>({ url: `${base(projectId)}/tree/`, method: 'GET' }),
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

export async function getModules(params: Record<string, any> = {}) {
  const pid = _pid(params);
  return _wrapList(await moduleService.list(pid, params));
}

export async function getModuleTree(projectId?: number) {
  const pid = projectId ?? (useProjectStore().currentProjectId ?? 0);
  return _wrapList(await moduleService.tree(pid));
}

export async function getModuleById(id: number) {
  return _wrapOne(await moduleService.get(_pid(), id));
}

export async function createModule(data: any) {
  const pid = data.project ? Number(data.project) : _pid();
  delete data.project;
  return _wrapOne(await moduleService.create(pid, data));
}

export async function updateModule(id: number, data: any) {
  const pid = data.project ? Number(data.project) : _pid();
  delete data.project;
  return _wrapOne(await moduleService.update(pid, id, data));
}

export async function deleteModule(id: number) {
  return _wrapOne(await moduleService.delete(_pid(), id));
}

export type { ApiModule };
