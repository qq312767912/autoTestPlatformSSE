import { request } from '@/utils/request';
import type { ApiTestCaseTag } from '../types/testcase';

const base = (projectId: number) => `/projects/${projectId}/api-testcase-tags`;

export const testcaseTagService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiTestCaseTag[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiTestCaseTag>({ url: `${base(projectId)}/${id}/`, method: 'GET' }),

  create: (projectId: number, data: Partial<ApiTestCaseTag>) =>
    request<ApiTestCaseTag>({ url: `${base(projectId)}/`, method: 'POST', data }),

  update: (projectId: number, id: number, data: Partial<ApiTestCaseTag>) =>
    request<ApiTestCaseTag>({ url: `${base(projectId)}/${id}/`, method: 'PUT', data }),

  delete: (projectId: number, id: number) =>
    request<void>({ url: `${base(projectId)}/${id}/`, method: 'DELETE' }),

  statistics: (projectId: number) =>
    request<any[]>({ url: `${base(projectId)}/statistics/`, method: 'GET' }),
};
