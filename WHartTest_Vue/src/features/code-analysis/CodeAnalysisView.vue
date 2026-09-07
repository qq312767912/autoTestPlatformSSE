<template>
  <div class="analysis-page">
    <header class="hero">
      <div>
        <div class="eyebrow">QUALITY INTELLIGENCE</div>
        <h1>代码审查</h1>
        <p>对代码变更进行多维审查，识别风险、评估业务影响，并生成可执行的测试需求与回归建议。</p>
      </div>
      <div class="hero-actions">
        <a-button @click="configVisible = true"><template #icon><icon-settings /></template>审查源配置</a-button>
        <a-button type="primary" @click="openCreate"><template #icon><icon-plus /></template>新建分析</a-button>
      </div>
    </header>

    <section class="metric-grid">
      <div class="metric"><span>分析任务</span><strong>{{ tasks.length }}</strong></div>
      <div class="metric danger"><span>高风险</span><strong>{{ highRiskTotal }}</strong></div>
      <div class="metric"><span>需求测试点</span><strong>{{ testPointTotal }}</strong></div>
      <div class="metric"><span>完成率</span><strong>{{ completionRate }}%</strong></div>
    </section>

    <section class="content-card">
      <div class="section-head"><div><h2>审查记录</h2><p>结果在项目内共享，GitLab Token 始终按用户隔离。</p></div><a-button type="text" @click="() => loadTasks()"><icon-refresh /> 刷新</a-button></div>
      <a-empty v-if="!loading && !tasks.length" description="还没有代码审查任务" />
      <a-spin :loading="loading" style="width:100%">
        <div v-for="task in tasks" :key="task.id" class="task-row" @click="selectedTask = task">
          <div class="task-mark" :class="task.status"></div>
          <div class="task-main"><div class="task-title">{{ task.title || task.repository_name }}</div><div class="task-meta"><span v-if="isPlatformAdmin">{{ task.project_name || `项目 ${task.project}` }} · </span>{{ task.repository_name }} · {{ sourceLabel(task) }} · {{ task.creator_name }}</div></div>
          <div class="task-score"><span>风险</span><b>{{ task.change_report?.summary?.risk_count || 0 }}</b></div>
          <div class="task-score"><span>测试点</span><b>{{ task.test_report?.summary?.test_point_count || 0 }}</b></div>
          <a-tag :color="statusColor(task.status)">{{ statusLabel(task.status) }}</a-tag>
          <a-progress :percent="task.progress / 100" :show-text="false" size="small" style="width:90px" />
          <a-button v-if="isRunning(task)" type="text" status="warning" @click.stop="cancelAnalysis(task)">取消</a-button>
          <a-button v-else-if="canRerun(task)" type="text" status="success" @click.stop="rerunAnalysis(task)">重跑</a-button>
          <a-button type="text" status="danger" @click.stop="removeTask(task)"><icon-delete /></a-button>
        </div>
      </a-spin>
    </section>

    <a-drawer :visible="!!selectedTask" :width="780" unmount-on-close @cancel="selectedTask = null">
      <template #title>{{ selectedTask?.title || '分析详情' }}</template>
      <div v-if="selectedTask" class="iteration-overview">
        <div class="overview-title"><div><span>本次迭代分析</span><h2>{{ iterationConclusion(selectedTask) }}</h2></div><a-tag :color="selectedTask.change_report?.summary?.high_risk_count ? 'red' : 'green'">{{ selectedTask.change_report?.summary?.high_risk_count ? '建议重点回归' : '常规回归' }}</a-tag></div>
        <div class="overview-numbers">
          <div><b>{{ selectedTask.change_report?.summary?.changed_files || 0 }}</b><span>变更文件</span></div>
          <div class="addition"><b>+{{ selectedTask.change_report?.summary?.additions || 0 }}</b><span>新增行</span></div>
          <div class="deletion"><b>-{{ selectedTask.change_report?.summary?.deletions || 0 }}</b><span>删除行</span></div>
          <div><b>{{ selectedTask.change_report?.summary?.changed_lines || 0 }}</b><span>总变更行</span></div>
          <div class="danger"><b>{{ selectedTask.change_report?.summary?.high_risk_count || 0 }}</b><span>高风险</span></div>
          <div><b>{{ selectedTask.change_report?.summary?.risk_count || 0 }}</b><span>风险提示</span></div>
          <div><b>{{ selectedTask.test_report?.summary?.test_point_count || 0 }}</b><span>需求测试点</span></div>
          <div><b>{{ displayedImpactCount(selectedTask) }}</b><span>重点影响</span></div>
        </div>
        <div class="overview-focus" v-if="selectedTask.change_report?.impact_summary?.length"><b>重点影响</b><span v-for="item in selectedTask.change_report.impact_summary.slice(0,3)" :key="item">{{ item }}</span></div>
        <div v-if="selectedTask.change_report?.impact_modules?.length" class="impact-module-grid"><div v-for="item in selectedTask.change_report.impact_modules.slice(0,4)" :key="`${item.module}-${item.change_type}`"><b>{{ item.module || '关联模块' }}</b><span>{{ item.change_type || '变更影响' }}</span><small>{{ item.regression_scope || item.impact }}</small></div></div>
        <div class="overview-files" v-if="selectedTask.change_report?.files?.length">
          <b>变更文件</b>
          <div v-for="file in selectedTask.change_report.files.slice(0,5)" :key="file.path"><code>{{ file.path }}</code><span class="line-stat"><i>+{{ file.additions || 0 }}</i><em>-{{ file.deletions || 0 }}</em></span></div>
          <small v-if="selectedTask.change_report.files.length > 5">其余 {{ selectedTask.change_report.files.length - 5 }} 个文件请在报告中查看</small>
        </div>
      </div>
      <a-collapse v-if="selectedTask" :bordered="false" class="input-collapse">
        <a-collapse-item key="input" header="分析输入与运行信息">
      <div class="analysis-context">
        <div class="context-head">
          <div><span class="context-kicker">ANALYSIS INPUT</span><h3>{{ selectedTask.repository_name }}</h3></div>
          <a-tag color="arcoblue">{{ modeLabel(selectedTask.mode) }}</a-tag>
        </div>
        <div class="context-grid">
          <div><span>分析来源</span><b>{{ sourceLabel(selectedTask) }}</b></div>
          <div><span>提交范围</span><b class="mono">{{ shortSha(selectedTask.base_sha) }} → {{ shortSha(selectedTask.head_sha) }}</b></div>
          <div><span>发起人</span><b>{{ selectedTask.creator_name }}</b></div>
          <div><span>完成时间</span><b>{{ formatTime(selectedTask.completed_at) }}</b></div>
          <div><span>机器分析覆盖</span><b>{{ selectedTask.machine_coverage }}%</b></div>
          <div><span>AI分析覆盖</span><b>{{ selectedTask.ai_coverage }}%</b></div>
          <div class="wide"><span>AI用量</span><b>{{ selectedTask.token_usage || 0 }} Token <a-tooltip content="本次AI分析读取的输入内容与生成内容的计量单位，用于观察上下文规模和推理成本，不代表分析质量。"><icon-question-circle class="help-icon" /></a-tooltip></b></div>
        </div>
        <div v-if="executionLogs.length" class="execution-log">
          <div class="execution-log-head"><b>执行记录</b><small>用于查看重试、取消与最终覆盖情况</small></div>
          <div v-for="entry in executionLogs" :key="entry.id" class="execution-log-item">
            <span class="execution-dot" :class="entry.event"></span><time>{{ formatTime(entry.created_at) }}</time><b>{{ executionEventLabel(entry.event) }}</b><span>{{ entry.message }}</span><em>执行人：{{ entry.actor_name || '系统' }}</em>
          </div>
        </div>
      </div>
        </a-collapse-item>
      </a-collapse>
      <a-tabs v-if="selectedTask" default-active-key="change" class="report-tabs">
        <a-tab-pane key="change" title="代码审查报告">
          <div class="report-toolbar"><div><h3>风险与影响</h3><p>确定性规则与 AI 风险提示的融合结果</p></div><a-button type="outline" @click="download(selectedTask, 'change')"><template #icon><icon-download /></template>下载报告</a-button></div>
          <a-alert v-if="ocrStatusNotice" :type="ocrStatusNotice.type" class="ocr-status-alert"><b>{{ ocrStatusNotice.title }}</b><span>{{ ocrStatusNotice.message }}</span></a-alert>
          <a-collapse v-if="ocrDiagnostics" :bordered="false" class="ocr-diagnostics">
            <a-collapse-item key="ocr-diagnostics" header="OCR 完整度与失败明细">
              <div class="ocr-diagnostic-summary">
                <span>选中 <b>{{ ocrDiagnostics.selected || 0 }}</b></span><span>完成 <b>{{ ocrDiagnostics.completed || 0 }}</b></span><span>复用 <b>{{ ocrDiagnostics.reused || 0 }}</b></span><span class="failed">失败 <b>{{ ocrDiagnostics.failed || 0 }}</b></span><span>耗时 <b>{{ ocrDiagnostics.elapsed || '—' }}</b></span><span>并发 <b>{{ ocrDiagnostics.configured_concurrency || '—' }}</b></span>
              </div>
              <div v-if="Object.keys(ocrDiagnostics.failure_type_counts || {}).length" class="ocr-failure-types"><b>失败类型</b><a-tag v-for="(count,type) in ocrDiagnostics.failure_type_counts" :key="type" color="orangered">{{ ocrFailureType(String(type)) }} {{ count }}</a-tag></div>
              <div v-if="ocrDiagnostics.groups?.length" class="ocr-group-list">
                <article v-for="group in ocrDiagnostics.groups" :key="group.label"><div><b>{{ group.label }}</b><a-tag :color="group.failed ? 'orange' : 'green'">覆盖 {{ group.coverage }}%</a-tag></div><small>{{ group.completed }}/{{ group.files?.length || 0 }} 完成<span v-if="group.failed">，{{ group.failed }} 失败</span></small><p v-if="group.failed_files?.length">失败文件：{{ group.failed_files.join('、') }}</p></article>
              </div>
              <div v-if="ocrDiagnostics.retry" class="ocr-retry-note">模型请求 {{ ocrDiagnostics.retry.total_requests || 0 }} 次；重试 {{ ocrDiagnostics.retry.retried_requests || 0 }} 次，恢复 {{ ocrDiagnostics.retry.recovered_requests || 0 }} 次，最终失败 {{ ocrDiagnostics.retry.failed_requests || 0 }} 次。</div>
              <div v-if="ocrDiagnostics.resume?.triggered" class="ocr-resume-note">
                <b>OCR 自动续审：</b>首轮失败 {{ ocrDiagnostics.resume.before_failed || 0 }} 个文件，低并发续审恢复 {{ ocrDiagnostics.resume.recovered_files || 0 }} 个，仍失败 {{ ocrDiagnostics.resume.after_failed || 0 }} 个；续审后 OCR 覆盖 {{ ocrDiagnostics.resume.after_coverage || 0 }}%。
              </div>
              <div v-if="ocrDiagnostics.fallback?.triggered" class="ocr-fallback-note">
                <b>失败文件补审：</b>OCR 失败的 {{ ocrDiagnostics.fallback.file_count || 0 }} 个文件已继续交给平台 AI 分析；补审覆盖 {{ ocrDiagnostics.fallback.coverage || 0 }}%，综合 AI 覆盖 {{ ocrDiagnostics.fallback.effective_coverage || 0 }}%。
                <span v-if="ocrDiagnostics.fallback.status === 'failed'">补审失败，但已保留 OCR 完成结果，迭代总结和需求测试点仍会继续生成。</span>
              </div>
              <div v-if="ocrDiagnostics.tool_failures?.length" class="ocr-tool-failure"><b>工具调用失败</b><p v-for="(failure,index) in ocrDiagnostics.tool_failures" :key="index">{{ failure.tool_name }}：{{ failure.error }}</p></div>
            </a-collapse-item>
          </a-collapse>
          <div class="report-summary">
            <span>风险总数 <b>{{ selectedTask.change_report?.summary?.risk_count || 0 }}</b></span>
            <span class="risk-number">高风险 <b>{{ selectedTask.change_report?.summary?.high_risk_count || 0 }}</b></span>
          </div>
          <div v-if="selectedTask.change_report?.summary?.source_counts" class="quality-breakdown"><b>风险来源</b><span v-for="(count, source) in selectedTask.change_report.summary.source_counts" :key="source">{{ sourceName(String(source)) }} {{ count }}</span></div>
          <div v-if="selectedTask.change_report?.findings?.length" class="finding-filter">
            <a-select v-model="riskSeverityFilter" allow-clear placeholder="全部风险级别" :style="{width:'148px'}"><a-option value="high">高风险</a-option><a-option value="medium">中风险</a-option><a-option value="low">低风险</a-option></a-select>
            <a-select v-model="riskTypeFilter" allow-clear placeholder="全部风险类型" :style="{width:'178px'}"><a-option v-for="type in availableRiskTypes" :key="type" :value="type">{{ type }}</a-option></a-select>
            <span>展示 {{ filteredFindings.length }} / {{ selectedTask.change_report.findings.length }} 项，按风险级别排序</span>
          </div>
          <div v-for="item in filteredFindings" :key="item.key" class="finding">
            <div class="finding-head"><div><a-tag :color="severityColor(item.severity)">{{ severityLabel(item.severity) }}</a-tag><strong>{{ item.change }}</strong></div><span class="confidence">{{ sourceName(item.source) }} · {{ item.verified === false ? '待人工确认' : confidenceText(item.confidence) }}</span></div>
            <p>{{ item.file }} <a-tag class="risk-type" color="gray">{{ riskType(item) }}</a-tag></p>
            <div class="code-snippet"><div class="snippet-caption"><span>风险凭据</span><small>{{ item.line_start ? `旧版本第 ${item.line_start} 行` : '未定位源码行' }}</small></div><div v-for="(line, index) in evidenceLines(item)" :key="index" class="snippet-line"><span>{{ item.line_start ? item.line_start + index : '—' }}</span><code>{{ line || ' ' }}</code></div></div>
            <div class="finding-detail"><b>影响：</b><span>{{ item.impact || '需结合代码上下文和完整 Diff 确认实际影响范围' }}</span></div>
            <div class="finding-detail"><b>建议：</b><span>{{ item.recommendation || '覆盖相关正常流程、异常分支及调用链后再决定是否修复' }}</span></div>
            <div class="patch-actions">
              <a-link class="diff-link" @click="openDiff(item)"><icon-file /> 查看相关 Diff</a-link>
              <a-link class="diff-link fix-link" :disabled="patchLoadingKey === item.key" @click="openSuggestedPatch(item)"><icon-file /> {{ patchLoadingKey === item.key ? '正在生成…' : '查看建议修复' }}</a-link>
              <a-tag v-if="item.patch_status" size="small" :color="item.patch_status === 'applicable' ? 'green' : 'gray'">{{ item.patch_status === 'applicable' ? '可应用' : '仅供参考' }}</a-tag>
            </div>
          </div>
          <a-empty v-if="selectedTask.change_report?.findings?.length && !filteredFindings.length" description="没有符合筛选条件的风险" />
          <a-empty v-if="!selectedTask.change_report?.findings?.length" description="未发现确定性风险" />
        </a-tab-pane>
        <a-tab-pane key="test" title="测试分析报告">
          <div class="report-toolbar"><div><h3>需求测试点与风险点</h3><p>需求测试点仍为草稿，确认后再转正式用例</p></div><a-button type="outline" @click="download(selectedTask, 'test')"><template #icon><icon-download /></template>下载报告</a-button></div>
          <section v-if="selectedTask.test_report?.iteration_summary?.title" class="report-section iteration-summary">
            <h3>本次迭代总结</h3>
            <strong>{{ selectedTask.test_report.iteration_summary.title }}</strong>
            <div class="iteration-summary-points">
              <div v-for="(item, index) in iterationSummaryItems" :key="`${index}-${item.title}`" class="iteration-summary-point">
                <span class="summary-index">{{ index + 1 }}.</span>
                <div><b>{{ item.title }}</b><p>{{ item.content }}</p></div>
              </div>
            </div>
            <div v-if="iterationGroups.length" class="iteration-group-grid">
              <article v-for="group in iterationGroups" :key="group.name" class="iteration-group-card">
                <div><b>{{ group.name }}</b><a-tag color="arcoblue">{{ testCountForGroup(group.name) }} 个测试点</a-tag></div>
                <span v-if="group.module" class="group-module">{{ group.module }}</span>
                <p>{{ group.description }}</p><small>测试关注：{{ group.test_focus }}</small>
                <div class="group-files"><code v-for="file in group.files || []" :key="file">{{ file }}</code></div>
              </article>
            </div>
          </section>
          <section class="report-section risk-summary">
            <h3>风险总结</h3>
            <p v-if="riskTestRequirements.length">已从代码审查风险生成 {{ riskTestRequirements.length }} 个风险测试点，其中高优先级 {{ riskHighPriorityCount }} 个。</p>
            <p v-else>本次变更未生成需单独验证的风险测试点。</p>
          </section>
          <div class="report-summary"><span>有效需求测试点 <b>{{ filteredTestRequirements.length }}</b></span><span class="risk-number">高优先级 <b>{{ visibleHighPriorityCount }}</b></span></div>
          <div class="quality-breakdown"><b>覆盖类型</b><span v-for="(count, testType) in testTypeCounts" :key="testType">{{ testType }} {{ count }}</span></div>
          <div v-if="selectedTask.test_report?.test_requirements?.length" class="finding-filter">
            <a-select v-model="testPriorityFilter" allow-clear placeholder="全部优先级" :style="{width:'148px'}"><a-option value="high">高优先级</a-option><a-option value="medium">中优先级</a-option><a-option value="low">低优先级</a-option></a-select>
            <a-select v-model="testTypeFilter" multiple allow-clear placeholder="全部测试类型" :style="{width:'220px'}"><a-option v-for="type in availableTestTypes" :key="type" :value="type">{{ type }}</a-option></a-select>
            <span>展示 {{ filteredTestRequirements.length + filteredRiskTestRequirements.length }} / {{ visibleTestRequirements.length }} 项，已忽略的草稿不纳入报告</span>
          </div>
          <h3 class="point-section-title">需求测试点</h3>
          <div v-for="item in filteredTestRequirements" :key="item.id" class="test-point">
            <div class="finding-head"><div><a-tag :color="priorityColor(item.priority)">{{ priorityLabel(item.priority) }}</a-tag><a-tag color="arcoblue">迭代验证</a-tag><strong>{{ item.title }}</strong></div><div class="draft-actions"><a-tag :color="draftStatusColor(draftStatus(item))">{{ draftStatusLabel(draftStatus(item)) }}</a-tag><a-button v-if="draftStatus(item) === 'draft'" type="text" size="mini" @click="acceptDraft(item)">采纳</a-button><a-button v-if="draftStatus(item) !== 'converted'" type="text" size="mini" status="danger" @click="ignoreDraft(item)">忽略</a-button><a-button v-if="draftStatus(item) === 'accepted'" type="text" size="mini" status="success" @click="openConvert(item)">转正式用例</a-button></div></div>
            <p><b>测试目标：</b>{{ item.objective }}</p><div class="expected"><b>预期结果：</b>{{ item.expected_result }}</div><div class="source-key">来源：{{ item.source_finding_key || '综合分析' }}</div>
          </div>
          <template v-if="filteredRiskTestRequirements.length">
            <h3 class="point-section-title">风险测试点</h3>
            <div v-for="item in filteredRiskTestRequirements" :key="item.id" class="test-point risk-test-point">
              <div class="finding-head"><div><a-tag :color="priorityColor(item.priority)">{{ priorityLabel(item.priority) }}</a-tag><a-tag color="orange">风险排查</a-tag><strong>{{ item.title }}</strong></div><div class="draft-actions"><a-tag :color="draftStatusColor(draftStatus(item))">{{ draftStatusLabel(draftStatus(item)) }}</a-tag><a-button v-if="draftStatus(item) === 'draft'" type="text" size="mini" @click="acceptDraft(item)">采纳</a-button><a-button v-if="draftStatus(item) !== 'converted'" type="text" size="mini" status="danger" @click="ignoreDraft(item)">忽略</a-button><a-button v-if="draftStatus(item) === 'accepted'" type="text" size="mini" status="success" @click="openConvert(item)">转正式用例</a-button></div></div>
              <p><b>测试目标：</b>{{ item.objective }}</p><div class="expected"><b>预期结果：</b>{{ item.expected_result }}</div><div class="source-key">关联代码审查风险：{{ item.risk_reference?.title || item.source_finding_key }}<template v-if="item.risk_reference?.file">（{{ item.risk_reference.file }}）</template></div>
            </div>
          </template>
          <a-empty v-if="!selectedTask.test_report?.test_requirements?.length" description="暂无需求测试点" />
          <div class="report-section gap"><h3>覆盖缺口</h3><ul v-if="selectedTask.test_report?.coverage_gaps?.length"><li v-for="item in selectedTask.test_report.coverage_gaps" :key="item">{{ item }}</li></ul><a-empty v-else description="未记录覆盖缺口" /></div>
        </a-tab-pane>
      </a-tabs>
    </a-drawer>

    <a-modal v-model:visible="convertVisible" title="转为正式测试用例" :ok-loading="converting" @ok="convertDraft">
      <p class="convert-description">将保留代码审查任务与风险来源，正式用例可在“测试用例”模块继续维护。</p>
      <a-form layout="vertical"><a-form-item label="目标用例模块" required><a-select v-model="convertModuleId" placeholder="选择当前项目下的用例模块"><a-option v-for="module in testcaseModules" :key="module.id" :value="module.id">{{ module.name }}</a-option></a-select></a-form-item></a-form>
    </a-modal>

    <a-modal v-model:visible="diffVisible" :title="diffTitle" width="960px" :footer="false" unmount-on-close>
      <a-alert v-if="diffNotice" :type="diffNoticeType" class="diff-notice">{{ diffNotice }}</a-alert>
      <a-spin :loading="diffLoading" style="width:100%"><div v-if="diffLines.length" class="diff-view"><div v-for="(line, index) in diffLines" :key="index" class="diff-line" :class="diffLineClass(line)"><span>{{ index + 1 }}</span><code>{{ line || ' ' }}</code></div></div><a-empty v-else-if="!diffLoading" description="未生成可展示的建议修复补丁" /></a-spin>
    </a-modal>

    <a-modal v-model:visible="createVisible" title="新建代码审查" :ok-loading="submitting" @ok="submitTask">
      <a-form :model="form" layout="vertical">
        <a-form-item label="代码仓库" required><a-select v-model="form.repository" @change="onRepositoryChange"><a-option v-for="r in repositories" :key="r.id" :value="r.id">{{ r.name }} · {{ r.source_type === 'local_git' ? '本地 Git' : r.path_with_namespace }}</a-option></a-select></a-form-item>
        <a-form-item label="分析来源"><a-radio-group v-model="form.source_type" type="button"><a-radio v-if="selectedRepository?.source_type !== 'local_git'" value="merge_request">Merge Request</a-radio><a-radio value="commits">两个分支 / Commit</a-radio></a-radio-group></a-form-item>
        <a-form-item v-if="form.source_type === 'merge_request'" label="Merge Request" required><a-select v-model="form.merge_request_iid" :loading="mrLoading"><a-option v-for="mr in mergeRequests" :key="mr.iid" :value="mr.iid">!{{ mr.iid }} {{ mr.title }}</a-option></a-select></a-form-item>
        <template v-else><a-form-item label="基准 Commit" required><a-input v-model="form.base_sha" /></a-form-item><a-form-item label="目标 Commit" required><a-input v-model="form.head_sha" /></a-form-item></template>
        <a-form-item label="需求文档（可选）"><a-select v-model="form.requirement_document_ids" multiple allow-clear placeholder="可选择多篇已上传的需求文档" :max-tag-count="2"><a-option v-for="doc in projectDocuments" :key="doc.id" :value="doc.id">{{ doc.title }}</a-option></a-select></a-form-item>
        <a-form-item label="接口文档（可选）"><a-select v-model="form.api_document_ids" multiple allow-clear placeholder="可选择多篇已上传的接口/设计文档" :max-tag-count="2"><a-option v-for="doc in projectDocuments" :key="doc.id" :value="doc.id">{{ doc.title }}</a-option></a-select></a-form-item>
        <a-form-item>
          <template #label>分析模式 <a-tooltip position="right"><icon-question-circle class="mode-help" /><template #content><div class="mode-tooltip"><div><b>快速</b><span>仅规则扫描；最快、零 Token。适合提交前筛查。</span></div><div><b>标准</b><span>规则 + AI 分批分析；速度与质量均衡，适合日常审查。</span></div><div><b>深度</b><span>扩大 AI 覆盖范围；适合核心改造与发布前审查，耗时和 Token 较高。</span></div></div></template></a-tooltip></template>
          <a-radio-group v-model="form.mode" type="button"><a-radio value="quick">快速</a-radio><a-radio value="standard">标准</a-radio><a-radio value="deep">深度</a-radio></a-radio-group>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="configVisible" title="审查源配置" :footer="false" width="680px">
      <a-tabs>
        <a-tab-pane key="local" title="本地 Git（开发测试）">
          <a-form :model="localRepoForm" layout="vertical"><a-alert type="info" style="margin-bottom:14px">仅能读取容器内 <code>/workspace</code> 挂载目录。仓库根目录填 <code>.</code>；子目录示例：<code>demo_repositories/项目名</code>。不支持绝对路径或 <code>../</code>。</a-alert><a-form-item label="仓库名称"><a-input v-model="localRepoForm.name" /></a-form-item><a-form-item label="本地路径"><a-input v-model="localRepoForm.local_path" placeholder=".（仓库根目录）" /></a-form-item><a-button type="primary" @click="addLocalRepository">关联本地仓库</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="connection" title="GitLab连接">
          <a-form :model="connectionForm" layout="vertical"><a-form-item label="名称"><a-input v-model="connectionForm.name" /></a-form-item><a-form-item label="GitLab地址"><a-input v-model="connectionForm.base_url" placeholder="https://gitlab.example.com" /></a-form-item><a-button type="primary" @click="addConnection">保存连接</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="repository" title="项目仓库">
          <a-form :model="repoForm" layout="vertical"><a-form-item label="GitLab连接"><a-select v-model="repoForm.connection"><a-option v-for="c in connections" :key="c.id" :value="c.id">{{ c.name }}</a-option></a-select></a-form-item><a-form-item label="GitLab项目ID"><a-input v-model="repoForm.gitlab_project_id" /></a-form-item><a-form-item label="仓库名称"><a-input v-model="repoForm.name" /></a-form-item><a-form-item label="项目路径"><a-input v-model="repoForm.path_with_namespace" /></a-form-item><a-button type="primary" @click="addRepository">关联仓库</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="token" title="我的Token">
          <a-form :model="tokenForm" layout="vertical"><a-form-item label="GitLab连接"><a-select v-model="tokenForm.connection"><a-option v-for="c in connections" :key="c.id" :value="c.id">{{ c.name }}</a-option></a-select></a-form-item><a-form-item label="Personal Access Token"><a-input-password v-model="tokenForm.token" placeholder="仅用于当前用户只读访问" /></a-form-item><a-button type="primary" @click="saveToken">加密保存</a-button></a-form>
        </a-tab-pane>
      </a-tabs>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { IconDelete, IconDownload, IconFile, IconPlus, IconQuestionCircle, IconRefresh, IconSettings } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { useAuthStore } from '@/store/authStore';
