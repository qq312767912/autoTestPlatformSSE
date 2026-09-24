import { request } from '@/utils/request';
import type {
  MappingPayload,
  TestHostDiagnosis,
  TestHostDiff,
  TestHostMapping,
  TestHostNode,
  TestHostOverview,
  TestHostVersion,
} from './types';

const base = '/test-host-config';

async function dataOrThrow<T>(promise: Promise<{ success: boolean; data?: T; error?: string }>): Promise<T> {
  const response = await promise;
  if (!response.success || response.data === undefined) {
    throw new Error(response.error || '请求失败');
  }
  return response.data;
}

export function listMappings(params: { search?: string; enabled?: string }) {
  return dataOrThrow(request<TestHostMapping[]>({ url: `${base}/mappings/`, method: 'GET', params }));
}

export function createMapping(payload: MappingPayload) {
  return dataOrThrow(request<TestHostMapping>({ url: `${base}/mappings/`, method: 'POST', data: payload }));
}

export function updateMapping(id: number, payload: MappingPayload) {
  return dataOrThrow(request<TestHostMapping>({ url: `${base}/mappings/${id}/`, method: 'PATCH', data: payload }));
}

export async function deleteMapping(id: number): Promise<void> {
  const response = await request<null>({ url: `${base}/mappings/${id}/`, method: 'DELETE' });
  if (!response.success) throw new Error(response.error || '删除失败');
}

export function getOverview() {
  return dataOrThrow(request<TestHostOverview>({ url: `${base}/mappings/overview/`, method: 'GET' }));
}

export function getDiff() {
  return dataOrThrow(request<TestHostDiff>({ url: `${base}/mappings/diff/`, method: 'GET' }));
}

export function publishMappings(expectedDraftRevision: number) {
  return dataOrThrow(request<TestHostVersion>({
    url: `${base}/mappings/publish/`, method: 'POST', data: { expected_draft_revision: expectedDraftRevision },
  }));
}

export function listVersions() {
  return dataOrThrow(request<TestHostVersion[]>({ url: `${base}/mappings/versions/`, method: 'GET' }));
}

export function rollbackVersion(id: number) {
  return dataOrThrow(request<TestHostVersion>({ url: `${base}/versions/${id}/rollback/`, method: 'POST' }));
}

export function listNodes() {
  return dataOrThrow(request<TestHostNode[]>({ url: `${base}/mappings/nodes/`, method: 'GET' }));
}

export function startDiagnosis(mappingId: number) {
  return dataOrThrow(request<TestHostDiagnosis>({ url: `${base}/mappings/${mappingId}/diagnose/`, method: 'POST' }));
}

export function getDiagnosis(id: number) {
  return dataOrThrow(request<TestHostDiagnosis>({ url: `${base}/mappings/diagnoses/${id}/`, method: 'GET' }));
}
