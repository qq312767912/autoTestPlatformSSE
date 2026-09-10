import http from '@/utils/request';

export interface TestCaseReview {
  id: number;
  source_name: string;
  business_context: string;
  review_mode: 'general' | 'specified';
  selected_skill?: number;
  skill_name: string;
  custom_rules: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  current_step: string;
  progress: number;
  report_url?: string;
  summary: Record<string, number>;
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
}) {
  const form = new FormData();
  form.append('source_file', file);
  form.append('business_context', options.businessContext);
  form.append('review_mode', options.reviewMode);
  form.append('custom_rules', options.customRules);
  if (options.selectedSkill) form.append('selected_skill', String(options.selectedSkill));
  const response = await http.post(`/projects/${projectId}/testcase-reviews/`, form);
  return response.data as TestCaseReview;
}

export async function retryReview(projectId: number, id: number) {
  const response = await http.post(`/projects/${projectId}/testcase-reviews/${id}/retry/`);
  return response.data as TestCaseReview;
}

export async function deleteReview(projectId: number, id: number) {
  await http.delete(`/projects/${projectId}/testcase-reviews/${id}/`);
}