import type { AnalysisExecutionLog, AnalysisTask, CodeRepository, GitLabConnection, MergeRequest } from './types';
import * as api from './service';

const projectStore = useProjectStore();
const authStore = useAuthStore();
const isPlatformAdmin = computed(() => !!authStore.currentUser?.is_staff);
const tasks = ref<AnalysisTask[]>([]), repositories = ref<CodeRepository[]>([]), connections = ref<GitLabConnection[]>([]), mergeRequests = ref<MergeRequest[]>([]), projectDocuments = ref<any[]>([]);
const loading = ref(false), submitting = ref(false), mrLoading = ref(false), createVisible = ref(false), configVisible = ref(false);
const selectedTask = ref<AnalysisTask|null>(null);
const executionLogs = ref<AnalysisExecutionLog[]>([]);
const testcaseModules = ref<any[]>([]), convertVisible = ref(false), converting = ref(false), convertModuleId = ref<number|undefined>(), convertingDraft = ref<any>(null);
const riskSeverityFilter = ref<string|undefined>();
const riskTypeFilter = ref<string|undefined>();
const testPriorityFilter = ref<string|undefined>();
const testTypeFilter = ref<string[]>([]);
const diffVisible = ref(false), diffLoading = ref(false), diffTitle = ref('代码 Diff'), diffText = ref(''), diffNotice = ref(''), diffNoticeType = ref<'success'|'warning'|'info'>('info');
const patchLoadingKey = ref('');
const form = reactive<any>({ repository:null, source_type:'commits', merge_request_iid:null, base_sha:'HEAD~1', head_sha:'HEAD', requirement_document_ids:[], api_document_ids:[], mode:'standard' });
const connectionForm = reactive<any>({ name:'内网 GitLab', base_url:'', verify_ssl:true, is_active:true });
const repoForm = reactive<any>({ connection:null, gitlab_project_id:'', name:'', path_with_namespace:'', default_branch:'main' });
const localRepoForm = reactive<any>({ name:'当前工作区', local_path:'.' });
const selectedRepository = computed(() => repositories.value.find(item => item.id === form.repository));
const severityOrder:Record<string,number> = { high:0, medium:1, low:2 };
const riskType = (item:any) => {
  const key = item.key || '';
  if (key.startsWith('annotation:')) return '注解与接口契约';
  if (key.startsWith('pydecorator:') || key.startsWith('frontend-permission:')) return '权限与访问控制';
  if (key.startsWith('config:')) return '配置与部署';
  if (key.startsWith('modifier:')) return '代码语义';
  if (key.startsWith('file_deleted:')) return '文件删除';
  if (item.source === 'ai_analysis') return 'AI语义风险';
  return '其他变更风险';
};
const availableRiskTypes = computed(() => Array.from(new Set((selectedTask.value?.change_report?.findings || []).map(riskType))).sort());
const filteredFindings = computed(() => (selectedTask.value?.change_report?.findings || []).filter(item => (!riskSeverityFilter.value || item.severity === riskSeverityFilter.value) && (!riskTypeFilter.value || riskType(item) === riskTypeFilter.value)).slice().sort((a,b) => (severityOrder[a.severity] ?? 9) - (severityOrder[b.severity] ?? 9)));
const ocrStatusNotice = computed(() => {
  const status = selectedTask.value?.change_report?.ocr_status;
  if (!status) {
    const legacyNote = selectedTask.value?.change_report?.analysis_note || '';
    return legacyNote.includes('OCR 不可用') ? { type:'error' as const, title:'OCR 审查失败', message:'本报告已使用 AI 降级分析结果；可在执行记录中查看失败原因。' } : null;
  }
  if (status.status === 'completed' || status.status === 'skipped') return null;
  if (status.status === 'partial') return { type:'warning' as const, title:'OCR 审查部分完成', message:status.message || `已保留完成部分，当前覆盖 ${status.coverage || 0}%` };
  return { type:'error' as const, title:'OCR 审查失败', message:'本报告已使用 AI 降级分析结果；可在执行记录中查看失败原因。' };
});
const ocrDiagnostics = computed<any>(() => selectedTask.value?.change_report?.ocr_status?.diagnostics || null);
const ocrFailureType = (type:string) => ({network:'网络错误',rate_limit:'接口限流',timeout:'请求超时',agent_subtask:'Agent 子任务失败',provider:'模型服务失败',platform:'平台调用失败',unknown:'未知失败'} as Record<string,string>)[type] || type;
const evidenceLines = (item:any) => String(item.evidence || '未提供代码证据').split(/\r?\n/);
const diffLines = computed(() => diffText.value.split(/\r?\n/).filter((line, index, all) => line || index < all.length - 1));
const diffLineClass = (line:string) => line.startsWith('+') && !line.startsWith('+++') ? 'added' : (line.startsWith('-') && !line.startsWith('---') ? 'removed' : (line.startsWith('@@') ? 'hunk' : ''));
const relevantDiffFragment = (diff:string, targetLine?:number, evidence?:string) => {
  const lines = String(diff || '').split(/\r?\n/);
  const hunks:Array<{start:number;end:number;oldStart:number;oldCount:number;newStart:number;newCount:number}> = [];
  lines.forEach((line,index) => {
    const match = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
    if (!match) return;
    if (hunks.length) hunks[hunks.length - 1].end = index;
    hunks.push({start:index,end:lines.length,oldStart:Number(match[1]),oldCount:Number(match[2] || 1),newStart:Number(match[3]),newCount:Number(match[4] || 1)});
  });
  if (!hunks.length) return lines.slice(0,24).join('\n');
  const evidenceNeedle = String(evidence || '').split(/\r?\n/).map(line => line.trim()).find(line => line.length >= 8);
  let hunk = targetLine ? hunks.find(item =>
    (targetLine >= item.oldStart && targetLine < item.oldStart + Math.max(item.oldCount,1)) ||
    (targetLine >= item.newStart && targetLine < item.newStart + Math.max(item.newCount,1))) : undefined;
  if (!hunk && evidenceNeedle) hunk = hunks.find(item => lines.slice(item.start,item.end).some(line => line.includes(evidenceNeedle)));
  hunk ||= hunks[0];
  let oldLine = hunk.oldStart, newLine = hunk.newStart, anchor = hunk.start + 1;
  for (let index=hunk.start+1; index<hunk.end; index++) {
    const line = lines[index];
    const matchesLine = !!targetLine && ((line.startsWith('-') && oldLine === targetLine) || (line.startsWith('+') && newLine === targetLine) || (!line.startsWith('+') && !line.startsWith('-') && (oldLine === targetLine || newLine === targetLine)));
    const matchesEvidence = !!evidenceNeedle && line.includes(evidenceNeedle);
    if (matchesLine || matchesEvidence) { anchor=index; break; }
    if (!line.startsWith('+')) oldLine++;
    if (!line.startsWith('-')) newLine++;
  }
  const from = Math.max(hunk.start + 1, anchor - 6);
  const to = Math.min(hunk.end, anchor + 9);
  return [lines[hunk.start], ...lines.slice(from,to)].join('\n');
};
const impactScopeCount = (task:AnalysisTask) => {
  const saved = task.change_report?.summary?.impact_scope_count;
  if (typeof saved === 'number') return saved;
  return new Set((task.change_report?.files || []).map(file => (file.path || '').split('/').filter(Boolean).slice(0,2).join('/')).filter(Boolean)).size;
};
const displayedImpactCount = (task:AnalysisTask) => Math.min(task.change_report?.impact_summary?.length || 0, 3);
const visibleTestRequirements = computed(() => (selectedTask.value?.test_report?.test_requirements || []).filter(item => draftStatus(item) !== 'ignored'));
const riskTestRequirements = computed(() => visibleTestRequirements.value
  .filter(item => ['风险排查', '风险点', '风险回归'].includes(item.change_group))
  .slice()
  .sort((a,b) => (severityOrder[a.priority] ?? 9) - (severityOrder[b.priority] ?? 9)));
