import { request } from '@/utils/request';
import service from '@/utils/request';

// ============ 类型定义 ============

export interface KeywordItem {
  keyword: string;
  replacement: string;
}

export interface AnonymizedDocumentItem {
  id: number;
  original_filename: string;
  file_type: string;
  file_size: number;
  status: 'pending' | 'anonymizing' | 'anonymized' | 'failed';
  status_label: string;
  anonymized_at: string | null;
  anonymization_report: AnonymizationReport | null;
  error_message: string;
  enabled_preset_types: string[];
  custom_keywords: KeywordItem[];
  uploaded_by: number;
  uploaded_by_name: string;
  created_at: string;
  updated_at: string;
}

export interface AnonymizationReport {
  total_count: number;
  details: Array<{
    entity_type: string;
    entity_label: string;
    count: number;
    examples: string[];
  }>;
  all_entities: Array<{
    entity_type: string;
    entity_label: string;
    start: number;
    end: number;
    score: number;
    text_snippet: string;
  }>;
}

export interface PresetRuleItem {
  id: number;
  name: string;
  entity_type: string;
  entity_label: string;
  regex: string;
  score: number;
  is_active: boolean;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface AnonymizationTemplate {
  id: number;
  name: string;
  description: string;
  enabled_preset_types: string[];
  custom_keywords: KeywordItem[];
  created_by: number | null;
  created_by_name: string;
  created_at: string;
  updated_at: string;
}

// ============ 文档管理 API ============

export async function uploadDocuments(files: File[]) {
  const formData = new FormData();
  files.forEach(f => formData.append('files', f));
  return request({
    url: '/operation-logs/anonymization-docs/',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

export async function getAnonymizedDocuments(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  search?: string;
}) {
  return request({
    url: '/operation-logs/anonymization-docs/',
    method: 'get',
    params: {
      page: params?.page || 1,
      page_size: params?.page_size || 20,
      status: params?.status || undefined,
      search: params?.search || undefined,
    },
  });
}

export async function getAnonymizedDocument(id: number) {
  return request({
    url: `/operation-logs/anonymization-docs/${id}/`,
    method: 'get',
  });
}

export async function updateDocumentRules(id: number, data: {
  enabled_preset_types?: string[];
  custom_keywords?: KeywordItem[];
}) {
  return request({
    url: `/operation-logs/anonymization-docs/${id}/`,
    method: 'patch',
    data,
  });
}

export async function executeAnonymization(id: number) {
  return request({
    url: `/operation-logs/anonymization-docs/${id}/execute/`,
    method: 'post',
  });
}

export async function downloadAnonymizedFile(id: number): Promise<{
  blob: Blob;
  fileName: string;
}> {
  const response = await service.get(`/operation-logs/anonymization-docs/${id}/download/`, {
    responseType: 'blob',
  });
  const disposition = response.headers['content-disposition'] || '';
  // 优先解析 RFC 5987 filename*=UTF-8'' 格式，回退普通 filename=""
  const fileNameStarMatch = disposition.match(/filename\*=(?:UTF-8''|utf-8'')([^;\s]+)/);
  const fileNameMatch = disposition.match(/filename="?([^";\s]+)"?/);
  const fileName = fileNameStarMatch
    ? decodeURIComponent(fileNameStarMatch[1])
    : (fileNameMatch ? decodeURIComponent(fileNameMatch[1]) : `anonymized_${id}`);
  return { blob: response.data as Blob, fileName };
}

export async function deleteAnonymizedDocument(id: number) {
  return request({
    url: `/operation-logs/anonymization-docs/${id}/`,
    method: 'delete',
  });
}

export async function resetAnonymization(id: number) {
  return request({
    url: `/operation-logs/anonymization-docs/${id}/reset/`,
    method: 'post',
  });
}

// ============ 预设规则 API ============

export async function getPresetRules(params?: { search?: string }) {
  return request({
    url: '/operation-logs/anonymization-rules/',
    method: 'get',
    params: { search: params?.search || undefined, page_size: 100 },
  });
}

export async function seedDefaultRules() {
  return request({
    url: '/operation-logs/anonymization-rules/seed-defaults/',
    method: 'post',
  });
}

// ============ 规则 CRUD API ============

export async function createRule(data: {
  name: string;
  entity_type: string;
  entity_label: string;
  regex: string;
  score?: number;
  is_active?: boolean;
  description?: string;
}) {
  return request({
    url: '/operation-logs/anonymization-rules/',
    method: 'post',
    data,
  });
}

export async function updateRule(id: number, data: Partial<{
  name: string;
  entity_type: string;
  entity_label: string;
  regex: string;
  score: number;
  is_active: boolean;
  description: string;
}>) {
  return request({
    url: `/operation-logs/anonymization-rules/${id}/`,
    method: 'patch',
    data,
  });
}

export async function deleteRule(id: number) {
  return request({
    url: `/operation-logs/anonymization-rules/${id}/`,
    method: 'delete',
  });
}

// ============ 模板 CRUD API ============

export async function getTemplates(params?: { search?: string }) {
  return request({
    url: '/operation-logs/anonymization-templates/',
    method: 'get',
    params: { search: params?.search || undefined, page_size: 100 },
  });
}

export async function createTemplate(data: {
  name: string;
  description?: string;
  enabled_preset_types?: string[];
  custom_keywords?: KeywordItem[];
}) {
  return request({
    url: '/operation-logs/anonymization-templates/',
    method: 'post',
    data,
  });
}

export async function updateTemplate(id: number, data: Partial<{
  name: string;
  description: string;
  enabled_preset_types: string[];
  custom_keywords: KeywordItem[];
}>) {
  return request({
    url: `/operation-logs/anonymization-templates/${id}/`,
    method: 'patch',
    data,
  });
}

export async function deleteTemplate(id: number) {
  return request({
    url: `/operation-logs/anonymization-templates/${id}/`,
    method: 'delete',
  });
}

export async function applyTemplate(templateId: number, documentId: number) {
  return request({
    url: `/operation-logs/anonymization-templates/${templateId}/apply/`,
    method: 'post',
    data: { document_id: documentId },
  });
}

export async function applyMultipleTemplates(templateIds: number[], documentId: number) {
  return request({
    url: '/operation-logs/anonymization-templates/apply-multiple/',
    method: 'post',
    data: { template_ids: templateIds, document_id: documentId },
  });
}

export async function seedDefaultTemplates() {
  return request({
    url: '/operation-logs/anonymization-templates/seed-defaults/',
    method: 'post',
  });
}
