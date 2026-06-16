import type { UserBrief } from './common';

export interface ApiTestCaseTag {
  id: number;
  name: string;
  color: string;
  project: number;
  created_by: UserBrief | null;
  created_at: string;
}

export interface ApiTestCaseGroup {
  id: number;
  name: string;
  parent: number | null;
  project: number;
  created_by: UserBrief | null;
  created_at: string;
  children?: ApiTestCaseGroup[];
}

export type TestCasePriority = 'P0' | 'P1' | 'P2' | 'P3';

export interface ApiTestCase {
  id: number;
  name: string;
  description: string;
  priority: TestCasePriority;
  config: Record<string, any>;
  project: number;
  group: number | null;
  tags: number[] | ApiTestCaseTag[];
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  steps?: ApiTestCaseStep[];
}

export interface ApiTestCaseStep {
  id: number;
  name: string;
  order: number;
  interface_data: Record<string, any>;
  testcase: number;
  origin_interface: number | null;
  sync_fields: string[];
  last_sync_time: string | null;
  [key: string]: any;
}

export type TestReportStatus = 'success' | 'failure' | 'error';

export interface ApiTestReport {
  id: number;
  name: string;
  status: TestReportStatus;
  success_count: number;
  fail_count: number;
  error_count: number;
  duration: number;
  start_time: string;
  summary: Record<string, any>;
  testcase: number;
  environment: number | null;
  executed_by: UserBrief | null;
  details?: ApiTestReportDetail[];
  [key: string]: any;
}

export interface ApiTestReportDetail {
  id: number;
  report: number;
  step: number | null;
  success: boolean;
  elapsed: number;
  request: Record<string, any>;
  response: Record<string, any>;
  validators: any[];
  extracted_variables: Record<string, any>;
  attachment: string;
  [key: string]: any;
}

// Compatibility type aliases used by components
export type Tag = ApiTestCaseTag;
export type TagStatistics = { tag_id: number; tag_name: string; count: number; [key: string]: any };
export type Group = ApiTestCaseGroup;
export type Variable = { key: string; value: any; description?: string; [key: string]: any };
export type TestCaseBasicInfo = { id: number; name: string; description: string; priority: TestCasePriority; group: number | null; tags: number[]; [key: string]: any };
export type TestCaseConfigData = { export: any[]; verify: boolean; base_url: string; variables: any[]; parameters: any[]; [key: string]: any };
export type TestCaseHistoryReport = {
  id: number;
  name: string;
  status: TestReportStatus;
  success_rate: number;
  success_count: number;
  fail_count: number;
  error_count: number;
  duration: number;
  start_time: string;
  environment_name: string;
  summary: Record<string, any>;
  [key: string]: any;
};