const iterationTestRequirements = computed(() => visibleTestRequirements.value.filter(item => !riskTestRequirements.value.includes(item)));
const testTypeFor = (item:any) => riskTestRequirements.value.includes(item) ? '风险排查' : '迭代验证';
const testTypeCounts = computed(() => ({
  '迭代验证': iterationTestRequirements.value.length,
  '风险排查': riskTestRequirements.value.length,
}));
const iterationGroupRanking = computed(() => {
  const groups = selectedTask.value?.test_report?.iteration_summary?.change_groups || [];
  const requirements = selectedTask.value?.test_report?.test_requirements || [];
  const scaleScore:Record<string,number> = {large:3,medium:2,small:1};
  return groups.map((group,index) => ({group,index,score:[scaleScore[group.change_scale || ''] || 0,(group.files || []).length,requirements.filter(item => item.change_group === group.name).length]}))
    .sort((a,b) => b.score[0]-a.score[0] || b.score[1]-a.score[1] || b.score[2]-a.score[2] || a.index-b.index);
});
const iterationGroups = computed(() => iterationGroupRanking.value.map(item => item.group));
const iterationSummaryItems = computed(() => {
  const summary = selectedTask.value?.test_report?.iteration_summary;
  if (!summary) return [];
  let points:string[] = [];
  if (summary.summary_points?.length) {
    points = summary.summary_points.length === iterationGroupRanking.value.length
      ? iterationGroupRanking.value.map(item => summary.summary_points![item.index])
      : summary.summary_points;
  } else if (iterationGroups.value.length) points = iterationGroups.value.map(item => item.description);
  else if (summary.change_items?.length) points = summary.change_items;
  else if (summary.description) points = [summary.description];
  return points.map((content,index) => {
    const group = iterationGroups.value[index];
    const matched = content.match(/^([^：:。；;]{2,24})[：:](.+)$/);
    const title = group?.name || matched?.[1]?.trim() || `变更点 ${index + 1}`;
    const detail = matched && (!group || matched[1].trim() === title) ? matched[2].trim() : content;
    return {title,content:detail};
  });
});
const testCountForGroup = (name:string) => visibleTestRequirements.value.filter(item => item.change_group === name).length;
const availableTestTypes = computed(() => ['迭代验证', '风险排查']);
const matchesTestType = (item:any) => !testTypeFilter.value.length || testTypeFilter.value.includes(testTypeFor(item));
const filteredTestRequirements = computed(() => iterationTestRequirements.value.filter(item => (!testPriorityFilter.value || item.priority === testPriorityFilter.value) && matchesTestType(item)).slice().sort((a,b) => (severityOrder[a.priority] ?? 9) - (severityOrder[b.priority] ?? 9)));
const filteredRiskTestRequirements = computed(() => riskTestRequirements.value.filter(item => (!testPriorityFilter.value || item.priority === testPriorityFilter.value) && matchesTestType(item)));
const visibleHighPriorityCount = computed(() => iterationTestRequirements.value.filter(item => item.priority === 'high').length);
const riskHighPriorityCount = computed(() => riskTestRequirements.value.filter(item => item.priority === 'high').length);
const tokenForm = reactive<any>({ connection:null, token:'' });
const highRiskTotal = computed(() => tasks.value.reduce((n,t) => n + (t.change_report?.summary?.high_risk_count || 0), 0));
const testPointTotal = computed(() => tasks.value.reduce((n,t) => n + (t.test_report?.summary?.test_point_count || 0), 0));
const completionRate = computed(() => tasks.value.length ? Math.round(tasks.value.filter(t => t.status === 'completed').length / tasks.value.length * 100) : 0);
const statusLabel = (s:string) => ({pending:'待执行',fetching:'获取代码中',machine_analyzing:'机器分析中',ai_analyzing:'AI分析中',generating_tests:'生成测试报告中',completed:'已完成',partial:'部分完成',failed:'失败',cancelled:'已取消'} as any)[s] || s;
const statusColor = (s:string) => ({completed:'green',failed:'red',partial:'orange',cancelled:'gray'} as any)[s] || 'arcoblue';
const sourceLabel = (t:AnalysisTask) => t.source_type === 'merge_request' ? `MR !${t.merge_request_iid}` : `${t.base_sha.slice(0,7)} → ${t.head_sha.slice(0,7)}`;
const shortSha = (sha:string) => sha ? sha.slice(0, 12) : '-';
const modeLabel = (mode:string) => ({quick:'快速模式',standard:'标准模式',deep:'深度模式'} as any)[mode] || mode;
const formatTime = (value?:string) => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '-';
const severityColor = (value:string) => ({high:'red',medium:'orange',low:'blue'} as any)[value] || 'gray';
const severityLabel = (value:string) => ({high:'高风险',medium:'中风险',low:'低风险'} as any)[value] || value;
const sourceName = (value:string) => ({machine_rule:'机器规则',static_scan:'静态分析',ocr_ai:'OCR Agent 审查',ai_analysis:'AI待确认提示',machine_ai:'机器+AI'} as any)[value] || value;
const confidenceText = (value:number) => Number.isFinite(value) ? `置信度 ${Math.round(value * 100)}%` : '置信度未知';
const priorityLabel = (value:string) => ({high:'高优先级',medium:'中优先级',low:'低优先级'} as any)[value] || value;
const priorityColor = (value:string) => ({high:'red',medium:'orange',low:'blue'} as any)[value] || 'gray';
const executionEventLabel = (event:string) => ({created:'已创建',queued:'已入队',started:'开始执行',stage_finished:'阶段结束',completed:'已完成',partial:'部分完成',failed:'执行失败',cancelled:'已取消'} as any)[event] || event;
const terminalStatuses = new Set(['completed','partial','failed','cancelled']);
const isRunning = (task:AnalysisTask) => !terminalStatuses.has(task.status);
const canRerun = (task:AnalysisTask) => ['completed','partial','failed','cancelled'].includes(task.status);
const draftStatus = (item:any) => selectedTask.value?.test_requirement_drafts?.find(draft => draft.id === item.id)?.status || 'draft';
const draftStatusLabel = (value:string) => ({draft:'待处理',accepted:'已采纳',ignored:'已忽略',converted:'已转正式用例'} as any)[value] || value;
const draftStatusColor = (value:string) => ({draft:'arcoblue',accepted:'green',ignored:'gray',converted:'purple'} as any)[value] || 'gray';
const iterationConclusion = (task:AnalysisTask) => {
  const high = task.change_report?.summary?.high_risk_count || 0;
  const risks = task.change_report?.summary?.risk_count || 0;
  if (high) return `发现 ${high} 项高风险变化，测试应优先覆盖关键影响链路`;
  if (risks) return `发现 ${risks} 项风险提示，建议结合影响范围执行回归`;
  return '未发现明显高风险变化，建议完成常规变更回归';
};
async function download(task:AnalysisTask,type:'change'|'test'){try{await api.downloadReport(task.id,type);Message.success('报告下载已开始')}catch(e:any){Message.error(e.message||'报告下载失败')}}
async function openDiff(item:any){if(!selectedTask.value)return;diffVisible.value=true;diffLoading.value=true;diffText.value='';diffNotice.value='';diffTitle.value=`相关 Diff · ${item.file || '变更文件'}`;try{const payload=await api.getTaskDiff(selectedTask.value.id,item.file);diffText.value=relevantDiffFragment(payload?.diff||'',item.line_start,item.evidence)}catch(e:any){Message.error(e.message||'读取 Diff 失败')}finally{diffLoading.value=false}}
async function openSuggestedPatch(item:any){
  if(!selectedTask.value || patchLoadingKey.value)return;
  patchLoadingKey.value=item.key;
  diffVisible.value=true;diffLoading.value=true;diffTitle.value=`建议修复 · ${item.file || '变更文件'}`;diffText.value='';
  diffNoticeType.value='info';diffNotice.value='纯审阅模式：正在生成最小修复补丁，平台不会修改仓库。';
  try{
    const generated=item.patch_status ? item : await api.generateSuggestedPatch(selectedTask.value.id,item.key);
    Object.assign(item,generated);diffText.value=generated.suggested_patch||'';
    const applicable=generated.patch_status==='applicable';diffNoticeType.value=applicable?'success':'warning';
    diffNotice.value=applicable?'纯审阅模式：该补丁已通过 git apply --check，但平台不会自动修改仓库。':`纯审阅模式：该建议仅供参考，未通过可应用性校验。${generated.patch_validation_message ? ` ${generated.patch_validation_message}` : ''}`;
  }catch(e:any){diffNoticeType.value='warning';diffNotice.value=e.message||'建议修复生成失败';Message.error(diffNotice.value)}finally{diffLoading.value=false;patchLoadingKey.value=''}
}
async function loadBase(){ const id=projectStore.currentProjectId; if(!id)return; [connections.value,repositories.value,projectDocuments.value]=await Promise.all([api.getConnections(),api.getRepositories(id),api.getProjectDocuments(id)]); }
async function refreshExecutionLogs(){if(!selectedTask.value)return;try{executionLogs.value=await api.getExecutionLogs(selectedTask.value.id)}catch{executionLogs.value=[]}}
async function loadTasks(silent=false){ const id=projectStore.currentProjectId;if(!id && !isPlatformAdmin.value)return;if(!silent)loading.value=true;try{tasks.value=await api.getTasks(isPlatformAdmin.value ? undefined : id!);if(selectedTask.value){selectedTask.value=tasks.value.find(task=>task.id===selectedTask.value?.id)||null;await refreshExecutionLogs()}}catch(e:any){if(!silent)Message.error(e.message)}finally{if(!silent)loading.value=false} }
async function openCreate(){ await loadBase(); if(!repositories.value.length){configVisible.value=true;Message.info('请先关联本地 Git 仓库或完成 GitLab 配置');return} createVisible.value=true; }
async function onRepositoryChange(v:any){mergeRequests.value=[];form.merge_request_iid=null;const repo=repositories.value.find(item=>item.id===v);if(!repo)return;if(repo.source_type==='local_git'){form.source_type='commits';return}mrLoading.value=true;try{mergeRequests.value=await api.getMergeRequests(Number(v))}catch(e:any){Message.error(`读取MR失败：${e.message}`)}finally{mrLoading.value=false}}
async function submitTask(){ const project=projectStore.currentProjectId;if(!project)return;submitting.value=true;try{const task=await api.createTask({...form,project});createVisible.value=false;await api.runTask(task.id);Message.success('分析任务已提交，可在列表查看进度');await loadTasks()}catch(e:any){Message.error(e.message||'提交分析失败');await loadTasks()}finally{submitting.value=false} }
async function addConnection(){try{await api.createConnection(connectionForm);connections.value=await api.getConnections();Message.success('连接已保存')}catch(e:any){Message.error(e.message)} }
async function addRepository(){const project=projectStore.currentProjectId;if(!project)return;try{await api.createRepository({...repoForm,project});repositories.value=await api.getRepositories(project);Message.success('仓库已关联')}catch(e:any){Message.error(e.message)} }
async function addLocalRepository(){const project=projectStore.currentProjectId;if(!project)return;try{await api.createRepository({project,source_type:'local_git',name:localRepoForm.name,path_with_namespace:localRepoForm.local_path,local_path:localRepoForm.local_path,default_branch:'main'});repositories.value=await api.getRepositories(project);Message.success('本地 Git 仓库已关联')}catch(e:any){Message.error(e.message)} }
async function saveToken(){const project=projectStore.currentProjectId;if(!project)return;try{await api.saveCredential({...tokenForm,project});tokenForm.token='';Message.success('Token已加密保存')}catch(e:any){Message.error(e.message)} }
async function cancelAnalysis(task:AnalysisTask){try{await api.cancelTask(task.id);Message.success('已取消分析任务');await loadTasks(true)}catch(e:any){Message.error(e.message||'取消失败')}}
async function rerunAnalysis(task:AnalysisTask){try{await api.runTask(task.id);Message.success('已重新提交分析任务');await loadTasks(true)}catch(e:any){Message.error(e.message||'重跑失败')}}
async function acceptDraft(item:any){try{await api.acceptTestRequirement(item.id);Message.success('测试需求已采纳');await loadTasks(true)}catch(e:any){Message.error(e.message||'采纳失败')}}
async function ignoreDraft(item:any){try{await api.ignoreTestRequirement(item.id);Message.success('测试需求已忽略');await loadTasks(true)}catch(e:any){Message.error(e.message||'忽略失败')}}
async function openConvert(item:any){const project=projectStore.currentProjectId;if(!project)return;try{testcaseModules.value=await api.getTestcaseModules(project);convertingDraft.value=item;convertModuleId.value=undefined;convertVisible.value=true}catch(e:any){Message.error(e.message||'读取用例模块失败')}}
async function convertDraft(){if(!convertingDraft.value || !convertModuleId.value){Message.warning('请选择目标用例模块');return}converting.value=true;try{await api.convertTestRequirement(convertingDraft.value.id,convertModuleId.value);convertVisible.value=false;Message.success('已转为正式测试用例');await loadTasks(true)}catch(e:any){Message.error(e.message||'转换失败')}finally{converting.value=false}}
async function removeTask(task:AnalysisTask){Modal.warning({title:'清空本次源码分析？',content:'Diff、源码证据、风险和测试分析报告将立即删除且无法恢复。正式测试用例不受影响。',hideCancel:false,onOk:async()=>{await api.deleteTask(task.id);if(selectedTask.value?.id===task.id)selectedTask.value=null;await loadTasks();}})}
watch(()=>projectStore.currentProjectId,async()=>{await loadBase();await loadTasks()});
watch(()=>selectedTask.value?.id,async(id)=>{riskSeverityFilter.value=undefined;riskTypeFilter.value=undefined;testPriorityFilter.value=undefined;testTypeFilter.value=[];executionLogs.value=[];if(!id)return;await refreshExecutionLogs()});
let pollTimer:number|undefined;
onMounted(async()=>{await loadBase();await loadTasks();pollTimer=window.setInterval(()=>{if(tasks.value.some(isRunning))loadTasks(true)},2000)});
onBeforeUnmount(()=>{if(pollTimer)window.clearInterval(pollTimer)});
</script>

