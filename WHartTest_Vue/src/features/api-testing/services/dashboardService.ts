import { request } from '@/utils/request';
import { useProjectStore } from '@/store/projectStore';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RecentTask {
  id: number;
  task_suite__name: string;
  status: string;
  created_at: string;
  success_rate: number;
  [k: string]: any;
}

export interface RecentReport {
  id: number;
  name: string;
  status: string;
  start_time: string;
  duration: number;
  success_count: number;
  fail_count: number;
  error_count: number;
  testcase__name: string;
  success_rate: number;
}

export interface DashboardSummary {
  total_testcases: number;
  total_interfaces: number;
  total_projects: number;
  total_tasks: number;
  success_rate: number;
  recent_tasks: RecentTask[];
  recent_reports: RecentReport[];
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

const base = (projectId: number) => `/projects/${projectId}/api-dashboard`;

export const dashboardService = {
  summary: (projectId: number) =>
    request<DashboardSummary>({ url: `${base(projectId)}/summary/`, method: 'GET' }),
};

// ---------------------------------------------------------------------------
// Compatibility exports
// ---------------------------------------------------------------------------

function _wrapOne(res: any): any {
  if (!res.success) {
    const err: any = new Error(res.error || res.message || '操作失败');
    err.errors = res.errors;
    throw err;
  }
  return { data: res.data ?? null, status: 'success', message: '' };
}

export async function getDashboardSummary() {
  const pid = useProjectStore().currentProjectId ?? 0;
  return _wrapOne(await dashboardService.summary(pid));
}
