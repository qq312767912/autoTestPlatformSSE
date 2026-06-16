import { request } from '@/utils/request';
import type { ApiInterfaceResult } from '../types/interface';

const base = (projectId: number) => `/projects/${projectId}/api-interface-results`;

export const interfaceResultService = {
  list: (projectId: number, params?: Record<string, any>) =>
    request<ApiInterfaceResult[]>({ url: `${base(projectId)}/`, method: 'GET', params }),

  get: (projectId: number, id: number) =>
    request<ApiInterfaceResult>({ url: `${base(projectId)}/${id}/`, method: 'GET' }),
};
