import http, { request } from '@/utils/request';

export interface ReviewChunkProgress {
  completed: number;
  uncovered: number;
  total: number;
}

export interface TestCaseReview {
  id: number;
  source_name: string;
  business_context: string;
  review_mode: 'general' | 'specified';
  selected_skill?: number;
  skill_name: string;
  custom_rules: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  current_step: string;
  progress: number;
  report_url?: string;
  summary: {
    total_rows?: number;
    high?: number;
    medium?: number;
    low?: number;
    uncovered_chunks?: number;
    uncovered_rows?: number;
    total_chunks?: number;
    progress_chunks?: ReviewChunkProgress;
  };
  error_message: string;
  creator_name?: string;
  created_at: string;
  completed_at?: string;
}

const unwrap = (data: any): TestCaseReview[] => data?.results || data?.data?.results || data?.data || [];

export async function listReviews(projectId: number) {
  const response = await http.get(`/projects/${projectId}/testcase-reviews/`);
  return unwrap(response.data);
}

export async function createReview(projectId: number, file: File, options: {
  businessContext: string;
  reviewMode: 'general' | 'specified';
  selectedSkill?: number;
  customRules: string;
  requirementDocumentIds: string[];
  knowledgeBaseIds: string[];
  knowledgeDocumentIds: string[];
}) {
  const form = new FormData();
  form.append('source_file', file);
  form.append('business_context', options.businessContext);
  form.append('review_mode', options.reviewMode);
  form.append('custom_rules', options.customRules);
  if (options.selectedSkill) form.append('selected_skill', String(options.selectedSkill));
  form.append('requirement_document_ids', JSON.stringify(options.requirementDocumentIds));
  form.append('knowledge_base_ids', JSON.stringify(options.knowledgeBaseIds));
  form.append('knowledge_document_ids', JSON.stringify(options.knowledgeDocumentIds));
  const response = await http.post(`/projects/${projectId}/testcase-reviews/`, form);
  return response.data as TestCaseReview;
}

export async function diagnoseReviewFile(projectId: number, file: File) {
  const form = new FormData();
  form.append('source_file', file);
  const response = await http.post(`/projects/${projectId}/testcase-reviews/diagnose-file/`, form);
  return response.data as { usable: boolean; detail: string; case_rows?: number };
}

export async function retryReview(projectId: number, id: number) {
  const response = await http.post(`/projects/${projectId}/testcase-reviews/${id}/retry/`);
  return response.data as TestCaseReview;
}

export async function cancelReview(projectId: number, id: number) {
  const response = await http.post(`/projects/${projectId}/testcase-reviews/${id}/cancel/`);
  return response.data as TestCaseReview;
}

export async function deleteReview(projectId: number, id: number) {
  await http.delete(`/projects/${projectId}/testcase-reviews/${id}/`);
}

// ---------------------------------------------------------------------------
// 用例审查专用 LLM 配置（平台级单例，仅平台管理员可读写）
// ---------------------------------------------------------------------------

export interface TestCaseReviewLlmConfig {
  id?: number;
  config_name: string;
  name: string;
  api_url: string;
  api_key?: string;
  has_api_key?: boolean;
  request_timeout: number;
  max_retries: number;
  is_active: boolean;
}

export interface PlatformLlmConfigOption {
  id: number;
  config_name: string;
  name: string;
  api_url: string;
  request_timeout: number;
  max_retries: number;
  is_active: boolean;
  has_api_key: boolean;
}

const llmConfigBase = '/testcases/review-llm-config';
const asList = <T>(value: any): T[] => (Array.isArray(value) ? value : value?.results || []);

export async function getReviewLlmConfig() {
  const r = await request<any>({ url: `${llmConfigBase}/`, method: 'GET' });
  if (!r.success) throw new Error(r.error);
  // 单例：列表接口只会返回 0 或 1 条。
  return asList<TestCaseReviewLlmConfig>(r.data)[0] || null;
}

export async function saveReviewLlmConfig(data: TestCaseReviewLlmConfig) {
  const r = await request<TestCaseReviewLlmConfig>({
    url: data.id ? `${llmConfigBase}/${data.id}/` : `${llmConfigBase}/`,
    method: data.id ? 'PATCH' : 'POST',
    data,
  });
  if (!r.success) throw new Error(r.error);
  return r.data!;
}

export async function testReviewLlmConfig(id: number) {
  const r = await request<any>({ url: `${llmConfigBase}/${id}/test-connection/`, method: 'POST' });
  if (!r.success) throw new Error(r.error);
  return r.data;
}

export async function getPlatformLlmConfigs() {
  const r = await request<any>({ url: `${llmConfigBase}/platform-configs/`, method: 'GET' });
  if (!r.success) throw new Error(r.error);
  return asList<PlatformLlmConfigOption>(r.data);
}

export async function copyPlatformLlmConfig(sourceConfigId: number) {
  const r = await request<TestCaseReviewLlmConfig>({
    url: `${llmConfigBase}/copy-from-platform/`,
    method: 'POST',
    data: { source_config_id: sourceConfigId },
  });
  if (!r.success) throw new Error(r.error);
  return r.data!;
}
