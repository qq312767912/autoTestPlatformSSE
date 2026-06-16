import { request } from '@/utils/request';
import type { ApiTestCaseGroup } from '../types/testcase';

const base = (projectId: number) => `/projects/${projectId}/api-testcase-groups`;

export const testcaseGroupService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiTestCaseGroup[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiTestCaseGroup>({ url: `${base(projectId)}/${id}/`, method: 'GET' }),

  create: (projectId: number, data: Partial<ApiTestCaseGroup>) =>
    request<ApiTestCaseGroup>({ url: `${base(projectId)}/`, method: 'POST', data }),

  update: (projectId: number, id: number, data: Partial<ApiTestCaseGroup>) =>
    request<ApiTestCaseGroup>({ url: `${base(projectId)}/${id}/`, method: 'PUT', data }),

  delete: (projectId: number, id: number) =>
    request<void>({ url: `${base(projectId)}/${id}/`, method: 'DELETE' }),

  tree: (projectId: number) =>
    request<ApiTestCaseGroup[]>({ url: `${base(projectId)}/tree/`, method: 'GET' }),

  testcases: (projectId: number, id: number) =>
    request<any[]>({ url: `${base(projectId)}/${id}/testcases/`, method: 'GET' }),
};
