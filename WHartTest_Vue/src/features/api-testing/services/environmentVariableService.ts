import { request } from '@/utils/request';
import type { ApiEnvironmentVariable } from '../types/environment';

const base = (projectId: number) => `/projects/${projectId}/api-environment-variables`;

export const environmentVariableService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiEnvironmentVariable[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiEnvironmentVariable>({ url: `${base(projectId)}/${id}/`, method: 'GET' }),

  create: (projectId: number, data: Partial<ApiEnvironmentVariable>) =>
    request<ApiEnvironmentVariable>({ url: `${base(projectId)}/`, method: 'POST', data }),

  update: (projectId: number, id: number, data: Partial<ApiEnvironmentVariable>) =>
    request<ApiEnvironmentVariable>({ url: `${base(projectId)}/${id}/`, method: 'PUT', data }),

  delete: (projectId: number, id: number) =>
    request<void>({ url: `${base(projectId)}/${id}/`, method: 'DELETE' }),
};
