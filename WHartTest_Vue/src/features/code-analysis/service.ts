import http, { request } from '@/utils/request';
import type { AnalysisTask, CodeRepository, GitLabConnection, MergeRequest } from './types';

const base = '/code-analysis';
const list = <T>(value: any): T[] => Array.isArray(value) ? value : (value?.results || []);

export async function getConnections() { const r = await request<any>({ url: `${base}/connections/`, method: 'GET' }); if (!r.success) throw new Error(r.error); return list<GitLabConnection>(r.data); }
export async function createConnection(data: Partial<GitLabConnection>) { const r = await request<GitLabConnection>({ url: `${base}/connections/`, method: 'POST', data }); if (!r.success) throw new Error(r.error); return r.data!; }
export async function getRepositories(project: number) { const r = await request<any>({ url: `${base}/repositories/`, method: 'GET', params: { project } }); if (!r.success) throw new Error(r.error); return list<CodeRepository>(r.data); }
export async function createRepository(data: any) { const r = await request<CodeRepository>({ url: `${base}/repositories/`, method: 'POST', data }); if (!r.success) throw new Error(r.error); return r.data!; }
export async function saveCredential(data: { project:number; connection:number; token:string }) { const r = await request<any>({ url: `${base}/credentials/`, method: 'POST', data }); if (!r.success) throw new Error(r.error); return r.data; }
export async function getMergeRequests(repository: number) { const r = await request<any>({ url: `${base}/repositories/${repository}/merge-requests/`, method: 'GET' }); if (!r.success) throw new Error(r.error); return list<MergeRequest>(r.data); }
export async function getTasks(project: number) { const r = await request<any>({ url: `${base}/tasks/`, method: 'GET', params: { project } }); if (!r.success) throw new Error(r.error); return list<AnalysisTask>(r.data); }
export async function createTask(data: any) { const r = await request<AnalysisTask>({ url: `${base}/tasks/`, method: 'POST', data }); if (!r.success) throw new Error(r.error); return r.data!; }
export async function runTask(id: string) { const r = await request<AnalysisTask>({ url: `${base}/tasks/${id}/run/`, method: 'POST' }); if (!r.success) throw new Error(r.error); return r.data!; }
export async function cancelTask(id: string) { const r = await request<AnalysisTask>({ url: `${base}/tasks/${id}/cancel/`, method: 'POST' }); if (!r.success) throw new Error(r.error); return r.data!; }
export async function deleteTask(id: string) { const r = await request({ url: `${base}/tasks/${id}/`, method: 'DELETE' }); if (!r.success) throw new Error(r.error); }
export async function downloadReport(id: string, type: 'change'|'test') {
  const url = `${base}/tasks/${id}/download-${type}-report/`;
  const response = await http.get(url, { responseType: 'blob' });
  const source = response.data instanceof Blob ? response.data : new Blob([response.data]);
  const blob = new Blob([source], { type: 'text/plain;charset=utf-8' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `代码审查-${id}-${type === 'change' ? '审查报告' : '测试报告'}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
