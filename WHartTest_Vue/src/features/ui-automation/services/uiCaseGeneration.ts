import { moduleApi, envConfigApi, authStateApi } from '../api';
import { SkillService } from '@/features/skills/services/skillService';
import { SkillHubService, createSkillAgentTask } from '@/features/skills/services/skillHubService';
import { sendChatMessageStream } from '@/features/langgraph/services/chatService';

export interface UiGenerationFormData {
  functional_case_ids: number[];
  module_id: number | null;
  env_config_id: number | null;
  auth_state_id: number | null;
  skillVersionId: string;
  businessNotes: string;
}

export async function allRows(fetchPage: (page: number) => Promise<any>): Promise<any[]> {
  const rows: any[] = [];
  for (let page = 1; ; page++) {
    const response = await fetchPage(page);
    const data = response?.data?.data ?? response?.data;
    if (!data || (!Array.isArray(data) && !Array.isArray(data.results))) throw new Error('读取生成范围失败');
    const items = Array.isArray(data) ? data : data.results;
    rows.push(...items);
    if (Array.isArray(data) || (!data.next && rows.length >= (data.count ?? rows.length))) return rows;
    if (!items.length) throw new Error('生成范围分页不完整');
  }
}
export async function loadUiGenerationOptions(project: number) {
  const [modules, environments, skills] = await Promise.all([
    moduleApi.tree(project),
    allRows(page => envConfigApi.list({ project, page, page_size: 100 } as any)),
    SkillService.getSkills(project),
  ]);
  const tree: any = (modules.data as any)?.data ?? modules.data;
  if (!Array.isArray(tree)) throw new Error('读取模块失败');
  const flatten = (nodes: any[], prefix = ''): any[] => nodes.flatMap(n => [{ id: n.id, name: prefix + n.name }, ...flatten(n.children || [], prefix + n.name + ' / ')]);
  return { modules: flatten(tree), environments, skills: skills.items.filter(s => s.is_active) };
}
export async function loadUiGenerationVersions(project: number, skill: number) {
  return (await SkillHubService.listVersions(project, skill)).filter(v => !v.archived && !['quarantined', 'rejected'].includes(v.state) && v.download_available !== false);
}
export async function loadUiGenerationAuth(env: number) {
  return allRows(page => authStateApi.list({ env_config: env, is_active: true, page, page_size: 100 } as any));
}
export function buildUiMessage(notes: string) {
  return '<!-- wharttest:generation_mode=ui_case -->\n请读取服务端 get_ui_generation_context，按锁定 Skill 精确版本逐条生成 UI 自动化用例，真实试跑成功后使用 bind_generated_ui_case 绑定。业务补充：\n' + notes;
}
export async function startUiGeneration(project: number, form: UiGenerationFormData, onError: (message: string) => void) {
  const { skillVersionId, businessNotes, ...context } = form;
  const binding = await createSkillAgentTask(project, skillVersionId, 'ui_case', context);
  localStorage.setItem('langgraph_session_id', binding.session_id);
  void sendChatMessageStream({ project_id: String(project), session_id: binding.session_id, skill_version_id: skillVersionId, message: buildUiMessage(businessNotes), auth_state_id: form.auth_state_id }, sessionId => localStorage.setItem('langgraph_session_id', sessionId), undefined, onError);
  return binding;
}
