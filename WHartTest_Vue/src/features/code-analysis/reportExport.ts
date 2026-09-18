import type { AnalysisTask, Finding } from './types';

export type ReportType = 'change' | 'test';

interface ReportTestPoint {
  id?: number;
  title?: string;
  priority?: string;
  change_group?: string;
  objective?: string;
  expected_result?: string;
  source_finding_key?: string;
  risk_reference?: { title?: string; file?: string };
}

const priorityOrder: Record<string, number> = { high: 0, medium: 1, low: 2 };
const riskGroups = new Set(['风险排查', '风险点', '风险回归']);

function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function richText(value: unknown): string {
  return escapeHtml(value).replace(/\r?\n/g, '<br>');
}

function safeFilename(value: string): string {
  return value.replace(/[/\\:*?"<>|\r\n]+/g, '_').replace(/^[ ._]+|[ ._]+$/g, '') || '未命名项目';
}

// 报告标题前缀本身带下划线，保证「代码审查报告_」在产物里是完整字符串，
// 内网 05-verify.sh 用它做前端产物级断言。
const reportTitlePrefixes: Record<ReportType, string> = { change: '代码审查报告_', test: '测试分析报告_' };

// 报告标题与下载文件名共用同一口径：`<报告名>_<代码仓库名>`。
// 同一平台项目下可以有多个代码仓库，按仓库名命名才能区分不同仓库的报告；
// 标题与文件名同源，避免两处写法各自漂移。
function reportName(task: AnalysisTask, type: ReportType): string {
  const repository = task.repository_name || task.project_name || `项目${task.project}`;
  return safeFilename(`${reportTitlePrefixes[type]}${repository}`);
}

function formatTime(value?: string): string {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-';
}

function severityLabel(value?: string): string {
  return ({ high: '高风险', medium: '中风险', low: '低风险' } as Record<string, string>)[value || ''] || '未分级';
}

function priorityLabel(value?: string): string {
  return ({ high: '高优先级', medium: '中优先级', low: '低优先级' } as Record<string, string>)[value || ''] || '未分级';
}

function modeLabel(value: string): string {
  return ({ quick: '快速模式', standard: '标准模式', deep: '深度模式' } as Record<string, string>)[value] || value;
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function metric(label: string, value: unknown, tone = ''): string {
  return `<div class="metric ${tone}"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`;
}

function reportDocument(task: AnalysisTask, title: string, body: string): string {
  const projectName = task.project_name || `项目 ${task.project}`;
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <style>
    :root{--ink:#1d2939;--muted:#667085;--line:#e4e9f0;--paper:#fff;--canvas:#f5f7fa;--teal:#176b87;--aqua:#16827d;--danger:#d9485f;--warn:#b85d18;--soft:#eef7f6}
    *{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65}
    .page{width:min(1080px,calc(100% - 40px));margin:28px auto 56px}.hero{padding:30px 34px;border-radius:18px;background:linear-gradient(125deg,#102a43,#176b87 62%,#1b8f8a);color:#fff;box-shadow:0 14px 40px rgb(16 42 67 / 18%)}
    .eyebrow{color:#9fe1dd;font-size:11px;letter-spacing:.16em}.hero h1{margin:7px 0 5px;font-size:28px}.hero p{margin:0;color:#d8edf0}.meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:20px}.meta div{padding:9px 11px;border-radius:8px;background:rgb(255 255 255 / 9%)}.meta span,.meta b{display:block}.meta span{color:#bcd5d8;font-size:11px}.meta b{margin-top:2px;overflow-wrap:anywhere;font-size:12px}
    .metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.metric{padding:16px 18px;border:1px solid var(--line);border-radius:12px;background:#fff}.metric strong,.metric span{display:block}.metric strong{font-size:24px}.metric span{color:#718096;font-size:12px}.metric.danger strong{color:var(--danger)}.metric.positive strong{color:#198f65}
    .section{margin-top:16px;padding:22px;border:1px solid var(--line);border-radius:14px;background:var(--paper)}.section-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:14px}.section-head h2{margin:0;font-size:18px}.section-head p{margin:3px 0 0;color:#8792a2;font-size:12px}.count{color:var(--aqua);font-size:12px;font-weight:700}
    .focus{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}.chip{padding:5px 9px;border-radius:999px;background:var(--soft);color:#176d69;font-size:11px}.card{margin-top:12px;padding:15px;border:1px solid #e7ebf0;border-radius:10px;background:#fff;break-inside:avoid}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.card-title{display:flex;align-items:flex-start;gap:8px;min-width:0}.card-title strong{overflow-wrap:anywhere}.badge{flex:none;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:700}.badge.high{background:#fff0f1;color:#c9364e}.badge.medium{background:#fff5e8;color:#a9500f}.badge.low{background:#edf5ff;color:#2c67a6}.source{color:#8792a2;font-size:11px;white-space:nowrap}
    .path{margin:8px 0;color:#526273;font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;overflow-wrap:anywhere}.evidence{margin:10px 0;padding:12px;overflow:auto;border:1px solid #e5eaee;border-radius:8px;background:#f6f8fa;color:#26333f;font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;white-space:pre-wrap}.detail{display:grid;grid-template-columns:58px minmax(0,1fr);gap:6px;margin-top:9px;color:#526273}.detail b{color:#344054}.summary{padding:16px;border:1px solid #dce9ea;border-radius:10px;background:linear-gradient(135deg,#f7fbfc,#fff)}.summary h3{margin:0 0 5px;color:#1f3f4d}.summary p{margin:0;color:#5d6b78}.group-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:12px}.group{padding:12px;border:1px solid #dce9ea;border-radius:9px;background:#fff}.group b{display:block;color:#234858}.group p{margin:5px 0;color:#526273;font-size:12px}.group small{color:#778899}.file-list{display:grid;gap:6px}.file-list code{padding:7px 9px;border-radius:6px;background:#f4f7f8;color:#526273;overflow-wrap:anywhere}.empty{padding:28px;text-align:center;color:#98a2b3}.footer{margin-top:18px;color:#98a2b3;font-size:11px;text-align:right}
    @media(max-width:760px){.page{width:min(100% - 20px,1080px);margin-top:10px}.hero{padding:23px}.meta,.metrics,.group-grid{grid-template-columns:1fr 1fr}.card-head{display:block}.source{display:block;margin-top:6px;white-space:normal}}
    @media print{body{background:#fff}.page{width:100%;margin:0}.hero,.metric,.section{box-shadow:none}.section{page-break-inside:auto}.card{page-break-inside:avoid}}
  </style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <div class="eyebrow">QUALITY INTELLIGENCE</div>
      <h1>${escapeHtml(title)}</h1>
      <p>${escapeHtml(task.title || task.repository_name)}</p>
      <div class="meta">
        <div><span>平台项目</span><b>${escapeHtml(projectName)}</b></div>
        <div><span>代码仓库</span><b>${escapeHtml(task.repository_name)}</b></div>
        <div><span>分析范围</span><b>${escapeHtml(task.base_sha)} → ${escapeHtml(task.head_sha)}</b></div>
        <div><span>分析模式</span><b>${escapeHtml(modeLabel(task.mode))}</b></div>
      </div>
    </header>
    ${body}
    <div class="footer">完成时间：${escapeHtml(formatTime(task.completed_at))} · 报告由 WHartTest 导出</div>
  </main>
</body>
</html>`;
}

function buildChangeReport(task: AnalysisTask): string {
  const report = task.change_report || {};
  const summary = report.summary || {};
  const findings = [...(report.findings || [])].sort(
    (left, right) => (priorityOrder[left.severity] ?? 9) - (priorityOrder[right.severity] ?? 9),
  );
  const impactSummary = report.impact_summary || [];
  const cards = findings.map((item: Finding, index) => `
    <article class="card">
      <div class="card-head">
        <div class="card-title"><span class="badge ${escapeHtml(item.severity)}">${escapeHtml(severityLabel(item.severity))}</span><strong>${index + 1}. ${escapeHtml(item.change)}</strong></div>
        <span class="source">${escapeHtml(item.source)} · ${escapeHtml(Math.round(numberValue(item.confidence) * 100))}%</span>
      </div>
      <div class="path">${escapeHtml(item.file)}</div>
      <pre class="evidence"><code>${escapeHtml(item.evidence || '未提供风险凭据')}</code></pre>
      <div class="detail"><b>影响</b><span>${richText(item.impact || '需结合代码上下文确认')}</span></div>
      <div class="detail"><b>建议</b><span>${richText(item.recommendation || '覆盖正常流程、异常分支及调用链后再决定是否修复')}</span></div>
    </article>`).join('');
  const files = (report.files || []).map(file => `<code>${escapeHtml(file.path || '')}</code>`).join('');
  const body = `
    <section class="metrics">
      ${metric('变更文件', numberValue(summary.changed_files))}
      ${metric('新增行', `+${numberValue(summary.additions)}`, 'positive')}
      ${metric('删除行', `-${numberValue(summary.deletions)}`, 'danger')}
      ${metric('总变更行', numberValue(summary.changed_lines))}
      ${metric('高风险', numberValue(summary.high_risk_count), 'danger')}
      ${metric('风险总数', numberValue(summary.risk_count))}
      ${metric('机器覆盖率', `${numberValue(task.machine_coverage)}%`)}
      ${metric('AI 覆盖率', `${numberValue(task.ai_coverage)}%`)}
    </section>
    <section class="section">
      <div class="section-head"><div><h2>风险与影响</h2><p>确定性规则与 AI 风险提示的融合结果</p></div><span class="count">按风险级别排序 · ${findings.length} 项</span></div>
      ${impactSummary.length ? `<div class="focus">${impactSummary.map(item => `<span class="chip">${escapeHtml(item)}</span>`).join('')}</div>` : ''}
      ${cards || '<div class="empty">未发现确定性风险</div>'}
    </section>
    <section class="section"><div class="section-head"><div><h2>变更文件</h2><p>本次分析范围内的文件</p></div></div><div class="file-list">${files || '<div class="empty">未记录变更文件</div>'}</div></section>`;
  return reportDocument(task, reportName(task, 'change'), body);
}

function buildTestReport(task: AnalysisTask): string {
  const report = task.test_report || {};
  const summary = report.summary || {};
  const draftStatusById = new Map((task.test_requirement_drafts || []).map(item => [item.id, item.status]));
  const points = ((report.test_requirements || []) as ReportTestPoint[])
    .filter(item => !item.id || draftStatusById.get(item.id) !== 'ignored');
  const riskPoints = points.filter(item => riskGroups.has(item.change_group || '')).sort(
    (left, right) => (priorityOrder[left.priority || ''] ?? 9) - (priorityOrder[right.priority || ''] ?? 9),
  );
  const iterationPoints = points.filter(item => !riskGroups.has(item.change_group || '')).sort(
    (left, right) => (priorityOrder[left.priority || ''] ?? 9) - (priorityOrder[right.priority || ''] ?? 9),
  );
  const pointCards = (items: ReportTestPoint[], type: '迭代验证' | '风险排查') => items.map((item, index) => `
    <article class="card">
      <div class="card-head">
        <div class="card-title"><span class="badge ${escapeHtml(item.priority)}">${escapeHtml(priorityLabel(item.priority))}</span><strong>${index + 1}. ${escapeHtml(item.title)}</strong></div>
        <span class="source">${type}</span>
      </div>
      ${item.risk_reference?.file ? `<div class="path">${escapeHtml(item.risk_reference.file)}</div>` : ''}
      <div class="detail"><b>测试目标</b><span>${richText(item.objective)}</span></div>
      <div class="detail"><b>预期结果</b><span>${richText(item.expected_result)}</span></div>
      <div class="detail"><b>来源</b><span>${escapeHtml(item.risk_reference?.title || item.source_finding_key || '综合分析')}</span></div>
    </article>`).join('');
  const iterationSummary = report.iteration_summary;
  const summaryPoints = iterationSummary?.summary_points || iterationSummary?.change_items || [];
  const groups = iterationSummary?.change_groups || [];
  const summaryBlock = iterationSummary?.title ? `
    <section class="section">
      <div class="summary"><h3>${escapeHtml(iterationSummary.title)}</h3><p>${richText(iterationSummary.description)}</p></div>
      ${summaryPoints.length ? `<div class="focus">${summaryPoints.map(item => `<span class="chip">${escapeHtml(item)}</span>`).join('')}</div>` : ''}
      ${groups.length ? `<div class="group-grid">${groups.map(group => `<article class="group"><b>${escapeHtml(group.name)}</b><p>${richText(group.description)}</p><small>测试关注：${escapeHtml(group.test_focus)}</small></article>`).join('')}</div>` : ''}
    </section>` : '';
  const gaps = (report.coverage_gaps || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  const body = `
    <section class="metrics">
      ${metric('需求测试点', iterationPoints.length)}
      ${metric('风险测试点', riskPoints.length)}
      ${metric('高优先级', numberValue(summary.high_priority_count), 'danger')}
      ${metric('有效测试点', points.length, 'positive')}
    </section>
    ${summaryBlock}
    <section class="section"><div class="section-head"><div><h2>需求测试点</h2><p>依据本次迭代变更生成</p></div><span class="count">按优先级排序 · ${iterationPoints.length} 项</span></div>${pointCards(iterationPoints, '迭代验证') || '<div class="empty">暂无需求测试点</div>'}</section>
    <section class="section"><div class="section-head"><div><h2>风险测试点</h2><p>依据代码审查风险生成</p></div><span class="count">按优先级排序 · ${riskPoints.length} 项</span></div>${pointCards(riskPoints, '风险排查') || '<div class="empty">暂无风险测试点</div>'}</section>
    <section class="section"><div class="section-head"><div><h2>覆盖缺口</h2><p>建议进一步确认的测试范围</p></div></div>${gaps ? `<ul>${gaps}</ul>` : '<div class="empty">未记录覆盖缺口</div>'}</section>`;
  return reportDocument(task, reportName(task, 'test'), body);
}

export function buildHtmlReport(task: AnalysisTask, type: ReportType): string {
  return type === 'change' ? buildChangeReport(task) : buildTestReport(task);
}

export function downloadHtmlReport(task: AnalysisTask, type: ReportType): void {
  const blob = new Blob([buildHtmlReport(task, type)], { type: 'text/html;charset=utf-8' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${reportName(task, type)}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