<style scoped>
.patch-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:11px}.patch-actions .diff-link{margin-top:0}.fix-link{color:#16827d}.diff-notice{margin-bottom:12px}
.ocr-status-alert{margin:0 0 14px}.ocr-status-alert b{margin-right:8px}.ocr-status-alert span{line-height:1.6}.finding-detail{display:grid;grid-template-columns:52px minmax(0,1fr);gap:6px;margin-top:12px;color:#526273;line-height:1.65}.finding-detail b{color:#344054}
.ocr-fallback-note{margin-top:10px;padding:9px;border-radius:7px;background:#eef8f3;color:#376b55;font-size:11px}
.ocr-resume-note{margin-top:10px;padding:9px;border-radius:7px;background:#eef5ff;color:#315f91;font-size:11px}
.ocr-diagnostics{margin:-5px 0 14px;border:1px solid #e3eaf0;border-radius:9px;background:#fbfcfd}.ocr-diagnostic-summary{display:grid;grid-template-columns:repeat(6,1fr);gap:7px}.ocr-diagnostic-summary span{padding:8px;border-radius:7px;background:#eef4f6;color:#667085;font-size:11px}.ocr-diagnostic-summary b{display:block;margin-top:2px;color:#243746;font-size:16px}.ocr-diagnostic-summary .failed b{color:#d9485f}.ocr-failure-types{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin-top:12px;color:#445365;font-size:12px}.ocr-group-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:12px}.ocr-group-list article{padding:10px;border:1px solid #e5eaf0;border-radius:8px;background:#fff}.ocr-group-list article>div{display:flex;justify-content:space-between;gap:8px}.ocr-group-list small{display:block;margin-top:5px;color:#7b8795}.ocr-group-list p,.ocr-tool-failure p{margin:5px 0 0;color:#9a5b43;font-size:11px;line-height:1.5;overflow-wrap:anywhere}.ocr-retry-note,.ocr-tool-failure{margin-top:10px;padding:9px;border-radius:7px;background:#f6f8fa;color:#657486;font-size:11px}.ocr-tool-failure b{color:#8e4b3b}@media(max-width:900px){.ocr-diagnostic-summary{grid-template-columns:repeat(3,1fr)}.ocr-group-list{grid-template-columns:1fr}}
.analysis-page{padding:28px;min-height:100%;background:#f5f7fa;color:#1d2939}.hero{display:flex;justify-content:space-between;align-items:flex-end;padding:30px 34px;border-radius:18px;background:linear-gradient(125deg,#102a43,#176b87 62%,#1b8f8a);color:white;box-shadow:0 14px 40px rgb(16 42 67 / 18%)}.eyebrow{font-size:11px;letter-spacing:.16em;color:#9fe1dd}.hero h1{margin:8px 0 6px;font-size:28px}.hero p{margin:0;color:#d8edf0}.hero-actions{display:flex;gap:10px}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.metric{padding:18px 20px;background:white;border:1px solid #e5eaf0;border-radius:12px}.metric span{display:block;color:#718096;font-size:13px}.metric strong{display:block;margin-top:5px;font-size:26px}.metric.danger strong{color:#d9485f}.content-card{padding:22px;background:white;border:1px solid #e4e9f0;border-radius:14px}.section-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.section-head h2{margin:0;font-size:17px}.section-head p{margin:4px 0 0;color:#8792a2;font-size:12px}.task-row{display:grid;grid-template-columns:4px minmax(240px,1fr) 70px 70px 90px 90px 48px 36px;gap:12px;align-items:center;padding:15px 6px;border-top:1px solid #edf0f4;cursor:pointer}.task-row:hover{background:#f8fafc}.task-mark{height:34px;border-radius:4px;background:#2d8cf0}.task-mark.completed{background:#16a085}.task-mark.failed{background:#d9485f}.task-title{font-weight:600}.task-meta{margin-top:4px;color:#8b96a5;font-size:12px}.task-score span{display:block;color:#8b96a5;font-size:11px}.task-score b{font-size:17px}.iteration-overview{padding:20px;border-radius:14px;background:linear-gradient(135deg,#102f46,#176b6f);color:#fff;box-shadow:0 10px 24px rgb(16 47 70 / 16%)}.overview-title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.overview-title span{font-size:11px;letter-spacing:.1em;color:#9bd9d4}.overview-title h2{max-width:560px;margin:6px 0 0;font-size:20px;line-height:1.45}.overview-numbers{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:18px}.overview-numbers>div{padding:10px 12px;border-radius:9px;background:rgb(255 255 255 / 9%)}.overview-numbers b,.overview-numbers span{display:block}.overview-numbers b{font-size:20px}.overview-numbers span{margin-top:2px;color:#c5dadd;font-size:11px}.overview-numbers .danger b,.overview-numbers .deletion b{color:#ffb0a8}.overview-numbers .addition b{color:#8ee6b5}.overview-focus{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px}.overview-focus b{margin-right:3px;font-size:12px}.overview-focus span{padding:5px 9px;border-radius:999px;background:rgb(255 255 255 / 12%);font-size:11px}.overview-files{margin-top:14px;padding-top:12px;border-top:1px solid rgb(255 255 255 / 14%)}.overview-files>b{display:block;margin-bottom:7px;font-size:12px}.overview-files>div{display:flex;justify-content:space-between;gap:12px;padding:5px 0;color:#e3f0f1}.overview-files code{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.line-stat{display:flex;gap:8px;white-space:nowrap}.line-stat i{color:#8ee6b5;font-style:normal}.line-stat em{color:#ffb0a8;font-style:normal}.overview-files small{display:block;margin-top:5px;color:#a9c5c8}.input-collapse{margin:10px 0 4px;background:transparent}.analysis-context{padding:14px;border:1px solid #dbe7ee;border-radius:10px;background:#f8fafc}.context-head,.report-toolbar,.finding-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.context-head h3,.report-toolbar h3{margin:3px 0 0}.context-kicker{font-size:10px;letter-spacing:.14em;color:#16827d}.context-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-top:16px}.context-grid span{display:block;color:#8591a2;font-size:11px}.context-grid b{display:block;margin-top:3px;font-size:13px}.context-grid .wide{grid-column:1/-1}.help-icon{margin-left:3px;color:#758397}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.report-tabs{margin-top:4px}.report-toolbar{align-items:center;margin:8px 0 14px}.report-toolbar p{margin:4px 0 0;color:#8893a2;font-size:12px}.report-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}.report-summary span{padding:10px 12px;border-radius:8px;background:#f3f6f8;color:#718096;font-size:11px}.report-summary b{display:block;margin-top:2px;color:#223143;font-size:18px}.report-summary .risk-number b{color:#d9485f}.finding,.test-point{margin-top:12px;padding:15px;border:1px solid #e7ebf0;border-radius:10px;background:#fff}.finding strong,.test-point strong{margin-left:8px}.finding p{color:#667085}.finding code{display:block;padding:10px;overflow:auto;background:#f7f8fa;border-radius:6px}.confidence{white-space:nowrap;color:#8792a2;font-size:11px}.impact,.expected{margin-top:10px;color:#526273}.source-key{margin-top:9px;color:#98a2b3;font-size:11px}.report-section{margin-top:18px;padding:16px;border:1px solid #e7ebf0;border-radius:10px}.report-section h3{margin:0 0 12px;font-size:14px}.chip-list{display:flex;flex-wrap:wrap;gap:8px}.chip-list span{padding:6px 10px;border-radius:999px;background:#eaf5f4;color:#176d69;font-size:12px}.file-list{display:flex;flex-direction:column;gap:8px}.file-list>div{display:flex;align-items:center;gap:8px}.file-list code{overflow:hidden;text-overflow:ellipsis;color:#526273}.two-column-sections{display:grid;grid-template-columns:1fr 1fr;gap:12px}.report-section ul{margin:0;padding-left:20px;color:#526273}.report-section li+li{margin-top:7px}.report-section.gap{border-color:#f0dfca;background:#fffaf3}.mode-description{font-size:12px;color:#8b96a5}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.task-row{grid-template-columns:4px 1fr 80px}.task-score,.task-row :deep(.arco-progress){display:none}.context-grid,.two-column-sections{grid-template-columns:1fr}}
.quality-breakdown{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin:-4px 0 14px;color:#667085;font-size:12px}.quality-breakdown>b{margin-right:3px;color:#344054}.quality-breakdown span{padding:4px 8px;border-radius:999px;background:#edf7f6;color:#166b66}.mode-help{margin-left:4px;vertical-align:-2px;color:#718096;cursor:help}.mode-tooltip{width:290px;padding:3px 2px}.mode-tooltip>div{display:grid;grid-template-columns:38px 1fr;gap:8px;padding:7px 4px;border-bottom:1px solid rgb(255 255 255 / 12%);line-height:1.45}.mode-tooltip>div:last-child{border-bottom:0}.mode-tooltip b{color:#9fe1dd}.mode-tooltip span{color:#e7edf3;font-size:12px}.finding-filter{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:14px 0;padding:10px 12px;border:1px solid #dce8ef;border-radius:9px;background:#f8fbfc}.finding-filter>span{color:#758397;font-size:12px}.risk-type{margin-left:7px}.code-snippet{overflow:hidden;border:1px solid #e8edf1;border-radius:8px;background:#f6f8fa}.snippet-caption{display:flex;justify-content:space-between;padding:7px 11px;border-bottom:1px solid #e6ebef;background:#edf2f5;color:#5f6f7f;font-size:11px}.snippet-caption small{color:#8b98a6}.snippet-line{display:grid;grid-template-columns:42px minmax(0,1fr);min-height:25px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.55}.snippet-line>span{padding:3px 9px 3px 4px;border-right:1px solid #e4e9ee;background:#f0f3f5;color:#98a4af;text-align:right;user-select:none}.snippet-line code{padding:3px 12px;white-space:pre-wrap;overflow-wrap:anywhere;color:#26333f;background:transparent}
.impact-module-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:14px}.impact-module-grid>div{min-height:68px;padding:10px;border:1px solid rgb(255 255 255 / 17%);border-radius:8px;background:rgb(1 18 29 / 16%)}.impact-module-grid b,.impact-module-grid span,.impact-module-grid small{display:block}.impact-module-grid b{font-size:13px}.impact-module-grid span{margin-top:3px;color:#9fe1dd;font-size:11px}.impact-module-grid small{margin-top:5px;color:#c2d7d9;font-size:11px;line-height:1.35}.diff-link{display:inline-flex;align-items:center;gap:5px;margin-top:11px;font-size:12px}.diff-view{overflow:auto;max-height:66vh;border:1px solid #dce4ea;border-radius:9px;background:#fbfcfd}.diff-line{display:grid;grid-template-columns:54px minmax(0,1fr);min-height:25px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.58}.diff-line>span{padding:3px 10px 3px 4px;border-right:1px solid #e3e8ed;color:#98a5b2;background:#f4f6f8;text-align:right;user-select:none}.diff-line code{padding:3px 12px;white-space:pre-wrap;overflow-wrap:anywhere;background:transparent;color:#344054}.diff-line.added{background:#ecfdf3}.diff-line.added code{color:#12633f}.diff-line.removed{background:#fff1f1}.diff-line.removed code{color:#a12834}.diff-line.hunk{background:#eef6ff}.diff-line.hunk code{color:#2865a8}
.execution-log{margin-top:16px;padding-top:13px;border-top:1px solid #dce6ec}.execution-log-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}.execution-log-head small{color:#8591a2;font-size:11px}.execution-log-item{display:grid;grid-template-columns:10px 132px 76px minmax(180px,1fr) 104px;column-gap:12px;align-items:start;padding:6px 0;color:#556473;font-size:12px;line-height:1.55}.execution-log-item time{color:#8591a2;font-variant-numeric:tabular-nums;white-space:nowrap}.execution-log-item b{color:#334155;font-weight:600;white-space:nowrap}.execution-log-item>span:not(.execution-dot){min-width:0;overflow-wrap:anywhere}.execution-log-item em{color:#16827d;font-size:11px;font-style:normal;white-space:nowrap}.execution-dot{width:7px;height:7px;margin-top:6px;border-radius:50%;background:#8fa2b2}.execution-dot.completed{background:#1fa77a}.execution-dot.partial{background:#f59e0b}.execution-dot.failed{background:#e15361}.execution-dot.cancelled{background:#8793a0}.execution-dot.started,.execution-dot.queued,.execution-dot.stage_finished{background:#2488cc}@media(max-width:900px){.execution-log-item{grid-template-columns:10px 118px 68px minmax(0,1fr);column-gap:9px}.execution-log-item em{grid-column:4;margin-top:2px}}
.draft-actions{display:flex;align-items:center;gap:3px}.convert-description{margin:0 0 14px;color:#667085;font-size:13px;line-height:1.6}
.point-section-title{margin:22px 0 8px;color:#344054;font-size:15px}.risk-test-point{border-color:#f2dec5;background:#fffdfa}
.iteration-summary{background:linear-gradient(135deg,#f7fbfc,#fff)}.iteration-summary>strong{display:block;font-size:16px;color:#1f3f4d;line-height:1.5}.iteration-summary-points{display:flex;flex-direction:column;gap:12px;margin-top:14px}.iteration-summary-point{display:grid;grid-template-columns:26px minmax(0,1fr);gap:4px;color:#5d6b78}.summary-index{color:#176b87;font-weight:700;line-height:1.55}.iteration-summary-point b{display:block;color:#294b5a;font-size:14px;line-height:1.55}.iteration-summary-point p{margin:3px 0 0;color:#5d6b78;line-height:1.65}.iteration-group-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}.iteration-group-card{padding:12px;border:1px solid #dce9ea;border-radius:9px;background:#fff}.iteration-group-card>div:first-child{display:flex;align-items:center;justify-content:space-between;gap:8px}.iteration-group-card>div:first-child b{color:#234858;font-size:13px}.group-module{display:inline-block;margin-top:6px;color:#16827d;font-size:11px}.iteration-group-card p{min-height:38px;margin:8px 0 5px;color:#526273;font-size:12px;line-height:1.55}.iteration-group-card small{display:block;color:#778899;font-size:11px;line-height:1.5}.group-files{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}.group-files code{max-width:100%;padding:3px 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-radius:4px;background:#edf4f5;color:#477180;font-size:10px}@media(max-width:900px){.iteration-group-grid{grid-template-columns:1fr}}
</style>
