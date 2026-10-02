<template>
  <div class="qe-page">
    <div v-if="!projectStore.currentProjectId" class="empty-page">
      <a-empty description="请先选择项目，再进入质量飞轮" />
    </div>
    <template v-else>
      <header class="hero">
        <div class="hero-copy"><h1>{{ pageHeader.title }}</h1><p>{{ pageHeader.description }}</p></div>
        <div class="hero-actions"><a-tag color="green">人工门禁开启</a-tag><a-tag><icon-storage /> {{ projectName }}</a-tag><a-button @click="refreshAll"><template #icon><icon-refresh /></template>刷新数据</a-button></div>
      </header>

      <nav v-if="primaryView==='data'" class="quick-tabs" aria-label="数据飞轮功能">
        <button type="button" :class="{active:quickMode==='console'}" @click="quickMode='console'">控制台</button>
        <button type="button" :class="{active:quickMode==='skills'}" @click="quickMode='skills'">Skill Hub</button>
      </nav>

      <div class="workspace-shell" :class="{'graph-layout':primaryView==='graph'}">

      <main class="workspace-content">
        <div v-if="primaryView==='data' && quickMode==='console' && workspace!=='overview'" class="console-context">
          <button type="button" @click="workspace='overview'"><icon-right/>返回控制台</button>
          <span>{{ tabs.find(item=>item.key===workspace)?.label }}</span>
        </div>
        <template v-if="primaryView === 'agents'">
          <section class="board-kpis">
            <article v-for="kpi in agentKpis" :key="kpi.key" :class="kpi.tone">
              <span>{{ kpi.label }}</span>
              <div class="kpi-value"><b>{{ kpi.value }}</b><em v-if="kpi.delta!==null" :class="kpi.invert?(kpi.delta>=0?'down':'up'):(kpi.delta>=0?'up':'down')">{{ kpi.delta>=0?'+':'' }}{{ kpi.delta }}%</em></div>
              <small>{{ kpi.hint }}</small>
              <div class="kpi-spark" aria-hidden="true"><i v-for="(height,index) in kpi.spark" :key="index" :style="{height:`${height}%`}"/></div>
            </article>
          </section>
          <section class="panel board-chart">
            <div class="section-head"><div><span>调用趋势</span><h2>会话数与 Token 消耗</h2></div><small>近 14 天 · 共 {{ allTraces.length }} 次会话</small></div>
            <div v-if="hasTrendData" class="chart-body">
              <div class="chart-legend"><span><i class="dot session"/>会话数</span><span><i class="dot token"/>Token 消耗</span></div>
              <div class="chart-bars">
                <div v-for="point in agentTrend" :key="point.date" class="bar-col">
                  <div class="bar-stack">
                    <i class="bar session" :style="{height:`${point.sessionPct}%`}" :title="`${point.date} · ${point.sessions} 次会话`"/>
                    <i class="bar token" :style="{height:`${point.tokenPct}%`}" :title="`${point.date} · ${point.tokens} tokens`"/>
                  </div>
                  <small>{{ point.date }}</small>
                </div>
              </div>
            </div>
            <a-empty v-else description="近 14 天暂无会话数据"/>
          </section>

          <section class="panel board-table">
            <div class="section-head"><div><span>Agent 台账</span><h2>按能力聚合的运行指标</h2></div><small>{{ agentRows.length }} 个 Agent · 不含代码审查与知识库问答</small></div>
            <div class="agent-head"><span>Agent</span><span>会话数</span><span>用户数</span><span>Token 用量</span><span>平均耗时</span><span>失败率</span></div>
            <div class="agent-rows">
              <article v-for="row in agentRows" :key="row.stage" :class="{ idle: !row.sessions }">
                <div class="agent-name"><b>{{ row.label }}</b><small>{{ row.stage }}</small></div>
                <span class="cell"><b>{{ row.sessions }}</b><i class="meter"><u :style="{width:`${row.sessionPct}%`}"/></i></span>
                <span class="cell"><b>{{ row.users }}</b><i class="meter"><u :style="{width:`${row.userPct}%`}"/></i></span>
                <span class="cell"><b>{{ row.tokens.toLocaleString() }}</b><i class="meter"><u :style="{width:`${row.tokenPct}%`}"/></i></span>
                <span class="cell"><b>{{ row.latencyText }}</b><i class="meter"><u :style="{width:`${row.latencyPct}%`}"/></i></span>
                <span class="cell"><a-tag :color="row.failed?'red':'green'" size="small">{{ row.failedRate }}%</a-tag></span>
              </article>
            </div>
          </section>
          <div class="overview-grid">
            <section class="panel padded">
              <div class="section-head"><div><span>当前待办</span><h2>需要人工处理的事项</h2></div></div>
              <div class="task-list">
                <button type="button" @click="openData('gold')" ><i class="blue"><icon-message/></i><div><b>建设金标资产</b><small>复核 Badcase、误报、漏报与业务结果</small></div><strong>{{ pendingFeedback }}</strong><icon-right/></button>
                <button type="button" @click="openData('evaluation')"><i class="orange"><icon-experiment/></i><div><b>分析失败样本</b><small>定位低分层级和能力缺口</small></div><strong>{{ failedResults.length }}</strong><icon-right/></button>
                <button type="button" @click="openData('attribution')"><i class="teal"><icon-branch/></i><div><b>检查运行轨迹</b><small>查看来源、耗时、Token 与失败状态</small></div><strong>{{ failedTraces.length }}</strong><icon-right/></button>
                <button type="button" @click="openData('optimization')"><i class="teal"><icon-safe/></i><div><b>审批优化候选</b><small>验证 Prompt、知识、检索与工具改进</small></div><strong>{{ pendingOptimizationCount }}</strong><icon-right/></button>
              </div>
            </section>
          </div>
        </template>

        <section v-else-if="primaryView === 'graph'" class="knowledge-graph-embed">
          <KnowledgeGraphView />
        </section>

        <section v-else-if="quickMode === 'skills'" class="skill-hub-embed">
          <SkillHubConsole :project-id="projectStore.currentProjectId!" :key="`quality-skill-hub-${projectStore.currentProjectId}`" />
        </section>

        <section v-else-if="workspace === 'overview'" class="panel launch-console">
          <div class="launch-heading">
            <div><span>QUICK START</span><h2>选择一项工作</h2><p>进入对应的数据、评测或治理流程。</p></div>
            <a-tag color="arcoblue">{{ projectName }}</a-tag>
          </div>
          <div class="launch-grid">
            <button type="button" @click="workspace='single'"><i><icon-experiment/></i><div><b>独立能力评测</b><small>用例审查的单次质量检查</small></div><em>约 5 分钟</em><icon-right/></button>
            <button type="button" @click="workspace='workflow'"><i><icon-branch/></i><div><b>全链路测试</b><small>测试方案 → 测试用例 → 测试执行 → 报告生成</small></div><em>{{ cockpit.workflows.length }} 条流程</em><icon-right/></button>
            <button type="button" @click="workspace='gold'"><i><icon-message/></i><div><b>建设评测数据</b><small>整理反馈、Badcase 与金标数据</small></div><em>{{ pendingFeedback }} 条待复核</em><icon-right/></button>
            <button type="button" @click="workspace='evaluation'"><i><icon-dashboard/></i><div><b>运行自动评测</b><small>选择数据集，执行分层质量扫描</small></div><em>{{ runs.length }} 次运行</em><icon-right/></button>
            <button type="button" @click="workspace='attribution'"><i><icon-branch/></i><div><b>分析失败轨迹</b><small>识别意图、规划、检索、模型或工具调用问题</small></div><em>{{ failedTraces.length }} 条待分析</em><icon-right/></button>
            <button type="button" @click="workspace='optimization'"><i><icon-safe/></i><div><b>优化与发布</b><small>影子验证、人工审批、灰度发布与安全回滚</small></div><em>{{ pendingOptimizationCount }} 项待处理</em><icon-right/></button>
          </div>
        </section>

        <section v-else-if="workspace === 'single'" class="panel content-panel">
          <div class="section-head toolbar"><div><span>单次评测</span><h2>独立能力质量面板</h2><p>用例审查是当前唯一的单次能力 Agent；代码审查与知识库问答属平台基础能力，只做质量观测、不进自进化。</p></div></div>
          <div class="single-grid"><article v-for="item in cockpit.single_capabilities" :key="item.stage"><header><div><span>{{ capabilityCode(item.stage) }}</span><h3>{{ taskTypeLabels[item.stage] || item.stage }}</h3></div><a-tag :color="item.failed ? 'red' : item.outputs ? 'green' : 'gray'">{{ item.failed ? '存在失败' : item.outputs ? '正常' : '暂无产出' }}</a-tag></header><div class="single-stats"><span><b>{{ item.outputs }}</b><small>产出</small></span><span><b>{{ item.feedback }}</b><small>反馈</small></span><span><b>{{ item.failed }}</b><small>失败</small></span></div><footer>最近产出 {{ formatDate(item.latest_at || undefined) }}</footer></article></div>
        </section>

        <section v-else-if="workspace === 'workflow'" class="panel content-panel workflow-panel">
          <div class="section-head toolbar"><div><span>全链路测试</span><h2>四阶段质量门禁</h2><p>{{ workflowChainText }}。每阶段评测通过后进入下一阶段，失败样本用于改进对应 Skill。</p></div><div class="actions"><a-tag color="arcoblue">{{ cockpit.workflows.length }} 条流程</a-tag><a-button type="primary" @click="openWorkflowStart"><template #icon><icon-plus/></template>发起流程</a-button></div></div>
          <div v-if="workflowStartResult" class="start-result">
            <div class="start-result-head"><div><b>已发起流程 {{ workflowStartResult.workflow_id }}</b><small>四阶段 Skill 版本已在入口一次性锁定</small></div><a-button size="mini" @click="workflowStartResult=null">收起</a-button></div>
            <div class="binding-row">
              <article v-for="stage in workflowStartResult.stage_order" :key="stage" :class="['binding',workflowStartResult.bindings[stage]?.managed?'locked':'unmanaged']">
                <b>{{ taskTypeLabels[stage] || stage }}</b>
                <template v-if="workflowStartResult.bindings[stage]?.managed"><small>{{ workflowStartResult.bindings[stage].skill_name }} · {{ workflowStartResult.bindings[stage].version }}</small><small class="sha">sha {{ (workflowStartResult.bindings[stage].package_sha256||'').slice(0,12) }}</small></template>
                <template v-else><small>未锁定 · 无版本溯源</small><small class="sha">{{ workflowStartResult.bindings[stage]?.detail || '项目未登记该阶段可用的 Skill 版本' }}</small></template>
              </article>
            </div>
            <p v-if="workflowStartResult.unmanaged_stages.length" class="warn-line">⚠ 有 {{ workflowStartResult.unmanaged_stages.length }} 个阶段没有锁定 Skill 版本，这些阶段的产出无法归属到具体版本，后续也谈不上按版本回滚。</p>
            <!-- 跨声明选用不是错误，但必须说出来：主链路刚换过阶段名，现存包声明的还是旧阶段，
                 不显示的话事后没人知道"这条链路用的是不是本来为它准备的包"。 -->
            <p v-if="workflowStartResult.mismatched_stages?.length" class="warn-line">跨声明选用 {{ workflowStartResult.mismatched_stages.length }} 个阶段（{{ workflowStartResult.mismatched_stages.map(v=>taskTypeLabels[v]||v).join('、') }}）：所选包在其 manifest 里声明的不是本阶段。已连同声明阶段写进流程锁留痕。</p>
          </div>

          <div class="wf-shell">
            <!-- 左栏：已发起的流程版本。用户发起完流程第一件事是"我在哪条流程里"，
                 所以列表必须常驻，而不是藏在某个下拉里。 -->
            <aside class="wf-flows" aria-label="已发起的流程版本">
              <div class="wf-flows-head"><b>流程版本</b><span>{{ cockpit.workflows.length }}</span></div>
              <!-- 高亮跟随 `activeFlow`（含"没点过时默认取第一条"），
                   不能只比 activeFlowId：那样首次进入会"中栏显示第一条、左栏一条都不亮"。 -->
              <button v-for="flow in cockpit.workflows" :key="flow.workflow_id" type="button" class="wf-flow" :class="{active: activeFlow?.workflow_id===flow.workflow_id}" @click="activeFlowId=flow.workflow_id">
                <div class="wf-flow-top"><b>{{ flow.workflow_id }}</b><a-tag :color="workflowColor(flow)" size="small">{{ workflowSummary(flow) }}</a-tag></div>
                <div class="wf-flow-meta"><span>{{ stageProgressText(flow) }}</span><span>锁定 {{ flow.locked_version_count ?? 0 }}/{{ flow.stages.length }}</span></div>
                <i class="wf-flow-bar"><u :style="{width:`${flowProgressPct(flow)}%`}"/></i>
                <small>{{ flowTemplateText(flow) }} · 发起 {{ formatDate(flow.created_at || undefined) }}</small>
              </button>
              <a-empty v-if="!cockpit.workflows.length" description="暂无全链路测试；点右上角「发起流程」创建第一条"/>
            </aside>

            <div class="wf-timeline">
              <template v-if="activeFlow">
                <!-- 阶段状态条：01–04 一屏看完整条链路走到哪一步 -->
                <div class="wf-stage-bar">
                  <button v-for="(step,index) in activeFlow.stages" :key="step.stage" type="button" :class="['wf-step',stageTone(activeFlow,step),{selected:activeStage(activeFlow)?.stage===step.stage}]" @click="stageSelection[activeFlow!.workflow_id]=step.stage">
                    <i>{{ String(index+1).padStart(2,'0') }}</i>
                    <b>{{ taskTypeLabels[step.stage] || step.stage }}</b>
                    <small>{{ gateText(step.status) }}</small>
                  </button>
                </div>
                <div class="wf-cards">
                  <article v-for="(step,index) in activeFlow.stages" :key="step.stage" :class="['wf-card',stageTone(activeFlow,step),{active:activeStage(activeFlow)?.stage===step.stage}]">
                    <header class="wf-card-head">
                      <i>{{ String(index+1).padStart(2,'0') }}</i>
                      <div><b>{{ taskTypeLabels[step.stage] || step.stage }}</b><small>{{ step.skill_name ? `${step.skill_name} · ${step.skill_version}` : '未锁定 Skill 版本' }}</small></div>
                      <a-tag :color="gateColor(step.status)">{{ gateText(step.status) }}</a-tag>
                    </header>
                    <div class="wf-card-body">
                      <section class="wf-side ai">
                        <h4><icon-robot/> AI 生成 / 处理</h4>
                        <p v-if="step.output_id" class="wf-line">产出 <code>{{ step.task_id }}</code></p>
                        <p v-else class="wf-line">{{ step.status==='running' ? '已派发，等待产出回写' : '本阶段尚无产出' }}</p>
                        <p class="wf-line">{{ gateEvidenceText(step) }}</p>
                      </section>
                      <section class="wf-side human">
                        <h4><icon-user/> 人工确认 / 复核</h4>
                        <p class="wf-line">{{ step.manual_score ? `人工评分 ${step.manual_score.value}/100` : '尚无人工评分' }}</p>
                        <p class="wf-line">{{ step.decided_by ? `决策人 ${step.decided_by}` : '尚无人工决策' }}</p>
                        <p class="wf-line">{{ step.human_decided ? '已由人拍板，自动评测不会推翻该结论' : '尚未拍板' }}</p>
                      </section>
                    </div>
                    <!-- 派发参数必须"常驻"在阶段卡片里，而不是只在 toast 里闪一下：
                         用户拿到参数后要去另一个页面（agent / 测试执行）把它填进去，
                         弹窗一关就找不到 module_key / workflow_id 了。 -->
                    <p v-if="planFor(activeFlow.workflow_id,step.stage)" class="dispatch-note">
                      <b>已派发</b>·通道 {{ planFor(activeFlow.workflow_id,step.stage)!.entry }}
                      <template v-if="planFor(activeFlow.workflow_id,step.stage)!.module_key"> · module_key <code>{{ planFor(activeFlow.workflow_id,step.stage)!.module_key }}</code></template>
                      · workflow_id <code>{{ activeFlow.workflow_id }}</code>
                      <template v-if="planFor(activeFlow.workflow_id,step.stage)!.parent_output_ids.length"> · 上游产出 <code>{{ planFor(activeFlow.workflow_id,step.stage)!.parent_output_ids.join('、') }}</code></template>
                      <template v-if="planFor(activeFlow.workflow_id,step.stage)!.managed"> · 已锁定 {{ planFor(activeFlow.workflow_id,step.stage)!.skill_name }} {{ planFor(activeFlow.workflow_id,step.stage)!.skill_version }}</template>
                      <br/>{{ planFor(activeFlow.workflow_id,step.stage)!.hint }}
                    </p>
                    <!-- 平台没有"打回上一阶段"这个动作，就不能摆一个按了没反应的按钮。
                         不通过时人真正该做的是重跑门禁或请负责人放行，这里把出路写清楚。 -->
                    <p v-if="step.status==='failed'" class="wf-return-note">本阶段结论为不通过。平台不含「打回上一阶段」动作：请修改产出后重跑门禁测评，或由负责人填写原因强制放行。</p>
                    <footer class="wf-card-actions">
                      <a-button v-if="step.output_id" size="small" @click="openStageOutput(activeFlow.workflow_id,step.stage)">
                        <template #icon><icon-file/></template>查看结果
                      </a-button>
                      <a-button v-if="!step.output_id" size="small" type="primary" :disabled="!['ready','running'].includes(step.status)" :loading="executeBusy===`${activeFlow.workflow_id}:${step.stage}`" @click="executeStage(activeFlow.workflow_id,step.stage)">
                        <template #icon><icon-play-arrow/></template>执行本阶段
                      </a-button>
                      <a-button v-if="step.output_id && ['pending','unscored','failed'].includes(step.status)" size="small" :loading="gateBusy===`${activeFlow.workflow_id}:${step.stage}`" @click="runGate(activeFlow.workflow_id,step.stage)">运行门禁测评</a-button>
                      <a-button v-if="step.output_id && step.scorable" size="small" @click="openStageScore(activeFlow.workflow_id,step.stage)">
                        <template #icon><icon-edit/></template>人工评分
                      </a-button>
                      <a-button v-if="step.output_id && step.confirmable" size="small" type="primary" status="success" :loading="gateBusy===`${activeFlow.workflow_id}:${step.stage}`" @click="confirmStage(activeFlow.workflow_id,step.stage)">确认进入下一阶段</a-button>
                      <a-button v-if="step.status==='failed'" size="small" status="warning" @click="openOverride(activeFlow.workflow_id,step.stage)">负责人放行</a-button>
                    </footer>
                  </article>
                </div>
              </template>
              <a-empty v-else description="左侧选中一条流程后，这里显示它的四阶段时间线"/>
            </div>
          </div>
        </section>

        <section v-else-if="workspace === 'gold'" class="panel content-panel">
          <div class="section-head toolbar"><div><span>第一环 · 金标资产</span><h2>真实业务 Badcase 与人工判断</h2><p>将采纳、误报、漏报和缺陷确认沉淀为评测资产。</p></div><div class="actions"><a-select v-model="feedbackSignal" allow-clear placeholder="全部信号" style="width:160px" @change="loadFeedback"><a-option value="">全部信号</a-option><a-option v-for="(label,key) in signalLabels" :key="key" :value="key">{{ label }}</a-option></a-select><a-button type="primary" @click="showSuiteModal=true"><template #icon><icon-plus/></template>新建评测集</a-button></div></div>
          <div class="gold-type-nav"><button type="button" :class="{active:!goldTaskType}" @click="goldTaskType=''">全部 <b>{{ goldDatasets.length }}</b></button><button v-for="(label,key) in taskTypeLabels" :key="key" type="button" :class="{active:goldTaskType===key}" @click="goldTaskType=key">{{ label }} <b>{{ (cockpit.gold_by_type[key] || []).length }}</b></button></div>
          <div class="asset-strip"><article v-for="dataset in filteredGoldDatasets" :key="dataset.id"><div><b>{{ dataset.name }}</b><small>{{ taskTypeLabels[dataset.task_type] || dataset.task_type }} · {{ dataset.version_count || 0 }} 个版本</small></div><a-tag :color="dataset.status==='active'?'green':'gray'">{{ dataset.status==='active'?'启用':'已归档' }}</a-tag></article><a-empty v-if="!filteredGoldDatasets.length" description="当前类型暂无金标集，可从下方反馈中筛选 Badcase" /></div>
          <h3 class="subheading">待沉淀的真实反馈</h3><div class="table-head"><span>信号</span><span>反馈内容</span><span>操作人</span><span>发生时间</span></div>
          <div v-if="feedbackEvents.length" class="rows"><article v-for="item in feedbackEvents" :key="item.id"><a-tag :color="signalColor(item.signal)">{{ signalText(item.signal) }}</a-tag><div><b>{{ feedbackSummary(item) }}</b><small>{{ item.reason_code || '未填写原因编码' }}</small></div><span>{{ feedbackActor(item) }}</span><time>{{ formatDate(item.created_at) }}</time></article></div>
          <a-empty v-else description="暂无反馈信号"/>
        </section>

        <div v-else-if="workspace === 'evaluation'" class="split-view">
          <aside class="panel suite-panel"><div class="section-head"><div><span>评测资产</span><h2>评测集</h2></div><a-button type="primary" size="small" @click="showSuiteModal=true"><template #icon><icon-plus/></template>新建</a-button></div><button v-for="suite in suites" :key="suite.id" type="button" class="suite" :class="{active:selectedSuiteId===suite.id}" @click="selectSuite(suite)"><div><b>{{ suite.name }}</b><small>{{ suiteTypeLabels[suite.suite_type] }} · {{ suite.case_count }} 个样本</small></div><icon-right/></button><a-empty v-if="!suites.length&&!suitesLoading" description="暂无评测集"/></aside>
          <section class="panel eval-panel"><div class="section-head toolbar"><div><span>运行与结果</span><h2>{{ selectedSuite?.name || '请选择评测集' }}</h2></div><a-button type="primary" :disabled="!selectedSuiteId" @click="createRun"><template #icon><icon-play-arrow/></template>运行评测</a-button></div>
            <div class="run-list"><button v-for="run in runs" :key="run.id" type="button" :class="{active:selectedRunId===run.id}" @click="selectRun(run)"><div><b>{{ run.name||'评测运行' }}</b><small>{{ formatDate(run.created_at) }}</small></div><span><i>一级 {{ fmt(run.l0_score) }}</i><i>二级 {{ fmt(run.l1_score) }}</i><i>三级 {{ fmt(run.l2_score) }}</i><i>四级 {{ fmt(run.l3_score) }}</i></span><a-tag :color="statusColor(run.status)">{{ statusText(run.status) }}</a-tag></button></div>
            <div v-if="selectedRun" class="result-summary"><div><span>样本总数</span><b>{{ results.length }}</b></div><div><span>失败样本</span><b>{{ failedResults.length }}</b></div><div><span>失败率</span><b>{{ failureRate }}%</b></div><div><span>Token</span><b>{{ totalTokens }}</b></div></div>
            <a-empty v-if="!runs.length" description="选择评测集后发起首次运行"/>
            <div v-if="failedResults.length" class="failures"><h3>失败样本</h3><article v-for="item in failedResults.slice(0,8)" :key="item.id"><a-tag color="red">#{{ item.case_number }}</a-tag><span>{{ failureSummary(item) }}</span><small>{{ item.split }}</small></article></div>
          </section>
        </div>

        <section v-else-if="workspace === 'attribution'" class="panel content-panel">
          <div class="section-head toolbar"><div><span>第三环 · 轨迹回流归因</span><h2>跨业务运行轨迹</h2><p>展开意图、检索、模型、工具和校验节点，结合规则、模型假设与反证完成归因。</p></div><div class="actions"><a-select v-model="traceTaskType" allow-clear placeholder="全部业务" style="width:160px"><a-option value="">全部业务</a-option><a-option v-for="(label,key) in taskTypeLabels" :key="key" :value="key">{{ label }}</a-option></a-select><a-select v-model="traceStatus" allow-clear placeholder="全部状态" style="width:130px"><a-option value="">全部状态</a-option><a-option value="completed">已完成</a-option><a-option value="failed">失败</a-option><a-option value="untraceable">不可回流</a-option></a-select></div></div>
          <div class="trace-head"><span>业务阶段</span><span>任务/查询</span><span>检索通道</span><span>耗时</span><span>Token</span><span>状态</span></div>
          <div v-if="traces.length" class="trace-rows"><article v-for="item in traces" :key="item.id" :class="{selected:selectedTraceId===item.id}" @click="selectTrace(item)"><a-tag color="arcoblue">{{ taskTypeLabels[item.task_type] || item.task_type }}</a-tag><div><b>{{ item.query || item.task_id || '未记录查询' }}</b><small>{{ item.task_id || item.policy_version || '-' }}</small></div><span>{{ traceChannels(item) }}</span><span>{{ traceLatency(item) }}</span><strong>{{ item.token_usage || 0 }}</strong><a-tag :color="traceStatusColor(item.status)">{{ traceStatusText(item.status) }}</a-tag></article></div>
          <a-empty v-else description="当前项目暂无运行轨迹"/>
          <div v-if="selectedTraceId" class="trace-detail"><section><h3>执行节点</h3><div class="span-line"><article v-for="span in selectedSpans" :key="span.id"><i :class="span.status"/><div><b>{{ stepTypeText(span.step_type) }}<template v-if="span.tool_name"> · {{ span.tool_name }}</template></b><small>#{{ span.sequence }} · {{ span.latency_ms || 0 }}ms · {{ span.token_usage || 0 }} Token</small></div><a-tag :color="statusColor(span.status)">{{ statusText(span.status) }}</a-tag></article></div><a-empty v-if="!selectedSpans.length" description="该轨迹暂无节点数据"/></section><aside><h3>归因假设</h3><article v-for="item in traceAttributions" :key="item.id"><header><a-tag :color="item.source==='rule'?'green':'blue'">{{ item.source==='rule'?'确定性':'模型辅助' }}</a-tag><a-tag :color="item.state==='confirmed'?'green':item.state==='rejected'?'red':'orange'">{{ attributionStateText(item.state) }}</a-tag></header><b>{{ attributionCategoryText(item.category) }}</b><p>{{ item.hypothesis }}</p><small>置信度 {{ Math.round(item.confidence*100) }}% · 反证 {{ item.counterevidence?.length || 0 }} 条</small></article><a-empty v-if="!traceAttributions.length" description="尚未形成归因假设"/></aside></div>
        </section>

        <section v-else class="panel content-panel">
          <div class="section-head toolbar"><div><span>第四环 · 优化验证</span><h2>受控自进化与发布治理</h2><p>从归因生成 Prompt、知识、检索或工具候选，通过影子对比、人工审批和灰度观察后生效。</p></div><div class="actions"><a-select v-model="candidateState" allow-clear placeholder="全部状态" style="width:140px" @change="loadCandidates"><a-option value="">全部状态</a-option><a-option value="pending">待处理</a-option><a-option value="awaiting_approval">待审批</a-option><a-option value="accepted">已通过</a-option><a-option value="rejected">已驳回</a-option></a-select><a-button type="primary" :disabled="!selectedRunId||!failedResults.length" @click="showCandidateModal=true"><template #icon><icon-plus/></template>从失败样本生成</a-button></div></div>
          <div class="governance-board"><section><div class="mini-head"><b>优化提案</b><span>{{ proposals.length }}</span></div><article v-for="item in proposals" :key="item.id"><div><a-tag color="arcoblue">{{ proposalTypeText(item.proposal_type) }}</a-tag><a-tag :color="releaseStateColor(item.state)">{{ releaseStateText(item.state) }}</a-tag></div><b>{{ item.title }}</b><p>{{ item.summary || item.expected_benefit || '待补充收益说明' }}</p><small>回滚：{{ item.rollback_plan || '保留当前活跃版本' }}</small></article><a-empty v-if="!proposals.length" description="暂无归因生成的优化提案"/></section><section><div class="mini-head"><b>发布单元</b><span>{{ releases.length }}</span></div><article v-for="item in releases" :key="item.id"><div><a-tag color="blue">{{ item.kind }}</a-tag><a-tag :color="releaseStateColor(item.state)">{{ releaseStateText(item.state) }}</a-tag></div><b>{{ item.name }} · {{ item.version }}</b><p>{{ gateSummary(item) }}</p><small>{{ formatDate(item.created_at) }}</small></article><a-empty v-if="!releases.length" description="暂无待影子评测或灰度的发布单元"/></section></div>
          <h3 class="subheading">知识沉淀候选</h3>
          <div class="candidate-grid"><article v-for="item in candidates" :key="item.id"><header><a-tag color="arcoblue">{{ item.kind }}</a-tag><a-tag :color="candidateColor(item.state)">{{ candidateStateText(item.state) }}</a-tag></header><h3>{{ candidateTitle(item) }}</h3><p>来源 {{ item.origin }} · 置信度 {{ Math.round(item.confidence*100) }}%</p><footer><span>{{ formatDate(item.created_at) }}</span><div><a-button size="small" status="danger" :disabled="!reviewable(item)" @click="reject(item)">驳回</a-button><a-button size="small" type="primary" :disabled="!reviewable(item)" @click="approve(item)">通过</a-button></div></footer></article></div><a-empty v-if="!candidates.length" description="暂无改进候选"/>
          <div class="roadmap"><icon-safe/><div><b>受控自进化公共能力</b><small>候选版本 → 同集影子对比 → 硬门禁 → 测试负责人发布 → 可回滚</small></div><a-tag color="green">后端能力已就绪</a-tag></div>
        </section>
      </main>
        <aside v-if="primaryView!=='graph'" class="team-panel panel" aria-label="项目测试团队">
          <header class="team-head"><div><i></i><span>测试团队</span></div><button type="button" @click="loadCockpit"><icon-refresh />刷新</button></header>
          <p class="team-note">负责质量门禁决策、任务执行与结果反馈</p>
          <div class="people-group lead-group">
            <div class="group-title"><b>测试负责人</b><span>{{ cockpit.people.leads.length }} 人</span></div>
            <article v-for="person in cockpit.people.leads" :key="`lead-${person.id}`" class="person-card">
              <div class="person-main"><i>{{ personInitial(person) }}</i><div><b>{{ person.display_name }}</b><small>{{ person.username }}</small></div><em>负责人</em></div>
              <footer><span>质量审批与门禁放行</span><small><i></i>已就绪</small></footer>
            </article>
            <a-empty v-if="!cockpit.people.leads.length" description="未配置测试负责人"/>
          </div>
          <div class="people-group executor-group">
            <div class="group-title"><b>测试执行人员</b><span>{{ cockpit.people.executors.length }} 人</span></div>
            <article v-for="person in cockpit.people.executors" :key="`executor-${person.id}`" class="person-card">
              <div class="person-main"><i>{{ personInitial(person) }}</i><div><b>{{ person.display_name }}</b><small>{{ person.username }}</small></div><em>执行人</em></div>
              <footer><span>任务执行与结果反馈</span><small><i></i>已就绪</small></footer>
            </article>
            <a-empty v-if="!cockpit.people.executors.length" description="未配置测试执行人员"/>
          </div>
          <!-- AI 专家团队：与"测试团队"并列，说明这条链路每个阶段由哪个 Skill 在干、
               用的是哪一版。人和 AI 各自负责什么，在这一屏里就能对上，不用来回切页。 -->
          <div v-if="workspace==='workflow'&&activeFlow" class="people-group expert-group">
            <div class="group-title"><b>AI 专家团队</b><span>{{ activeFlow.locked_version_count ?? 0 }}/{{ activeFlow.stages.length }} 已锁定</span></div>
            <article v-for="step in activeFlow.stages" :key="`expert-${step.stage}`" class="person-card">
              <div class="person-main"><i>{{ (taskTypeLabels[step.stage] || step.stage).slice(0,1) }}</i><div><b>{{ taskTypeLabels[step.stage] || step.stage }}</b><small>{{ step.skill_name || '未锁定 Skill 版本' }}<template v-if="step.skill_version"> · {{ step.skill_version }}</template></small></div><em>AI</em></div>
              <footer><span>产出生成与自评</span><small :class="['expert-state',step.passed?'ok':step.status==='failed'?'bad':'']">{{ gateText(step.status) }}</small></footer>
            </article>
          </div>
        </aside>
      </div>
    </template>

    <a-modal v-model:visible="showSuiteModal" title="新建评测集" ok-text="创建" @ok="confirmCreateSuite"><a-form :model="suiteForm" layout="vertical"><a-form-item label="名称" required><a-input v-model="suiteForm.name" placeholder="例如：代码审查回归集"/></a-form-item><a-form-item label="评测集类型"><a-select v-model="suiteForm.suite_type"><a-option v-for="(label,key) in suiteTypeLabels" :key="key" :value="key">{{ label }}</a-option></a-select></a-form-item><a-form-item label="任务类型"><a-select v-model="suiteForm.task_type"><a-option v-for="(label,key) in taskTypeLabels" :key="key" :value="key">{{ label }}</a-option></a-select></a-form-item><a-form-item label="描述"><a-textarea v-model="suiteForm.description"/></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="showCandidateModal" title="从失败样本生成改进候选" ok-text="生成候选" @ok="confirmCreateCandidates"><a-form :model="candidateForm" layout="vertical"><a-form-item label="失败阈值"><a-slider v-model="candidateForm.threshold" :min="0" :max="1" :step="0.05"/></a-form-item><a-form-item label="最小失败样本数"><a-input-number v-model="candidateForm.minFailureCount" :min="1" :max="100"/></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="showGateOverrideModal" title="负责人强制放行" ok-text="确认放行" @ok="confirmGateOverride"><a-alert type="warning">放行会允许进入下一阶段，操作人和原因将被永久记录。</a-alert><a-form layout="vertical" style="margin-top:16px"><a-form-item label="放行原因" required><a-textarea v-model="gateOverride.reason" :max-length="500" show-word-limit placeholder="请说明风险、业务依据与后续补救措施"/></a-form-item></a-form></a-modal>
    <a-modal
      v-model:visible="showWorkflowStartModal"
      :title="workflowStep===1?'发起全链路测试 · 1/2 选择 Skill 包':'发起全链路测试 · 2/2 确认流程标识'"
      :ok-text="workflowStep===1?'下一步：填写流程标识':'发起并锁定版本'"
      :ok-loading="workflowStarting"
      :ok-button-props="{disabled: workflowStep===1 && !pinsComplete}"
      :on-before-ok="onWorkflowModalOk"
      :mask-closable="false"
      width="760px"
    >
      <a-alert type="info">发起时会把四个阶段的 Skill 版本<strong>一次性</strong>锁定。链路跑起来之后再有人激活新版本，也不会改变本次流程已锁定的版本——否则「这条链路的产出对应哪个版本」就无法回答，回滚也界定不了影响范围。</a-alert>

      <!-- 第一步就是"逐阶段选包"：发起这个动作的实质就是选版本。
           把它放在第二步之后等于让人先承诺再挑，顺序反了；
           而且没选完就不给「下一步」——否则"没锁上版本"会一路拖到最后才暴露。 -->
      <template v-if="workflowStep===1">
        <a-spin :loading="catalogLoading" style="width:100%">
          <div class="pin-grid">
            <section v-for="item in catalog.stages" :key="item.stage" class="pin-block">
              <header>
                <b>{{ item.label }}</b>
                <small v-if="pinnedSkill(item.stage)">{{ pinnedSkill(item.stage)!.skill_name }} · {{ pinnedSkill(item.stage)!.version }}</small>
                <small v-else class="missing">未选定</small>
              </header>
              <a-input v-model="pinSearch[item.stage]" size="small" allow-clear placeholder="搜索 Skill 包名或说明"/>
              <div class="pin-list">
                <button v-for="skill in pinCandidates(item.stage)" :key="skill.skill_id" type="button" :class="['pin-item',{selected:pins[item.stage]===skill.skill_id,blocked:!skill.runnable}]" @click="pins[item.stage]=skill.skill_id">
                  <div>
                    <b>{{ skill.skill_name }}</b>
                    <a-tag v-if="skill.declared_stage && skill.declared_stage!==item.stage" size="small" color="orange">声明 {{ skill.declared_stage_label }}</a-tag>
                    <a-tag v-if="!skill.runnable" size="small" color="gray">不可运行</a-tag>
                  </div>
                  <small>{{ skill.version || '无活跃版本' }}<template v-if="skill.package_sha256"> · sha {{ skill.package_sha256.slice(0,10) }}</template></small>
                </button>
                <p v-if="!pinCandidates(item.stage).length" class="pin-empty">没有匹配的 Skill 包</p>
              </div>
            </section>
          </div>
          <p v-if="!pinsComplete" class="pin-warn">四个阶段都要选定一个<strong>可运行</strong>的 Skill 包才能进入下一步。没有合适的包时，请先到 Skill Hub 上传并激活该阶段的版本。</p>
        </a-spin>
      </template>

      <template v-else>
        <div class="pin-summary">
          <article v-for="stage in catalog.stage_order" :key="stage">
            <b>{{ taskTypeLabels[stage] || stage }}</b>
            <small>{{ pinnedSkill(stage)?.skill_name || '—' }} · {{ pinnedSkill(stage)?.version || '—' }}</small>
          </article>
        </div>
        <a-form layout="vertical" style="margin-top:16px">
          <a-form-item label="流程标识 workflow_id" required extra="建议用可读标识，例如「交易网关-回归-20261002-01」。它会出现在流程列表、门禁留痕与链路图节点名上，UUID 不便人工核对。">
            <a-input v-model="workflowForm.workflowId" placeholder="交易网关-回归-20261002-01" allow-clear/>
          </a-form-item>
        </a-form>
      </template>
    </a-modal>
    <a-modal v-model:visible="showStageScoreModal" title="人工评分" ok-text="提交评分" :ok-loading="stageScoreBusy" @ok="confirmStageScore">
      <a-alert type="info">评分是<strong>百分制</strong>：达到阈值判通过并放行下一阶段，低于阈值判未通过。分数、评分人与备注都会被永久记录——「谁认为这一阶段合格」在事后必须能查。</a-alert>
      <a-form layout="vertical" style="margin-top:16px">
        <a-form-item :label="`评分（0–100，阈值 ${stageScore.threshold}）`">
          <a-slider v-model="stageScore.value" :min="0" :max="100" :step="1"/>
          <a-input-number v-model="stageScore.value" :min="0" :max="100" :step="1" style="width:140px;margin-top:8px"/>
          <span :class="['score-preview',stageScore.value>=stageScore.threshold?'ok':'bad']" style="margin-left:12px">{{ stageScore.value }} 分 · {{ stageScore.value>=stageScore.threshold?'判通过':'判未通过' }}</span>
        </a-form-item>
        <a-form-item label="评分说明">
          <a-textarea v-model="stageScore.reason" :max-length="500" show-word-limit placeholder="可选。例如：方案覆盖完整，但缺少回滚演练条目"/>
        </a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="showStageOutputModal" title="阶段结果" :footer="false" width="760px">
      <a-spin :loading="stageOutputLoading" style="width:100%">
        <template v-if="stageOutput">
          <div class="output-meta">
            <span><b>{{ stageOutput.stage_label }}</b> · 任务 {{ stageOutput.task_id }}</span>
            <span v-if="stageOutput.skill_name">{{ stageOutput.skill_name }}<template v-if="stageOutput.skill_version"> · {{ stageOutput.skill_version }}</template></span>
            <span v-if="stageOutput.package_sha256" class="sha">sha {{ stageOutput.package_sha256.slice(0,12) }}</span>
            <span>{{ formatDate(stageOutput.created_at) }}</span>
          </div>
          <div v-if="stageOutput.gate" class="output-gate">
            <a-tag :color="gateColor(stageOutput.gate.status)">{{ gateText(stageOutput.gate.status) }}</a-tag>
            <span>{{ stageOutput.gate.reason || '门禁暂无说明' }}</span>
            <span v-if="stageOutput.gate.decided_by">操作人 {{ stageOutput.gate.decided_by }}</span>
          </div>
          <pre class="output-content">{{ stageOutput.content || '（本阶段产出正文为空）' }}</pre>
          <p v-if="stageOutput.truncated" class="detail-note">正文共 {{ stageOutput.content_length }} 字，此处只展示前 4000 字。</p>
        </template>
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Message } from '@arco-design/web-vue';
import { IconBranch, IconDashboard, IconEdit, IconExperiment, IconFile, IconMessage, IconPlayArrow, IconPlus, IconRefresh, IconRight, IconRobot, IconSafe, IconStorage, IconUser } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { SkillHubConsole } from '@/features/skills';
import KnowledgeGraphView from '@/features/knowledge-graph/KnowledgeGraphView.vue';
import { confirmWorkflowStage, createEvaluationRun, createEvaluationSuite, evaluateWorkflowStage, executeWorkflowStage, generateCandidatesFromRun, getProjectQualityCockpit, getStageOutput, getWorkflowStageCatalog, getWorkflowStatus, listCapabilityReleases, listEvaluationResults, listEvaluationRuns, listEvaluationSuites, listExecutionSpans, listFailureAttributions, listFeedbackEvents, listGoldDatasets, listKnowledgeCandidates, listOptimizationProposals, listRetrievalTraces, overrideWorkflowStage, scoreWorkflowStage, startWorkflow, updateCandidateState } from './service';
import type { CapabilityRelease, EvaluationResult, EvaluationRun, EvaluationSuite, ExecutionSpan, FailureAttribution, FeedbackEvent, GoldDataset, KnowledgeCandidate, OptimizationProposal, ProjectQualityCockpit, ProjectQualityPerson, ProjectWorkflowView, RetrievalTrace, StageExecutionPlan, StageOutputView, StartWorkflowResult, WorkflowCatalogSkill, WorkflowStageCatalog, WorkflowStageGateView } from './types';

type Workspace='overview'|'single'|'workflow'|'gold'|'evaluation'|'attribution'|'optimization';
type PrimaryView='agents'|'data'|'graph';
type QuickMode='console'|'skills';
const projectStore=useProjectStore(); const projectName=computed(()=>projectStore.currentProject?.name||'当前项目'); const workspace=ref<Workspace>('overview');
const route=useRoute(),router=useRouter();
const routeView=():PrimaryView=>['agents','data','graph'].includes(String(route.query.view))?String(route.query.view) as PrimaryView:'agents';
const primaryView=ref<PrimaryView>(routeView()),quickMode=ref<QuickMode>('console');
const pageHeader=computed(()=>({
  agents:{title:'Agent总览',description:'查看运行、评测、失败样本与待处理改进。'},
  data:{title:'数据飞轮',description:'沉淀反馈、评测、归因与优化数据。'},
  graph:{title:'知识图谱',description:'连接代码、文档与质量经验。'},
}[primaryView.value]));
watch(()=>route.query.view,()=>{primaryView.value=routeView();workspace.value='overview'});
async function openData(target:Workspace){primaryView.value='data';quickMode.value='console';await router.replace({path:'/knowledge-evolution',query:{view:'data'}});workspace.value=target}
const suites=ref<EvaluationSuite[]>([]),runs=ref<EvaluationRun[]>([]),results=ref<EvaluationResult[]>([]),feedbackEvents=ref<FeedbackEvent[]>([]),candidates=ref<KnowledgeCandidate[]>([]),allTraces=ref<RetrievalTrace[]>([]);
const goldDatasets=ref<GoldDataset[]>([]),selectedSpans=ref<ExecutionSpan[]>([]),attributions=ref<FailureAttribution[]>([]),proposals=ref<OptimizationProposal[]>([]),releases=ref<CapabilityRelease[]>([]);
const emptyCockpit=():ProjectQualityCockpit=>({people:{leads:[],executors:[]},gold_by_type:{},single_capabilities:[],workflows:[],stage_order:[]});
const cockpit=ref<ProjectQualityCockpit>(emptyCockpit());
const suitesLoading=ref(false),selectedSuiteId=ref<string>(),selectedRunId=ref(''),selectedTraceId=ref(''),feedbackSignal=ref(''),candidateState=ref(''),traceTaskType=ref(''),traceStatus=ref(''),goldTaskType=ref(''),showSuiteModal=ref(false),showCandidateModal=ref(false),showGateOverrideModal=ref(false),gateBusy=ref('');
const gateOverride=ref({workflowId:'',stage:'',reason:''});
// ---- 全链路测试：逐阶段推进（步骤条 + 单阶段展开 + 查看结果/人工评分/人工确认）
//: 用户点开的阶段。没点过就跟随后端给的 current_stage——「当前该看哪一步」
//: 由后端算，前端只负责记住"用户临时切到哪一步看了一眼"。
const stageSelection=ref<Record<string,string>>({});
const executeBusy=ref('');
/** 已派发的执行参数，按 `workflowId:stage` 存：派发结果必须留在页面上，不能弹一下就没了。 */
const executionPlans=ref<Record<string,StageExecutionPlan>>({});
const showStageScoreModal=ref(false),stageScoreBusy=ref(false);
const stageScore=ref({workflowId:'',stage:'',value:80,reason:'',threshold:70});
const showStageOutputModal=ref(false),stageOutputLoading=ref(false),stageOutput=ref<StageOutputView|null>(null);
/** 放行状态：与后端 `GATE_PASSING_STATES` 一一对应，多一个少一个都会出现"页面说能走、接口 400"。 */
const PASSING_STATES=['passed','confirmed','overridden'];
const isPassedStatus=(v:string)=>PASSING_STATES.includes(v);
const currentStageOf=(flow:ProjectWorkflowView):WorkflowStageGateView|undefined=>flow.stages.find(v=>v.stage===flow.current_stage)||flow.stages[0];
const activeStage=(flow:ProjectWorkflowView):WorkflowStageGateView|undefined=>flow.stages.find(v=>v.stage===stageSelection.value[flow.workflow_id])||currentStageOf(flow);
/** 步骤条三档配色：done=已放行、active/failed/running=当前步、todo=未轮到的灰步。 */
const stageTone=(flow:ProjectWorkflowView,step:WorkflowStageGateView)=>{
  if(isPassedStatus(step.status))return 'done';
  if(currentStageOf(flow)?.stage!==step.stage)return 'todo';
  if(step.status==='failed')return 'failed';
  if(step.status==='running')return 'running';
  return 'active';
};
const stageProgressText=(flow:ProjectWorkflowView)=>{
  const passed=flow.stages.filter(v=>isPassedStatus(v.status)).length;
  return `${passed} / ${flow.stages.length} 个阶段已放行`;
};
// ---- 左栏：已发起流程版本列表
/** 当前选中的流程。默认取左栏第一条（后端已按发起时间倒序），用户点其它条目才切换。 */
const activeFlowId=ref('');
const activeFlow=computed<ProjectWorkflowView|undefined>(()=>{
  const flows=cockpit.value.workflows;
  return flows.find(v=>v.workflow_id===activeFlowId.value)||flows[0];
});
/** 进度条百分比按"已放行阶段数"算。刻意不用门禁通过率之类的合成分：
 *  那种分数算出来没人能解释"为什么是 62%"，而阶段计数一眼能对上。 */
const flowProgressPct=(flow:ProjectWorkflowView)=>{
  if(!flow.stages.length)return 0;
  return Math.round(flow.stages.filter(v=>isPassedStatus(v.status)).length/flow.stages.length*100);
};
/** 存量流程用的是旧阶段序列，必须标出来：不标的话用户会以为它"少了两个阶段"。 */
const flowTemplateText=(flow:ProjectWorkflowView)=>flow.stage_template==='legacy'?'历史链路四阶段':'当前链路四阶段';
/** 标题里的链路说明取自后端给的默认序列，不在前端抄一份阶段名。 */
const workflowChainText=computed(()=>{
  const order=cockpit.value.stage_order?.length?cockpit.value.stage_order:['risk_identification','testcase_generation','test_execution','issue_tracking'];
  return order.map(v=>taskTypeLabels[v]||v).join(' → ');
});
/** 门禁证据一句话：分数/评语/操作人放一行，避免页面里三处各说一段。 */
const gateEvidenceText=(step:WorkflowStageGateView)=>{
  const parts:string[]=[];
  if(step.manual_score)parts.push(`人工评分 ${step.manual_score.value}/100`);
  const auto=Object.entries(step.scores||{}).filter(([k])=>k!=='manual');
  if(auto.length)parts.push(auto.map(([k,n])=>`${k.toUpperCase()} ${Number(n).toFixed(2)}`).join(' · '));
  if(step.reason)parts.push(step.reason);
  if(step.decided_by)parts.push(`操作人 ${step.decided_by}`);
  return parts.length?parts.join(' · '):'门禁尚无结论；可运行门禁测评、人工评分，或直接确认进入下一步。';
};
const planFor=(workflowId:string,stage:string)=>executionPlans.value[`${workflowId}:${stage}`];
function openStageScore(workflowId:string,stage:string){
  const flow=cockpit.value.workflows.find(v=>v.workflow_id===workflowId);
  const step=flow?.stages.find(v=>v.stage===stage);
  // 阈值取自后端门禁，不在前端写死：两处各写一个 0.7，改一处就会对不上。
  stageScore.value={workflowId,stage,value:step?.manual_score?.value??80,reason:'',threshold:Math.round((step?.threshold??0.7)*100)};
  showStageScoreModal.value=true;
}
async function confirmStageScore(){
  if(!projectStore.currentProjectId)return;
  stageScoreBusy.value=true;
  try{
    await scoreWorkflowStage(projectStore.currentProjectId,stageScore.value.workflowId,stageScore.value.stage,stageScore.value.value,stageScore.value.reason);
    showStageScoreModal.value=false;
    await loadCockpit();
    const passed=stageScore.value.value>=stageScore.value.threshold;
    if(passed)Message.success('已提交人工评分：判通过，可进入下一步');
    else Message.warning('已提交人工评分：低于阈值，判未通过；可修改评分或由负责人强制放行');
  }catch{Message.error('提交人工评分失败')}
  finally{stageScoreBusy.value=false}
}
async function confirmStage(workflowId:string,stage:string){
  if(!projectStore.currentProjectId)return;
  gateBusy.value=`${workflowId}:${stage}`;
  try{
    await confirmWorkflowStage(projectStore.currentProjectId,workflowId,stage);
    await loadCockpit();
    Message.success('已人工确认，可进入下一阶段');
  }catch{Message.error('人工确认失败')}
  finally{gateBusy.value=''}
}
async function executeStage(workflowId:string,stage:string){
  if(!projectStore.currentProjectId)return;
  executeBusy.value=`${workflowId}:${stage}`;
  try{
    const plan=await executeWorkflowStage(projectStore.currentProjectId,workflowId,stage);
    executionPlans.value={...executionPlans.value,[`${workflowId}:${stage}`]:plan};
    // 必须说清"这不等于已经跑完"：平台只有测试执行有内部执行器，
    // 另外三个阶段的产出由 agent 提交。含糊其辞会让人一直等一个不会来的结果。
    if(plan.channel==='platform')Message.info(`已校验前置阶段并下发执行参数：请到「${plan.entry}」选用例套件执行，workflow_id 填 ${plan.workflow_id}`);
    else Message.info(`已校验前置阶段并下发执行参数：请到「${plan.entry}」以 module_key = ${plan.module_key} 执行，产出回写后本阶段自动亮起`);
    await loadCockpit();
  }catch{Message.error('执行本阶段失败：请确认上一阶段已放行')}
  finally{executeBusy.value=''}
}
async function openStageOutput(workflowId:string,stage:string){
  if(!projectStore.currentProjectId)return;
  showStageOutputModal.value=true;stageOutputLoading.value=true;stageOutput.value=null;
  try{stageOutput.value=await getStageOutput(projectStore.currentProjectId,workflowId,stage)}
  catch{Message.error('读取阶段结果失败')}
  finally{stageOutputLoading.value=false}
}
// ---- 全链路测试：发起流程（入口固定在 数据飞轮 → 控制台 → 全链路测试）
// 两步向导：① 逐阶段选 Skill 包（选完才能下一步）② 填 workflow_id 并发起。
const showWorkflowStartModal=ref(false),workflowStarting=ref(false),workflowStartResult=ref<StartWorkflowResult|null>(null);
const workflowForm=ref({workflowId:''});
const workflowStep=ref<1|2>(1),catalogLoading=ref(false);
const emptyCatalog=():WorkflowStageCatalog=>({stage_order:[],all_stage_order:[],stages:[],skills:[]});
const catalog=ref<WorkflowStageCatalog>(emptyCatalog());
/** 阶段 → 选定的 Skill ID。这是发起动作的实质内容，会被原样送进 `pins`。 */
const pins=ref<Record<string,string>>({});
/** 阶段 → 搜索关键词。逐阶段独立，避免在"风险识别"里输入的词把"问题跟踪"的候选也筛掉。 */
const pinSearch=ref<Record<string,string>>({});
const skillById=(skillId:string):WorkflowCatalogSkill|undefined=>catalog.value.skills.find(v=>v.skill_id===skillId);
const pinnedSkill=(stage:string):WorkflowCatalogSkill|undefined=>skillById(pins.value[stage]||'');
/**
 * 阶段候选：**不做声明阶段硬筛**，只把声明了本阶段的排前面。
 *
 * 硬筛会在主链路刚换阶段名时让向导一个候选都给不出来——现存包声明的还是旧阶段。
 * 「人选了它」本来就比「包里写了什么」更强（后端会把跨声明写进流程锁留痕）。
 * 不可运行的包也列出来并标注，让人看见"它在、但还不能用"，比它凭空消失好排查。
 */
function pinCandidates(stage:string):WorkflowCatalogSkill[]{
  const keyword=(pinSearch.value[stage]||'').trim().toLowerCase();
  return catalog.value.skills
    .filter(v=>!keyword||v.skill_name.toLowerCase().includes(keyword)||v.description.toLowerCase().includes(keyword))
    .slice()
    .sort((a,b)=>{
      // 排序键必须显式标成元组：推断成 (string|number)[] 后 `da-db` 会被 TS 判为非法算术。
      const rank=(item:WorkflowCatalogSkill):[number,number,string]=>[item.declared_stage===stage?0:1,item.runnable?0:1,item.skill_name];
      const [da,ra,na]=rank(a),[db,rb,nb]=rank(b);
      return da-db||ra-rb||na.localeCompare(nb);
    });
}
/** 四阶段都选到了**可运行**的包才算选完。选了个锁不上的包等于没选——必须在这里拦住。 */
const pinsComplete=computed(()=>{
  const stages=catalog.value.stage_order;
  return stages.length>0&&stages.every(stage=>pinnedSkill(stage)?.runnable===true);
});
function suggestWorkflowId(){const d=new Date();return `回归-${d.getFullYear()}${String(d.getMonth()+1).padStart(2,'0')}${String(d.getDate()).padStart(2,'0')}-01`}
async function loadStageCatalog(){
  if(!projectStore.currentProjectId)return;
  catalogLoading.value=true;
  try{
    catalog.value=await getWorkflowStageCatalog(projectStore.currentProjectId);
    // 默认项取后端按 manifest 解析的包，不在前端自己挑一个"看起来最像"的：
    // 两处各写一套"哪个包管哪个阶段"，改一处就会出现默认项与实际锁定项不一致。
    const nextPins:Record<string,string>={},nextSearch:Record<string,string>={};
    catalog.value.stages.forEach(item=>{
      nextPins[item.stage]=item.default?.skill_id||'';
      nextSearch[item.stage]='';
    });
    pins.value=nextPins;pinSearch.value=nextSearch;
  }catch{Message.error('加载阶段候选 Skill 失败')}
  finally{catalogLoading.value=false}
}
async function openWorkflowStart(){
  workflowForm.value={workflowId:suggestWorkflowId()};
  workflowStep.value=1;
  showWorkflowStartModal.value=true;
  await loadStageCatalog();
}
/**
 * 向导主按钮。第一步只负责"选完了没有"，第二步才真正提交。
 *
 * 返回 `false` 阻止弹窗关闭——第一步结束后要停在同一个弹窗里换内容，
 * 关掉再开一次会让刚选好的包全部重置。
 */
async function onWorkflowModalOk():Promise<boolean>{
  if(workflowStep.value===1){
    if(catalogLoading.value){Message.warning('候选 Skill 还在加载，请稍候');return false}
    if(!pinsComplete.value){Message.warning('请为四个阶段都选定一个可运行的 Skill 包');return false}
    workflowStep.value=2;
    return false;
  }
  return confirmWorkflowStart();
}
async function confirmWorkflowStart():Promise<boolean>{
  const workflowId=workflowForm.value.workflowId.trim();
  if(!workflowId){Message.warning('请填写流程标识');return false}
  if(!projectStore.currentProjectId)return false;
  workflowStarting.value=true;
  try{
    workflowStartResult.value=await startWorkflow(projectStore.currentProjectId,workflowId,{...pins.value});
    showWorkflowStartModal.value=false;
    workflowStep.value=1;
    await loadCockpit();
    // 发起后自动选中新流程：用户点「发起」的下一秒就是想看这条流程的四个阶段，
    // 让他去左栏里再找一遍是多余的。
    activeFlowId.value=workflowId;
    const unmanaged=workflowStartResult.value.unmanaged_stages.length;
    // 未锁定的阶段要立刻提醒：等跑到问题跟踪阶段才暴露，前面阶段的算力与人工就白费了。
    if(unmanaged)Message.warning(`流程已发起，但 ${unmanaged} 个阶段未锁定 Skill 版本，产出将没有版本溯源`);
    else Message.success('流程已发起，四个阶段的 Skill 版本已一次性锁定');
    return true;
  }catch{Message.error('发起流程失败：该操作仅限测试负责人');return false}
  finally{workflowStarting.value=false}
}
const suiteForm=ref({name:'',description:'',suite_type:'regression',task_type:'code_review'}),candidateForm=ref({threshold:.5,minFailureCount:1});
const suiteTypeLabels:Record<string,string>={seed:'种子集',gold:'金标集',regression:'回归集',fresh:'新鲜集',challenge:'挑战集'};
const taskTypeLabels:Record<string,string>={case_review:'用例审查',code_review:'代码审查',knowledge_query:'知识库问答',risk_identification:'风险识别',test_plan_generation:'测试方案',test_execution:'测试执行',testcase_generation:'测试用例',report_generation:'报告生成',issue_tracking:'问题跟踪'};
const signalLabels:Record<string,string>={accepted:'采纳',rejected:'驳回',edited:'编辑',test_passed:'测试通过',test_failed:'测试失败',defect_confirmed:'缺陷确认',false_positive:'误报',missed:'漏报',merged:'已合并',reverted:'已回退'};
const selectedSuite=computed(()=>suites.value.find(v=>v.id===selectedSuiteId.value)),selectedRun=computed(()=>runs.value.find(v=>v.id===selectedRunId.value));
const completedRuns=computed(()=>runs.value.filter(v=>v.status==='completed')),failedResults=computed(()=>results.value.filter(isFailure)),failureRate=computed(()=>results.value.length?Math.round(failedResults.value.length/results.value.length*100):0);
const pendingCandidates=computed(()=>candidates.value.filter(v=>['pending','awaiting_approval'].includes(v.state))),pendingFeedback=computed(()=>feedbackEvents.value.filter(v=>['edited','false_positive','missed','defect_confirmed'].includes(v.signal)).length),totalTokens=computed(()=>results.value.reduce((n,v)=>n+(v.token_usage||0),0));
const pendingOptimizationCount=computed(()=>pendingCandidates.value.length+proposals.value.filter(v=>['draft','pending','awaiting_approval'].includes(v.state)).length+releases.value.filter(v=>['draft','shadow','awaiting_approval'].includes(v.state)).length);
const traces=computed(()=>allTraces.value.filter(v=>(!traceTaskType.value||v.task_type===traceTaskType.value)&&(!traceStatus.value||v.status===traceStatus.value)));
const filteredGoldDatasets=computed(()=>goldTaskType.value?goldDatasets.value.filter(v=>v.task_type===goldTaskType.value):goldDatasets.value);
const traceAttributions=computed(()=>{const outputIds=new Set(allTraces.value.find(v=>v.id===selectedTraceId.value)?.output_ids||[]);return attributions.value.filter(v=>(v.span&&selectedSpans.value.some(s=>s.id===v.span))||(v.output&&outputIds.has(v.output)))});
const failedTraces=computed(()=>allTraces.value.filter(v=>v.status!=='completed'));
const tabs=computed(()=>[{key:'overview' as const,label:'飞轮总览',desc:'指标与待办',icon:IconDashboard},{key:'single' as const,label:'单次能力',desc:'审查与问答',icon:IconExperiment,count:cockpit.value.single_capabilities.length},{key:'workflow' as const,label:'全链路测试',desc:'四阶段门禁',icon:IconBranch,count:cockpit.value.workflows.length},{key:'gold' as const,label:'金标资产',desc:'按业务分类',icon:IconMessage,count:pendingFeedback.value},{key:'evaluation' as const,label:'自动评测',desc:'L0–L3 分层扫描',icon:IconExperiment,count:runs.value.length},{key:'attribution' as const,label:'轨迹归因',desc:'节点与反证',icon:IconBranch,count:failedTraces.value.length},{key:'optimization' as const,label:'优化发布',desc:'影子、灰度、回滚',icon:IconSafe,count:pendingOptimizationCount.value}]);
const loopSteps=computed(()=>[{title:'建设金标集',desc:`${goldDatasets.value.length} 个金标集 · ${feedbackEvents.value.length} 条反馈`,state:pendingFeedback.value?'待复核':'可用',color:pendingFeedback.value?'orange':'green',target:'gold' as const},{title:'自动化评测',desc:`${completedRuns.value.length} 次完成 · ${failedResults.value.length} 个失败`,state:runs.value.length?'可运行':'待建集',color:runs.value.length?'blue':'gray',target:'evaluation' as const},{title:'轨迹回流归因',desc:`${traces.value.length} 条轨迹 · ${attributions.value.length} 条归因`,state:failedTraces.value.length?'待定位':'正常',color:failedTraces.value.length?'red':'green',target:'attribution' as const},{title:'自主优化验证',desc:`${pendingOptimizationCount.value} 项待处理`,state:pendingOptimizationCount.value?'待审批':'受控',color:pendingOptimizationCount.value?'orange':'green',target:'optimization' as const}]);
const sourceDefinitions=Object.entries(taskTypeLabels).map(([key,label])=>({key,label,traceType:key}));
const businessSources=computed(()=>sourceDefinitions.map(source=>({...source,connected:true,count:allTraces.value.filter(v=>v.task_type===source.traceType).length})));

// ---------------------------------------------------------------- Agent 大盘
// 只统计「能力能被 Skill 直接迭代升级」的 5 个阶段：代码审查与知识库问答属
// 平台基础能力，不进 Agent 台账（口径见 specs/agent-ledger/requirements.md §2、§3）。
// 全部指标由 RetrievalTrace 现场聚合，不新增接口；评分按已定口径暂不纳入。
const AGENT_STAGE_ORDER:string[]=['case_review','test_plan_generation','testcase_generation','test_execution','report_generation'];
const DAY_MS=86400000,TREND_DAYS=14;
function traceLatencyMs(v:RetrievalTrace):number{const values=Object.entries(v.timings||{}).filter(([,n])=>typeof n==='number') as [string,number][];return values.reduce((sum,[,n])=>sum+n,0)}
function dayLabel(v:Date):string{return `${String(v.getMonth()+1).padStart(2,'0')}-${String(v.getDate()).padStart(2,'0')}`}
function formatLatency(ms:number):string{if(ms<=0)return'-';return ms>=1000?`${(ms/1000).toFixed(2)}s`:`${Math.round(ms)}ms`}
function sparkHeights(values:number[]):number[]{const max=Math.max(...values,1);return values.map(v=>v>0?Math.max(14,Math.round(v/max*100)):3)}
interface DailyBucket{sessions:number;tokens:number;users:Set<number>;latencySum:number;latencyCount:number;failed:number}
const agentDaily=computed(()=>{
  const days:string[]=[];const now=new Date();
  for(let i=TREND_DAYS-1;i>=0;i-=1){const d=new Date(now);d.setDate(now.getDate()-i);days.push(dayLabel(d))}
  const buckets=new Map<string,DailyBucket>();
  days.forEach(day=>buckets.set(day,{sessions:0,tokens:0,users:new Set<number>(),latencySum:0,latencyCount:0,failed:0}));
  allTraces.value.forEach(trace=>{
    const slot=buckets.get(dayLabel(new Date(trace.created_at)));if(!slot)return;
    slot.sessions+=1;slot.tokens+=trace.token_usage||0;
    if(trace.status!=='completed')slot.failed+=1;
    if(trace.user!=null)slot.users.add(trace.user);
    const latency=traceLatencyMs(trace);if(latency>0){slot.latencySum+=latency;slot.latencyCount+=1}
  });
  return days.map(day=>({date:day,...buckets.get(day)!}));
});
const agentTrend=computed(()=>{
  const sessions=agentDaily.value.map(d=>d.sessions),tokens=agentDaily.value.map(d=>d.tokens);
  const maxSessions=Math.max(...sessions,1),maxTokens=Math.max(...tokens,1);
  return agentDaily.value.map((d,index)=>({date:d.date,sessions:d.sessions,tokens:d.tokens,sessionPct:Math.round(sessions[index]/maxSessions*100),tokenPct:Math.round(tokens[index]/maxTokens*100)}));
});
const hasTrendData=computed(()=>agentDaily.value.some(d=>d.sessions>0||d.tokens>0));
/** 环比取「近 7 天 vs 前 7 天」。前一周没有数据时返回 null——不编一个 0%。 */
function weekDelta(pick:(list:RetrievalTrace[])=>number):number|null{
  const now=Date.now();
  const recent=allTraces.value.filter(v=>now-new Date(v.created_at).getTime()<=7*DAY_MS);
  const previous=allTraces.value.filter(v=>{const age=now-new Date(v.created_at).getTime();return age>7*DAY_MS&&age<=14*DAY_MS});
  const base=pick(previous);if(!base)return null;
  return Math.round((pick(recent)-base)/base*1000)/10;
}
const agentKpis=computed(()=>{
  const list=allTraces.value;
  const sessionsOf=(items:RetrievalTrace[])=>items.length;
  const usersOf=(items:RetrievalTrace[])=>new Set(items.map(v=>v.user).filter((v):v is number=>v!=null)).size;
  const tokensOf=(items:RetrievalTrace[])=>items.reduce((sum,v)=>sum+(v.token_usage||0),0);
  const avgLatencyOf=(items:RetrievalTrace[])=>{const valid=items.map(traceLatencyMs).filter(v=>v>0);return valid.length?valid.reduce((a,b)=>a+b,0)/valid.length:0};
  const failRateOf=(items:RetrievalTrace[])=>items.length?items.filter(v=>v.status!=='completed').length/items.length*100:0;
  const tokens=tokensOf(list);
  return [
    {key:'sessions',label:'会话数',value:sessionsOf(list).toLocaleString(),hint:`${new Set(list.map(v=>v.task_id)).size} 个任务`  ,delta:weekDelta(sessionsOf),spark:sparkHeights(agentDaily.value.map(d=>d.sessions)),tone:''},
    {key:'users',label:'活跃用户',value:usersOf(list).toLocaleString(),hint:'按轨迹去重',delta:weekDelta(usersOf),spark:sparkHeights(agentDaily.value.map(d=>d.users.size)),tone:''},
    {key:'tokens',label:'Token 消耗',value:tokens.toLocaleString(),hint:tokens?'已回传用量':'调用未回传用量',delta:weekDelta(tokensOf),spark:sparkHeights(agentDaily.value.map(d=>d.tokens)),tone:'blue'},
    {key:'latency',label:'平均耗时',value:formatLatency(avgLatencyOf(list)),hint:`${list.filter(v=>traceLatencyMs(v)>0).length} 条有时长`,delta:weekDelta(avgLatencyOf),spark:sparkHeights(agentDaily.value.map(d=>d.latencyCount?d.latencySum/d.latencyCount:0)),tone:''},
    {key:'failed',label:'失败率',value:`${failRateOf(list).toFixed(1)}%`,hint:`${list.filter(v=>v.status!=='completed').length} 条未完成`,delta:weekDelta(failRateOf),spark:sparkHeights(agentDaily.value.map(d=>d.sessions?d.failed/d.sessions*100:0)),tone:'warn',invert:true},
  ];
});
/** Agent 台账：5 个阶段各一行，按会话数 / 用户数 / Token / 耗时 / 失败率聚合。 */
const agentRows=computed(()=>{
  const rows=AGENT_STAGE_ORDER.map(stage=>{
    const items=allTraces.value.filter(v=>v.task_type===stage);
    const valid=items.map(traceLatencyMs).filter(v=>v>0);
    const latency=valid.length?valid.reduce((a,b)=>a+b,0)/valid.length:0;
    const failed=items.filter(v=>v.status!=='completed').length;
    return {stage,label:taskTypeLabels[stage]||stage,sessions:items.length,users:new Set(items.map(v=>v.user).filter((v):v is number=>v!=null)).size,tokens:items.reduce((sum,v)=>sum+(v.token_usage||0),0),latency,latencyText:formatLatency(latency),failed,failedRate:items.length?Math.round(failed/items.length*100):0};
  });
  const maxSessions=Math.max(...rows.map(r=>r.sessions),1),maxUsers=Math.max(...rows.map(r=>r.users),1),maxTokens=Math.max(...rows.map(r=>r.tokens),1),maxLatency=Math.max(...rows.map(r=>r.latency),1);
  return rows.map(r=>({...r,sessionPct:Math.round(r.sessions/maxSessions*100),userPct:Math.round(r.users/maxUsers*100),tokenPct:Math.round(r.tokens/maxTokens*100),latencyPct:Math.round(r.latency/maxLatency*100)}));
});
async function loadSuites(){if(!projectStore.currentProjectId)return;suitesLoading.value=true;try{suites.value=await listEvaluationSuites(projectStore.currentProjectId)}catch{Message.error('加载评测集失败')}finally{suitesLoading.value=false}}
async function loadRuns(){runs.value=selectedSuiteId.value?await listEvaluationRuns(selectedSuiteId.value):[]}
async function loadResults(){results.value=selectedRunId.value?await listEvaluationResults(selectedRunId.value):[]}
async function loadFeedback(){try{feedbackEvents.value=await listFeedbackEvents({project:projectStore.currentProjectId||undefined,signal:feedbackSignal.value||undefined})}catch{Message.error('加载反馈信号失败')}}
async function loadCandidates(){if(!projectStore.currentProjectId)return;try{candidates.value=await listKnowledgeCandidates({project:projectStore.currentProjectId,state:candidateState.value||undefined})}catch{Message.error('加载改进候选失败')}}
async function loadTraces(){if(!projectStore.currentProjectId)return;try{allTraces.value=await listRetrievalTraces({project:projectStore.currentProjectId})}catch{Message.error('加载运行轨迹失败')}}
async function loadGovernance(){if(!projectStore.currentProjectId)return;try{[goldDatasets.value,attributions.value,proposals.value,releases.value]=await Promise.all([listGoldDatasets(projectStore.currentProjectId),listFailureAttributions(projectStore.currentProjectId),listOptimizationProposals(projectStore.currentProjectId),listCapabilityReleases({project:projectStore.currentProjectId})])}catch{Message.error('加载飞轮治理数据失败')}}
async function loadCockpit(){if(!projectStore.currentProjectId)return;try{cockpit.value=await getProjectQualityCockpit(projectStore.currentProjectId)}catch{cockpit.value=emptyCockpit();Message.error('加载项目质量驾驶舱失败')}}
async function selectTrace(v:RetrievalTrace){selectedTraceId.value=v.id;try{selectedSpans.value=await listExecutionSpans(v.id)}catch{selectedSpans.value=[];Message.error('加载节点轨迹失败')}}
async function selectSuite(v:EvaluationSuite){selectedSuiteId.value=v.id;await loadRuns();if(runs.value[0])await selectRun(runs.value[0]);else{selectedRunId.value='';results.value=[]}}
async function selectRun(v:EvaluationRun){selectedRunId.value=v.id;await loadResults()}
async function bootstrap(){await loadSuites();if(suites.value[0])await selectSuite(suites.value.find(v=>v.suite_type==='seed')||suites.value[0]);else{selectedSuiteId.value=undefined;selectedRunId.value='';runs.value=[];results.value=[]}await Promise.all([loadFeedback(),loadCandidates(),loadTraces(),loadGovernance(),loadCockpit()]);if(allTraces.value[0])await selectTrace(allTraces.value[0]);else{selectedTraceId.value='';selectedSpans.value=[]}}
async function refreshAll(){await bootstrap();Message.success('数据已刷新')}
async function createRun(){if(!selectedSuiteId.value)return;try{await createEvaluationRun({suite:selectedSuiteId.value,name:`评测运行 · ${new Date().toLocaleString('zh-CN')}`,policy_version:'default-policy@v3',model_name:'qwen3-coder-plus'});await loadRuns();Message.success('评测运行已启动')}catch{Message.error('启动评测失败')}}
async function confirmCreateSuite(){if(!suiteForm.value.name||!projectStore.currentProjectId){Message.warning('请填写名称并选择项目');return}try{await createEvaluationSuite({project:projectStore.currentProjectId,...suiteForm.value,is_active:true} as Partial<EvaluationSuite>);showSuiteModal.value=false;await loadSuites();Message.success('评测集已创建')}catch{Message.error('创建评测集失败')}}
async function confirmCreateCandidates(){if(!selectedRunId.value)return;try{const t=candidateForm.value.threshold,r=await generateCandidatesFromRun(selectedRunId.value,{thresholds:{l0:t,l1:t,l2:t,l3:t},min_failure_count:candidateForm.value.minFailureCount});showCandidateModal.value=false;await loadCandidates();Message.success(`已生成 ${r.created_count} 个候选`)}catch{Message.error('生成候选失败')}}
async function approve(v:KnowledgeCandidate){try{await updateCandidateState(v.id,'accepted','测试负责人审核通过');await loadCandidates();Message.success('候选已通过')}catch{Message.error('审批失败')}}
async function reject(v:KnowledgeCandidate){try{await updateCandidateState(v.id,'rejected','测试负责人驳回');await loadCandidates();Message.success('候选已驳回')}catch{Message.error('驳回失败')}}
async function runGate(workflowId:string,stage:string){if(!projectStore.currentProjectId)return;gateBusy.value=`${workflowId}:${stage}`;try{await evaluateWorkflowStage(projectStore.currentProjectId,workflowId,stage);await loadCockpit();Message.success('质量门禁已完成')}catch{Message.error('门禁评测失败')}finally{gateBusy.value=''}}
function openOverride(workflowId:string,stage:string){gateOverride.value={workflowId,stage,reason:''};showGateOverrideModal.value=true}
async function confirmGateOverride(){if(!projectStore.currentProjectId||!gateOverride.value.reason.trim()){Message.warning('请填写放行原因');return}try{await overrideWorkflowStage(projectStore.currentProjectId,gateOverride.value.workflowId,gateOverride.value.stage,gateOverride.value.reason);showGateOverrideModal.value=false;await loadCockpit();Message.success('已记录负责人放行决策')}catch{Message.error('放行失败，请确认当前账号是项目负责人')}}
const reviewable=(v:KnowledgeCandidate)=>['pending','awaiting_approval'].includes(v.state),signalText=(v:string)=>signalLabels[v]||v;
const personInitial=(v:ProjectQualityPerson)=>(v.display_name||v.username||'?').trim().slice(0,1).toUpperCase();
const capabilityCode=(v:string)=>({case_review:'CR',code_review:'CODE',knowledge_query:'KB'} as Record<string,string>)[v]||'AI';
//: 状态文案/配色唯一真值。`unscored`（无评分）与 `failed`（结论为负）必须分开显示：
//: 两者在页面上的含义完全不同——前者是"还没评上"，后者是"评了没过"，
//: 都写成"未通过"会让人以为链路被一个负面结论挡住了。
const gateText=(v:string)=>({pending:'待测评',unscored:'无评分',passed:'已通过',failed:'未通过',confirmed:'人工确认',overridden:'负责人放行',ready:'可执行',blocked:'待前置',running:'执行中'} as Record<string,string>)[v]||v;
const gateColor=(v:string)=>({pending:'orange',unscored:'gold',passed:'green',failed:'red',confirmed:'cyan',overridden:'purple',ready:'arcoblue',blocked:'gray',running:'blue'} as Record<string,string>)[v]||'gray';
const workflowSummary=(v:ProjectWorkflowView)=>{
  if(v.stages.some(s=>s.status==='failed'))return '有门禁未通过';
  if(v.stages.every(s=>isPassedStatus(s.status)))return '全流程通过';
  if(v.stages.some(s=>s.status==='running'))return '执行中';
  if(v.stages.some(s=>s.status==='unscored'))return '存在无评分阶段';
  if(v.stages.some(s=>s.status==='pending'))return '待测评';
  return '进行中';
};
const workflowColor=(v:ProjectWorkflowView)=>{
  if(v.stages.some(s=>s.status==='failed'))return 'red';
  if(v.stages.every(s=>isPassedStatus(s.status)))return 'green';
  if(v.stages.some(s=>s.status==='running'))return 'blue';
  return 'arcoblue';
};
function signalColor(v:string){if(['accepted','test_passed','merged'].includes(v))return'green';if(['rejected','test_failed','defect_confirmed'].includes(v))return'red';if(['false_positive','missed'].includes(v))return'orange';return'blue'}
const feedbackSummary=(v:FeedbackEvent)=>v.comment||(JSON.stringify(v.detail||{})==='{}'?'无附加说明':JSON.stringify(v.detail).slice(0,100)),feedbackActor=(v:FeedbackEvent)=>v.actor?.username||({user:'测试人员',system:'系统',integration:'集成服务'}[v.actor_type]);
const statusText=(v:string)=>({pending:'待执行',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'} as Record<string,string>)[v]||v,statusColor=(v:string)=>({completed:'green',running:'blue',failed:'red',pending:'orange'} as Record<string,string>)[v]||'gray';
const candidateColor=(v:string)=>({pending:'orange',awaiting_approval:'blue',accepted:'green',rejected:'red',conflicted:'red',evaluating:'blue',merged:'arcoblue'} as Record<string,string>)[v]||'gray',candidateStateText=(v:string)=>({pending:'待处理',awaiting_approval:'待审批',accepted:'已通过',rejected:'已驳回',conflicted:'冲突',evaluating:'评测中',merged:'已合并'} as Record<string,string>)[v]||v;
const candidateTitle=(v:KnowledgeCandidate)=>String(v.payload.title||v.payload.name||v.payload.statement||`${v.kind}-${v.id.slice(0,8)}`),fmt=(v?:number|null)=>v==null?'-':v.toFixed(2),formatDate=(v?:string)=>v?new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
const traceStatusText=(v:string)=>({completed:'已完成',failed:'失败',untraceable:'不可回流'} as Record<string,string>)[v]||v,traceStatusColor=(v:string)=>({completed:'green',failed:'red',untraceable:'orange'} as Record<string,string>)[v]||'gray';
function traceChannels(v:RetrievalTrace){const keys=Object.keys(v.channels||{});return keys.length?keys.slice(0,3).join(' / '):'未记录'}
function traceLatency(v:RetrievalTrace){const values=Object.entries(v.timings||{}).filter(([,n])=>typeof n==='number') as [string,number][];if(!values.length)return'-';const total=values.reduce((sum,[,n])=>sum+n,0);return total>=1000?`${(total/1000).toFixed(2)}s`:`${Math.round(total)}ms`}
const stepTypeText=(v:string)=>({intent:'意图理解',planning:'复杂规划',retrieval:'知识检索',graph_retrieval:'图谱检索',prompt:'Prompt组装',model:'模型推理',tool:'工具调用',validation:'结果校验',handoff:'Agent交接',human_edit:'人工修改',downstream:'下游执行'} as Record<string,string>)[v]||v;
const attributionStateText=(v:string)=>({proposed:'待确认',confirmed:'已确认',rejected:'已排除'} as Record<string,string>)[v]||v,attributionCategoryText=(v:string)=>({intent_error:'意图理解失败',planning_error:'复杂规划失败',knowledge_missing:'知识缺失',knowledge_stale:'知识过期',retrieval_error:'检索失败',prompt_error:'Prompt失败',tool_error:'工具失败',generation_error:'生成失败',downstream_execution_error:'下游执行失败'} as Record<string,string>)[v]||v;
const proposalTypeText=(v:string)=>({prompt:'Prompt',knowledge:'知识',retrieval_policy:'检索策略',skill:'Skill/工具'} as Record<string,string>)[v]||v,releaseStateText=(v:string)=>({draft:'草稿',shadow:'灰度中',awaiting_approval:'待审批',active:'已生效',rejected:'已拒绝',rolled_back:'已回滚',retired:'已退役',materialized:'已物化'} as Record<string,string>)[v]||v;
function releaseStateColor(v:string){if(v==='active')return'green';if(['rejected','rolled_back'].includes(v))return'red';if(['shadow','awaiting_approval'].includes(v))return'orange';return'blue'}
function gateSummary(v:CapabilityRelease){const report=v.gate_report||{};if(report.passed===true)return'已通过影子硬门禁';if(report.passed===false)return'未通过影子硬门禁';return'待执行影子对比'}
const levels=['l0','l1','l2','l3'] as const;function scores(v:EvaluationResult){return levels.map(k=>[k,v[`${k}_score`]] as const).filter((p):p is readonly[typeof levels[number],number]=>typeof p[1]==='number')}function isFailure(v:EvaluationResult){return v.status==='failed'||scores(v).some(([,n])=>n<.5)}function failureSummary(v:EvaluationResult){if(v.error_message)return v.error_message;const s=scores(v);if(!s.length)return'执行失败，暂无评分';const [l,n]=s.reduce((a,b)=>b[1]<a[1]?b:a);return`${l.toUpperCase()} 得分 ${n.toFixed(2)} 低于门槛 0.50`}
watch(()=>projectStore.currentProjectId,async id=>{if(id)await bootstrap()},{immediate:true});
</script>

<style scoped>
.qe-page{height:100%;box-sizing:border-box;padding:20px;overflow:auto;color:var(--theme-page-text,var(--color-text-1));background:var(--theme-page-bg,var(--color-fill-1));--blue:#165dff;--navy:#102a43;--teal:#0f766e;--orange:#ff7d00}.empty-page{height:100%;display:grid;place-items:center}.hero{position:relative;display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding:22px 26px;overflow:hidden;border-radius:12px;color:#fff;background:linear-gradient(118deg,var(--navy),#176b87 62%,var(--teal));box-shadow:0 12px 30px rgb(16 42 67/16%)}.hero>div:first-child>span{font-size:10px;letter-spacing:.18em;color:#9fe1dd}.hero h1{margin:5px 0 3px;font-size:26px}.hero p{margin:0;color:#d8edf0}.hero-actions{z-index:1;display:flex;align-items:center;gap:8px}.hero-actions :deep(.arco-tag){color:#fff;background:rgb(255 255 255/12%);border-color:rgb(255 255 255/16%)}.tabs{display:flex;gap:4px;margin:14px 0;padding:5px;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.tabs button{display:flex;align-items:center;justify-content:center;gap:7px;min-width:130px;padding:10px 15px;border:0;border-radius:7px;color:var(--color-text-2);background:transparent;cursor:pointer}.tabs button:hover{background:var(--color-fill-2)}.tabs button.active{color:var(--blue);background:rgb(var(--arcoblue-1));font-weight:600}.tabs small{min-width:18px;padding:1px 5px;border-radius:10px;background:var(--color-fill-3)}main{min-height:620px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}.metrics article{position:relative;padding:17px 19px;overflow:hidden;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.metrics article:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--teal)}.metrics .warn:before{background:var(--orange)}.metrics .blue:before{background:var(--blue)}.metrics span,.metrics small{display:block;color:var(--color-text-3)}.metrics b{display:block;margin:5px 0 3px;font-size:25px}.panel{border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2);box-shadow:0 6px 18px rgb(29 33 41/4%)}.padded,.loop-panel,.content-panel,.suite-panel,.eval-panel{padding:18px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.section-head span{font-size:11px;letter-spacing:.08em;color:var(--color-text-3)}.section-head h2{margin:3px 0 0;font-size:17px}.loop{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.loop button{position:relative;display:flex;min-width:0;align-items:flex-start;gap:8px;padding:13px 10px 39px;text-align:left;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1);cursor:pointer}.loop button:hover{border-color:var(--blue);background:rgb(var(--arcoblue-1))}.loop em{font-size:10px;font-weight:700;color:var(--blue);font-style:normal}.loop button div{min-width:0}.loop b,.loop small{display:block}.loop small{margin-top:4px;overflow:hidden;color:var(--color-text-3);text-overflow:ellipsis;white-space:nowrap}.loop .arco-tag{position:absolute;right:8px;bottom:8px}.overview-grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(340px,1fr);gap:12px;margin-top:12px}.task-list{display:grid;gap:8px}.task-list button{display:grid;grid-template-columns:38px 1fr auto 14px;align-items:center;gap:11px;padding:12px;text-align:left;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1);cursor:pointer}.task-list button:hover{border-color:var(--blue)}.task-list i{display:grid;width:34px;height:34px;place-items:center;border-radius:7px;font-style:normal}.task-list i.blue{color:var(--blue);background:rgb(var(--arcoblue-1))}.task-list i.orange{color:var(--orange);background:rgb(var(--orange-1))}.task-list i.teal{color:var(--teal);background:rgb(var(--green-1))}.task-list b,.task-list small{display:block}.task-list small{margin-top:3px;color:var(--color-text-3)}.task-list strong{font-size:18px}.roles article{display:flex;align-items:center;gap:11px;margin-top:9px;padding:12px;border:1px solid var(--color-border-2);border-radius:8px}.roles article>svg{flex:none;font-size:22px;color:var(--blue)}.roles article:first-of-type>svg{color:var(--orange)}.roles article div{min-width:0;flex:1}.roles b,.roles small{display:block}.roles small{margin-top:3px;color:var(--color-text-3)}.roles .agent{background:var(--color-fill-1)}.roles .agent>span{font-size:11px;color:var(--color-text-3)}.roles .agent>span i{display:inline-block;width:6px;height:6px;margin-right:5px;border-radius:50%;background:#00b42a}.content-panel{min-height:570px}.toolbar{padding-bottom:14px;border-bottom:1px solid var(--color-border-2)}.actions{display:flex;gap:8px}.table-head,.rows article{display:grid;grid-template-columns:110px minmax(260px,1fr) 150px 150px;align-items:center;gap:12px}.table-head{padding:9px 12px;color:var(--color-text-3);background:var(--color-fill-1)}.rows article{padding:13px 12px;border-bottom:1px solid var(--color-border-1)}.rows b,.rows small{display:block}.rows small,.rows time{margin-top:3px;color:var(--color-text-3)}.split-view{display:grid;grid-template-columns:290px minmax(0,1fr);gap:12px;min-height:620px}.suite{display:flex;width:100%;align-items:center;justify-content:space-between;margin-top:7px;padding:12px;text-align:left;border:1px solid transparent;border-radius:8px;background:var(--color-fill-1);cursor:pointer}.suite:hover,.suite.active{border-color:var(--blue);background:rgb(var(--arcoblue-1))}.suite b,.suite small{display:block}.suite small{margin-top:4px;color:var(--color-text-3)}.run-list{display:grid;gap:7px}.run-list button{display:grid;grid-template-columns:minmax(180px,1fr) auto 72px;align-items:center;gap:12px;padding:12px;text-align:left;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1);cursor:pointer}.run-list button.active{border-color:var(--blue)}.run-list b,.run-list small{display:block}.run-list small{margin-top:3px;color:var(--color-text-3)}.run-list button>span{display:flex;gap:4px}.run-list i{padding:3px 5px;border-radius:4px;color:var(--color-text-2);background:var(--color-fill-3);font-size:11px;font-style:normal}.result-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}.result-summary div{padding:12px;border-left:3px solid var(--blue);background:var(--color-fill-1)}.result-summary span,.result-summary b{display:block}.result-summary span{color:var(--color-text-3)}.result-summary b{margin-top:5px;font-size:20px}.failures article{display:grid;grid-template-columns:54px 1fr 90px;gap:10px;padding:9px;border-top:1px solid var(--color-border-1)}.failures small{color:var(--color-text-3)}.candidate-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.candidate-grid>article{display:flex;min-height:170px;flex-direction:column;padding:15px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}.candidate-grid header,.candidate-grid footer{display:flex;align-items:center;justify-content:space-between;gap:8px}.candidate-grid h3{margin:14px 0 6px;font-size:15px}.candidate-grid p{margin:0;color:var(--color-text-3)}.candidate-grid footer{margin-top:auto;padding-top:14px}.candidate-grid footer>span{font-size:11px;color:var(--color-text-3)}.candidate-grid footer div{display:flex;gap:6px}.governance{display:flex;min-height:560px;flex-direction:column;align-items:center;justify-content:center;text-align:center}.governance>svg{font-size:48px;color:var(--blue)}.governance>span{margin-top:18px;letter-spacing:.12em;color:var(--color-text-3)}.governance h2{margin:8px 0;font-size:24px}.governance p{max-width:620px;color:var(--color-text-3);line-height:1.7}.governance div{display:flex;gap:8px;margin-top:12px}@media(max-width:1200px){.loop{grid-template-columns:repeat(3,1fr)}.candidate-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:900px){.hero{align-items:flex-start;flex-direction:column}.tabs{overflow:auto}.metrics,.overview-grid,.split-view{grid-template-columns:1fr}.loop,.candidate-grid{grid-template-columns:1fr}.table-head{display:none}.rows article{grid-template-columns:90px 1fr}.rows article time{grid-column:2}.run-list button>span{display:none}}
.loop{grid-template-columns:repeat(4,1fr)}
.qe-page{padding:16px;background:#f5f7fa;--blue:#1677ff;--navy:#1f2937;--teal:#0f766e;--orange:#f59e0b}.hero{align-items:center;padding:16px 20px;border:1px solid #dbe7f5;border-radius:10px;color:var(--color-text-1);background:var(--color-bg-2);box-shadow:none}.hero:before{content:"";width:4px;align-self:stretch;border-radius:3px;background:var(--blue)}.hero>div:first-child>span{color:var(--blue)}.hero h1{font-size:23px}.hero p{color:var(--color-text-3)}.hero-actions :deep(.arco-tag){color:var(--color-text-2);background:var(--color-fill-1);border-color:var(--color-border-2)}.workspace-shell{display:grid;grid-template-columns:292px minmax(0,1fr);align-items:start;gap:14px;margin-top:14px}.stage-rail{position:sticky;top:0;min-height:calc(100vh - 150px);padding:14px}.rail-title{padding:4px 6px 12px;border-bottom:1px solid var(--color-border-2)}.rail-title span,.rail-title b{display:block}.rail-title span{font-size:11px;letter-spacing:.08em;color:var(--color-text-3)}.rail-title b{margin-top:4px}.stage-rail .tabs{display:grid;gap:5px;margin:12px 0;padding:0;border:0;background:transparent}.stage-rail .tabs button{display:grid;grid-template-columns:32px 1fr auto;justify-content:initial;width:100%;min-width:0;padding:10px;text-align:left}.stage-rail .tabs button>i{display:grid;width:30px;height:30px;place-items:center;border-radius:7px;color:var(--color-text-3);background:var(--color-fill-2);font-style:normal}.stage-rail .tabs button>span{display:block;min-width:0}.stage-rail .tabs button b,.stage-rail .tabs button small{display:block}.stage-rail .tabs button small{margin-top:2px;overflow:hidden;color:var(--color-text-3);text-overflow:ellipsis;white-space:nowrap}.stage-rail .tabs button em{min-width:22px;padding:2px 6px;border-radius:11px;text-align:center;color:var(--color-text-3);background:var(--color-fill-3);font-size:11px;font-style:normal}.stage-rail .tabs button.active{color:var(--blue);background:rgb(var(--arcoblue-1))}.stage-rail .tabs button.active>i{color:#fff;background:var(--blue)}.workspace-content{min-width:0}.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}.metrics article{box-shadow:none}.loop-panel{padding:18px}.loop button{min-height:112px;background:var(--color-bg-2)}.loop button:after{content:"";position:absolute;top:50%;right:-9px;width:9px;border-top:1px dashed rgb(var(--arcoblue-5))}.loop button:last-child:after{display:none}.overview-grid{grid-template-columns:1fr}.source-grid article{background:var(--color-bg-2)}.panel{box-shadow:none}.content-panel,.split-view{animation:panel-in .18s ease-out}@keyframes panel-in{from{opacity:.4;transform:translateY(4px)}to{opacity:1;transform:none}}
.source-panel{margin-top:12px;padding:18px}.source-panel>.section-head>small{color:var(--color-text-3)}.source-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.source-grid article{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:11px 12px;border:1px solid rgb(var(--green-3));border-radius:8px;background:rgb(var(--green-1))}.source-grid article.pending{border-color:var(--color-border-2);background:var(--color-fill-1)}.source-grid b,.source-grid small{display:block}.source-grid small{margin-top:3px;color:var(--color-text-3)}
.toolbar p{max-width:780px;margin:6px 0 0;color:var(--color-text-3)}.trace-head,.trace-rows article{display:grid;grid-template-columns:120px minmax(260px,1fr) 150px 100px 80px 90px;align-items:center;gap:12px}.trace-head{padding:9px 12px;color:var(--color-text-3);background:var(--color-fill-1)}.trace-rows article{padding:13px 12px;border-bottom:1px solid var(--color-border-1)}.trace-rows b,.trace-rows small{display:block}.trace-rows small{margin-top:3px;color:var(--color-text-3)}
.roadmap{display:flex;align-items:center;gap:12px;margin-top:18px;padding:14px;border:1px dashed rgb(var(--orange-5));border-radius:9px;background:rgb(var(--orange-1))}.roadmap>svg{flex:none;font-size:28px;color:var(--orange)}.roadmap>div{min-width:0;flex:1}.roadmap b,.roadmap small{display:block}.roadmap small{margin-top:4px;color:var(--color-text-3)}
.subheading{margin:18px 0 10px;font-size:15px}.asset-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-bottom:14px}.asset-strip>article{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.asset-strip b,.asset-strip small{display:block}.asset-strip small{margin-top:4px;color:var(--color-text-3)}.trace-rows article{cursor:pointer}.trace-rows article:hover,.trace-rows article.selected{background:rgb(var(--arcoblue-1))}.trace-detail{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(310px,.65fr);gap:14px;margin-top:18px;padding-top:16px;border-top:1px solid var(--color-border-2)}.trace-detail h3{margin:0 0 10px;font-size:15px}.span-line{display:grid;gap:7px}.span-line article{display:grid;grid-template-columns:10px 1fr auto;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.span-line i{width:8px;height:8px;border-radius:50%;background:var(--color-fill-4)}.span-line i.completed{background:#16a34a}.span-line i.failed{background:#ef4444}.span-line i.running{background:#1677ff}.span-line b,.span-line small{display:block}.span-line small{margin-top:3px;color:var(--color-text-3)}.trace-detail aside>article{margin-bottom:8px;padding:12px;border:1px solid var(--color-border-2);border-radius:8px}.trace-detail aside header{display:flex;justify-content:space-between;margin-bottom:8px}.trace-detail aside p{margin:6px 0;color:var(--color-text-2)}.trace-detail aside small{color:var(--color-text-3)}.governance-board{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}.governance-board>section{padding:14px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}.mini-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}.mini-head span{display:grid;min-width:22px;height:22px;place-items:center;border-radius:11px;color:var(--blue);background:rgb(var(--arcoblue-1))}.governance-board section>article{margin-top:8px;padding:11px;border:1px solid var(--color-border-2);border-radius:7px;background:var(--color-bg-2)}.governance-board article>div{display:flex;justify-content:space-between}.governance-board article>b,.governance-board article>small{display:block;margin-top:8px}.governance-board article>p{margin:5px 0;color:var(--color-text-3)}.governance-board article>small{color:var(--color-text-3)}
.rail-roles{margin-top:18px;padding-top:14px;border-top:1px solid var(--color-border-2)}.team-head,.team-head>div,.team-head button,.group-title,.person-main,.person-card footer,.person-card footer small{display:flex;align-items:center}.team-head{justify-content:space-between}.team-head>div{gap:8px}.team-head>div>i{width:4px;height:18px;border-radius:3px;background:var(--blue)}.team-head span{font-size:15px;font-weight:700;color:var(--color-text-1)}.team-head button{gap:4px;padding:4px;border:0;color:var(--blue);background:transparent;cursor:pointer}.team-head button:hover{color:rgb(var(--arcoblue-7))}.people-group{margin-top:16px}.people-group+.people-group{margin-top:20px;padding-top:18px;border-top:1px solid var(--color-border-2)}.group-title{justify-content:space-between;margin-bottom:9px}.group-title b{font-size:13px}.group-title span{padding:2px 7px;border-radius:10px;color:var(--color-text-3);background:var(--color-fill-2);font-size:11px}.person-card{margin-top:8px;padding:12px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1);transition:border-color .16s ease,box-shadow .16s ease,transform .16s ease}.person-card:hover{border-color:rgb(var(--arcoblue-4));box-shadow:0 7px 18px rgb(22 93 255/8%);transform:translateY(-1px)}.person-main{gap:10px}.person-main>i{display:grid;width:38px;height:38px;flex:none;place-items:center;border-radius:50%;color:#fff;background:linear-gradient(145deg,#4080ff,#165dff);font-size:15px;font-style:normal;font-weight:700;box-shadow:0 4px 10px rgb(22 93 255/20%)}.executor-group .person-main>i{background:linear-gradient(145deg,#14c9c9,#0e8a98);box-shadow:0 4px 10px rgb(20 201 201/18%)}.person-main>div{min-width:0;flex:1}.person-main b,.person-main small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.person-main b{font-size:14px}.person-main small{margin-top:2px;color:var(--color-text-3);font-size:11px}.person-main em{flex:none;padding:3px 7px;border:1px solid rgb(var(--arcoblue-3));border-radius:5px;color:var(--blue);background:rgb(var(--arcoblue-1));font-size:10px;font-style:normal}.executor-group .person-main em{border-color:rgb(var(--cyan-3));color:rgb(var(--cyan-7));background:rgb(var(--cyan-1))}.person-card footer{justify-content:space-between;gap:7px;margin-top:10px;padding-top:9px;border-top:1px solid var(--color-border-1)}.person-card footer>span{min-width:0;color:var(--color-text-3);font-size:11px}.person-card footer small{flex:none;gap:4px;color:var(--color-text-3);font-size:10px}.person-card footer small i{width:6px;height:6px;border-radius:50%;background:#00b42a;box-shadow:0 0 0 3px rgb(var(--green-1))}.people-group :deep(.arco-empty){padding:10px 0}.people-group :deep(.arco-empty-image){display:none}.people-group :deep(.arco-empty-description){font-size:11px}
.single-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.single-grid>article{padding:18px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}.single-grid header{display:flex;align-items:flex-start;justify-content:space-between}.single-grid header>div{display:flex;align-items:center;gap:10px}.single-grid header span{display:grid;width:42px;height:42px;place-items:center;border-radius:8px;color:#fff;background:var(--blue);font-size:11px;font-weight:700}.single-grid h3{margin:0;font-size:16px}.single-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:20px 0}.single-stats span{padding:10px;border-radius:7px;text-align:center;background:var(--color-bg-2)}.single-stats b,.single-stats small{display:block}.single-stats b{font-size:21px}.single-stats small,.single-grid footer{color:var(--color-text-3)}
.workflow-list{display:grid;gap:14px}.workflow-list>article{padding:16px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}.workflow-list>article>header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.workflow-list header b,.workflow-list header small{display:block}.workflow-list header small{margin-top:3px;color:var(--color-text-3)}/* 逐阶段步骤条：done=已放行、active/running/failed=当前步、todo=未轮到的灰步。
   todo 明确降透明度而不是"淡化颜色"——灰步必须一眼看出"还没轮到"，不是"待办事项"。 */
/* 用 flex 而不是 grid：步骤之间还有连接线 `.step-link` 这种"非步骤"元素，
   放在 grid 里会被当成格子占位，4 步 + 3 线 = 7 个格子必然折行成 2×2。
   flex 下步骤 flex:1 等分、连接线固定 20px，一行排完。 */
.stage-stepper{display:flex;align-items:stretch}
.step{display:grid;flex:1 1 0;min-width:0;gap:5px;justify-items:start;padding:12px;border:1px solid var(--color-border-2);border-top:3px solid var(--color-border-3);border-radius:8px;background:var(--color-bg-2);font-family:inherit;text-align:left;cursor:pointer;transition:border-color .16s ease,box-shadow .16s ease}
.step:hover{border-color:rgb(var(--arcoblue-4))}
.step.done{border-top-color:#16a34a}
.step.active,.step.running{border-top-color:#1677ff}
.step.active{box-shadow:0 4px 14px rgb(22 93 255/12%)}
.step.failed{border-top-color:#ef4444}
.step.todo{opacity:.5}
.step.selected{outline:2px solid rgb(var(--arcoblue-5));outline-offset:1px}
.step .step-dot{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;color:var(--color-text-2);background:var(--color-fill-3);font-size:11px;font-style:normal;font-weight:700}
.step.done .step-dot{color:#fff;background:#16a34a}
.step.active .step-dot,.step.running .step-dot{color:#fff;background:#1677ff}
.step.failed .step-dot{color:#fff;background:#ef4444}
.step b{font-size:14px}
.step small{color:var(--color-text-3);font-size:11px}
.step-link{align-self:center;flex:0 0 20px;height:2px;background:var(--color-border-3)}
.step-link.done{background:#16a34a}
.stage-detail{margin-top:14px;padding:14px;border:1px solid var(--color-border-2);border-left:3px solid var(--color-border-3);border-radius:8px;background:var(--color-bg-2)}
.stage-detail.done{border-left-color:#16a34a}
.stage-detail.active,.stage-detail.running{border-left-color:#1677ff}
.stage-detail.failed{border-left-color:#ef4444}
.stage-detail.todo{opacity:.55}
.detail-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.detail-head>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.detail-head b{font-size:15px}
.detail-head small{color:var(--color-text-3)}
.detail-note{margin:10px 0 0;color:var(--color-text-2);font-size:12px;line-height:1.75}
.dispatch-note{margin:10px 0 0;padding:10px;border-radius:7px;background:rgb(var(--arcoblue-1));color:var(--color-text-1);font-size:12px;line-height:1.85}
.dispatch-note code{padding:1px 5px;border-radius:4px;background:var(--color-fill-2);font-size:11px}
.stage-actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.score-preview{font-size:12px;font-weight:700}
.score-preview.ok{color:#16a34a}
.score-preview.bad{color:#ef4444}
.output-meta{display:flex;flex-wrap:wrap;gap:14px;padding-bottom:10px;color:var(--color-text-3);font-size:12px;border-bottom:1px solid var(--color-border-2)}
.output-meta .sha{font-family:ui-monospace,MENLO,monospace}
.output-gate{display:flex;align-items:center;gap:10px;padding:10px 0;color:var(--color-text-2);font-size:12px}
.output-content{margin:0;padding:12px;max-height:420px;overflow:auto;border-radius:7px;background:var(--color-fill-1);color:var(--color-text-1);font-size:12px;line-height:1.75;white-space:pre-wrap;word-break:break-word}
.gold-type-nav{display:flex;gap:7px;margin-bottom:14px;padding-bottom:12px;overflow:auto;border-bottom:1px solid var(--color-border-2)}.gold-type-nav button{flex:none;padding:7px 10px;border:1px solid var(--color-border-2);border-radius:7px;color:var(--color-text-2);background:var(--color-bg-2);cursor:pointer}.gold-type-nav button.active{border-color:var(--blue);color:var(--blue);background:rgb(var(--arcoblue-1))}.gold-type-nav b{margin-left:5px}
@media(max-width:1200px){.metrics{grid-template-columns:repeat(2,1fr)}.source-grid{grid-template-columns:repeat(2,1fr)}.stage-stepper{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.step-link{display:none}.single-grid{grid-template-columns:1fr}}
@media(max-width:900px){.workspace-shell{grid-template-columns:1fr}.stage-rail{position:static;min-height:auto}.stage-rail .tabs{grid-template-columns:repeat(7,minmax(145px,1fr));overflow:auto}.rail-roles{display:none}.metrics,.source-grid,.asset-strip,.trace-detail,.governance-board{grid-template-columns:1fr}.trace-head{display:none}.trace-rows article{grid-template-columns:90px 1fr}.trace-rows article span,.trace-rows article strong{grid-column:2}}

/* AgentLoop-inspired information architecture: quiet shell, horizontal workspaces, dense data canvas. */
.qe-page{min-height:100%;padding:0 18px 24px;background:#f7f8fa;color:#1d2129}
.hero{display:flex;margin:0 -18px;padding:17px 22px;border:0;border-bottom:1px solid #e5e6eb;border-radius:0;background:#fff}
.hero:before{display:none}.hero-copy{min-width:0}.hero h1{display:inline;margin:0;font-size:20px}.hero p{display:inline;margin-left:14px;font-size:12px}.hero-actions{margin-left:auto}.hero-actions :deep(.arco-btn){border-color:#e5e6eb;background:#fff}
.workspace-tabs{display:flex;gap:3px;margin:14px 0;padding:4px;overflow-x:auto;border:1px solid #e5e6eb;border-radius:10px;background:#fff}
.workspace-tabs button{display:flex;height:38px;flex:none;align-items:center;gap:7px;padding:0 13px;border:0;border-radius:7px;color:#4e5969;background:transparent;cursor:pointer;font-size:13px;transition:background .16s ease,color .16s ease,box-shadow .16s ease}
.workspace-tabs button:hover{color:#165dff;background:#f2f3f5}.workspace-tabs button.active{color:#1d2129;background:#f2f3f5;box-shadow:inset 0 0 0 1px #c9cdd4}.workspace-tabs button svg{font-size:15px}.workspace-tabs em{min-width:19px;padding:1px 5px;border-radius:9px;color:#86909c;background:#e5e6eb;font-size:10px;font-style:normal;text-align:center}.workspace-tabs button.active em{color:#165dff;background:#e8f3ff}
.workspace-shell{grid-template-columns:minmax(0,1fr) 282px;gap:14px;margin-top:0}.workspace-content{order:1}.team-panel{position:sticky;top:14px;order:2;padding:17px;background:#fff}.team-note{margin:8px 0 0;color:#86909c;font-size:11px;line-height:1.55}
.metrics{gap:0;margin-bottom:14px;overflow:hidden;border:1px solid #e5e6eb;border-radius:10px;background:#fff}.metrics article{min-height:126px;padding:17px 20px;border:0;border-right:1px solid #f0f1f2;border-radius:0;background:#fff}.metrics article:last-child{border-right:0}.metrics article:before{display:none}.metrics span{font-size:12px}.metrics b{margin:7px 0 2px;font-size:27px;letter-spacing:-.02em}.metrics small{font-size:11px}
.panel{border-color:#e5e6eb;border-radius:10px;background:#fff}.loop-panel,.source-panel,.overview-grid .panel{padding:20px}.section-head h2{font-size:16px}.section-head span{color:#86909c}.loop{gap:0;border:1px solid #e5e6eb;border-radius:9px}.loop button{min-height:124px;padding:17px 14px 43px;border:0;border-right:1px solid #e5e6eb;border-radius:0;background:#fff}.loop button:first-child{border-radius:8px 0 0 8px}.loop button:last-child{border-right:0;border-radius:0 8px 8px 0}.loop button:after{right:-1px;width:1px;border:0}.loop button:hover{background:#f7f8fa}.loop em{display:grid;width:24px;height:24px;flex:none;place-items:center;border-radius:50%;color:#165dff;background:#e8f3ff}.source-grid article{border-color:#e5e6eb!important;background:#fff!important}.source-grid article:hover{background:#f7f8fa!important}.task-list button{background:#fff}.task-list button:hover{background:#f7f8fa}
.team-head{padding-bottom:13px;border-bottom:1px solid #f0f1f2}.team-head>div>i{width:3px;height:16px}.team-head span{font-size:15px}.people-group{margin-top:17px}.person-card{padding:13px;background:#f7f8fa}.person-card:hover{background:#fff}.person-main>i{width:42px;height:42px;box-shadow:none}.person-main b{font-size:14px}.person-card footer>span{font-size:10px}.group-title b{font-size:12px}
.content-panel,.suite-panel,.eval-panel{background:#fff}.toolbar{padding-bottom:16px}.workflow-list>article,.single-grid>article,.asset-strip>article,.governance-board>section{background:#fafafa}
@media(max-width:1320px){.workspace-shell{grid-template-columns:minmax(0,1fr) 250px}.metrics article{padding-right:14px;padding-left:14px}.workspace-tabs button{padding:0 10px}}
@media(max-width:1080px){.workspace-shell{grid-template-columns:1fr}.team-panel{position:static;order:2}.workspace-content{order:1}.team-panel .people-group{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.team-panel .group-title,.team-panel :deep(.arco-empty){grid-column:1/-1}.people-group+.people-group{margin-top:16px}.metrics{grid-template-columns:repeat(2,1fr)}.metrics article:nth-child(2){border-right:0}.metrics article:nth-child(-n+2){border-bottom:1px solid #f0f1f2}}
@media(max-width:720px){.qe-page{padding-right:10px;padding-left:10px}.hero{margin-right:-10px;margin-left:-10px;align-items:flex-start}.hero p{display:block;margin:5px 0 0}.hero-actions{width:100%;margin:10px 0 0}.workspace-tabs{border-radius:8px}.metrics{grid-template-columns:1fr}.metrics article{border-right:0;border-bottom:1px solid #f0f1f2!important}.loop{display:grid;grid-template-columns:1fr}.loop button{border-right:0;border-bottom:1px solid #e5e6eb}.team-panel .people-group{display:block}}
.primary-tabs{display:flex;gap:24px;margin:0 -18px 14px;padding:0 22px;border-bottom:1px solid #e5e6eb;background:#fff}.primary-tabs button{position:relative;display:flex;height:48px;align-items:center;gap:7px;padding:0 2px;border:0;color:#4e5969;background:transparent;cursor:pointer;font-size:14px}.primary-tabs button:hover{color:#165dff}.primary-tabs button.active{color:#1d2129;font-weight:600}.primary-tabs button.active:after{content:"";position:absolute;right:0;bottom:-1px;left:0;height:2px;border-radius:2px;background:#165dff}.primary-tabs svg{font-size:16px}
.quick-tabs{display:flex;width:max-content;gap:3px;margin:0 0 14px;padding:3px;border-radius:8px;background:#e5e6eb}.quick-tabs button{min-width:88px;padding:7px 16px;border:0;border-radius:6px;color:#4e5969;background:transparent;cursor:pointer}.quick-tabs button.active{color:#1d2129;background:#fff;box-shadow:0 1px 4px rgb(29 33 41/10%);font-weight:600}
.launch-console{min-height:560px;padding:24px}.launch-heading{display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:20px;border-bottom:1px solid #e5e6eb}.launch-heading span{font-size:10px;font-weight:700;letter-spacing:.14em;color:#165dff}.launch-heading h2{margin:5px 0 4px;font-size:20px}.launch-heading p{margin:0;color:#86909c}.launch-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:20px}.launch-grid button{display:grid;grid-template-columns:42px minmax(0,1fr) auto 14px;align-items:center;gap:12px;min-height:116px;padding:18px;text-align:left;border:1px solid #e5e6eb;border-radius:9px;background:#fff;cursor:pointer}.launch-grid button:hover{border-color:#94bfff;background:#f7faff;box-shadow:0 8px 20px rgb(22 93 255/7%)}.launch-grid button>i{display:grid;width:40px;height:40px;place-items:center;border-radius:8px;color:#165dff;background:#e8f3ff;font-size:18px;font-style:normal}.launch-grid b,.launch-grid small{display:block}.launch-grid b{font-size:14px}.launch-grid small{margin-top:5px;color:#86909c;line-height:1.45}.launch-grid em{align-self:start;padding:3px 7px;border-radius:10px;color:#86909c;background:#f2f3f5;font-size:10px;font-style:normal;white-space:nowrap}.launch-grid>button>svg{color:#86909c}
.console-context{display:flex;align-items:center;gap:12px;margin-bottom:10px}.console-context button{display:flex;align-items:center;gap:4px;padding:6px 0;border:0;color:#165dff;background:transparent;cursor:pointer}.console-context button svg{transform:rotate(180deg)}.console-context span{color:#86909c;font-size:12px}.skill-hub-embed{min-height:680px;overflow:hidden;border:1px solid #e5e6eb;border-radius:10px;background:#fff}.knowledge-graph-embed{min-width:0}.graph-layout{grid-template-columns:minmax(0,1fr)}.knowledge-graph-embed :deep(.knowledge-graph-page){padding:0}.skill-hub-embed :deep(.skill-hub-console){border:0}
@media(max-width:1320px){.launch-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.primary-tabs{gap:16px;margin-right:-10px;margin-left:-10px;padding:0 12px}.quick-tabs{width:100%}.quick-tabs button{flex:1}.launch-grid{grid-template-columns:1fr}.launch-console{padding:16px}}
.board-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:12px}.board-kpis article{position:relative;padding:16px 18px 34px;overflow:hidden;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.board-kpis article:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--teal)}.board-kpis .warn:before{background:var(--orange)}.board-kpis .blue:before{background:var(--blue)}.board-kpis span{display:block;font-size:12px;color:var(--color-text-3)}.kpi-value{display:flex;align-items:baseline;gap:8px;margin:6px 0 2px}.kpi-value b{font-size:22px;line-height:1.1}.kpi-value em{font-size:12px;font-style:normal}.kpi-value em.up{color:rgb(var(--green-6))}.kpi-value em.down{color:rgb(var(--red-6))}.board-kpis small{display:block;font-size:12px;color:var(--color-text-3)}.kpi-spark{position:absolute;left:18px;right:18px;bottom:12px;display:flex;align-items:flex-end;gap:2px;height:18px}.kpi-spark i{flex:1;min-height:2px;border-radius:1px;background:rgb(var(--arcoblue-3));opacity:.6}.board-kpis .warn .kpi-spark i{background:rgb(var(--orange-3))}.chart-body{margin-top:6px}.chart-legend{display:flex;gap:16px;font-size:12px;color:var(--color-text-3)}.chart-legend span{display:flex;align-items:center;gap:6px}.chart-legend .dot{display:inline-block;width:8px;height:8px;border-radius:2px}.chart-legend .dot.session{background:rgb(var(--arcoblue-5))}.chart-legend .dot.token{background:var(--teal)}.chart-bars{display:flex;align-items:flex-end;gap:6px;height:180px;margin-top:12px}.bar-col{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;height:100%}.bar-stack{display:flex;align-items:flex-end;gap:2px;width:100%;height:100%}.bar{flex:1;min-height:2px;border-radius:2px 2px 0 0}.bar.session{background:rgb(var(--arcoblue-5))}.bar.token{background:var(--teal)}.bar-col small{font-size:10px;color:var(--color-text-3)}.board-table .agent-head,.board-table .agent-rows article{display:grid;grid-template-columns:1.6fr .8fr .8fr 1fr .9fr .7fr;gap:12px;align-items:center}.agent-head{padding:8px 14px;border-bottom:1px solid var(--color-border-2);font-size:12px;color:var(--color-text-3)}.agent-rows article{padding:12px 14px;border-bottom:1px solid var(--color-border-1)}.agent-rows article:last-child{border-bottom:0}.agent-rows article.idle{opacity:.55}.agent-name b{display:block;font-size:14px}.agent-name small{display:block;font-size:12px;color:var(--color-text-3)}.cell{display:flex;flex-direction:column;gap:5px}.cell b{font-size:13px}.meter{display:block;height:4px;overflow:hidden;border-radius:2px;background:var(--color-fill-2)}.meter u{display:block;height:100%;border-radius:2px;background:rgb(var(--arcoblue-6));text-decoration:none}.start-result{margin-bottom:12px;padding:14px 16px;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.start-result-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.start-result-head b{font-size:14px}.start-result-head small{display:block;font-size:12px;color:var(--color-text-3)}.binding-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.binding{padding:10px 12px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.binding.locked{border-color:rgb(var(--green-6));background:rgb(var(--green-1))}.binding.unmanaged{border-color:rgb(var(--orange-6));background:rgb(var(--orange-1))}.binding b{display:block;margin-bottom:4px;font-size:13px}.binding small{display:block;font-size:12px;color:var(--color-text-2);word-break:break-all}.binding .sha{color:var(--color-text-3)}.warn-line{margin:10px 0 0;font-size:12px;color:rgb(var(--orange-6))}
/* ---- 全链路测试：流程版本列表 + 四阶段时间线；右侧团队栏另有「AI 专家团队」 */
.wf-shell{display:grid;grid-template-columns:236px minmax(0,1fr);gap:14px;align-items:start}
.wf-flows{display:grid;gap:8px;align-content:start;max-height:680px;overflow:auto;padding:12px;border:1px solid #e5e6eb;border-radius:9px;background:#fafafa}
.wf-flows-head{display:flex;align-items:center;justify-content:space-between;padding-bottom:9px;border-bottom:1px solid #e5e6eb}.wf-flows-head b{font-size:13px}.wf-flows-head span{padding:2px 7px;border-radius:10px;color:#86909c;background:#e5e6eb;font-size:11px}
.wf-flow{display:grid;gap:7px;padding:11px;border:1px solid #e5e6eb;border-radius:8px;color:inherit;background:#fff;font-family:inherit;text-align:left;cursor:pointer;transition:border-color .16s ease,box-shadow .16s ease}
.wf-flow:hover{border-color:#94bfff}.wf-flow.active{border-color:#165dff;box-shadow:0 4px 14px rgb(22 93 255/12%)}
.wf-flow-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.wf-flow-top b{overflow:hidden;font-size:13px;text-overflow:ellipsis;white-space:nowrap}
.wf-flow-meta{display:flex;justify-content:space-between;color:#86909c;font-size:11px}
.wf-flow-bar{display:block;height:4px;overflow:hidden;border-radius:2px;background:#f0f1f2}.wf-flow-bar u{display:block;height:100%;background:#165dff}
.wf-flow>small{color:#86909c;font-size:10px}
.wf-timeline{display:grid;gap:12px;align-content:start;min-width:0}
/* 阶段状态条：01–04 一屏看完整条链路走到哪一步 */
.wf-stage-bar{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.wf-step{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:8px;padding:10px 12px;border:1px solid #e5e6eb;border-top:3px solid #e5e6eb;border-radius:8px;color:inherit;background:#fff;font-family:inherit;text-align:left;cursor:pointer}
.wf-step i{grid-row:span 2;display:grid;width:26px;height:26px;place-items:center;border-radius:50%;color:#4e5969;background:#f2f3f5;font-size:11px;font-style:normal;font-weight:700}
.wf-step b{overflow:hidden;font-size:13px;text-overflow:ellipsis;white-space:nowrap}.wf-step small{color:#86909c;font-size:11px}
.wf-step.done{border-top-color:#16a34a}.wf-step.done i{color:#fff;background:#16a34a}
.wf-step.active,.wf-step.running{border-top-color:#1677ff}.wf-step.active i,.wf-step.running i{color:#fff;background:#1677ff}
.wf-step.failed{border-top-color:#ef4444}.wf-step.failed i{color:#fff;background:#ef4444}
.wf-step.todo{opacity:.55}.wf-step.selected{outline:2px solid rgb(var(--arcoblue-5));outline-offset:1px}
/* 时间线卡片：左半边 AI 产出、右半边人工结论，底部是流转控制 */
.wf-cards{display:grid;gap:10px}
.wf-card{padding:14px;border:1px solid #e5e6eb;border-left:3px solid #e5e6eb;border-radius:9px;background:#fff}
.wf-card.done{border-left-color:#16a34a}.wf-card.active,.wf-card.running{border-left-color:#1677ff}
.wf-card.failed{border-left-color:#ef4444}.wf-card.todo{opacity:.6}
.wf-card.active{box-shadow:0 4px 16px rgb(22 93 255/8%)}
.wf-card-head{display:flex;align-items:center;gap:10px}
.wf-card-head>i{display:grid;width:28px;height:28px;flex:none;place-items:center;border-radius:7px;color:#165dff;background:#e8f3ff;font-size:12px;font-style:normal;font-weight:700}
.wf-card-head>div{min-width:0;flex:1}.wf-card-head b,.wf-card-head small{display:block}.wf-card-head b{font-size:14px}
.wf-card-head small{margin-top:2px;overflow:hidden;color:#86909c;font-size:11px;text-overflow:ellipsis;white-space:nowrap}
.wf-card-body{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}
.wf-side{padding:11px 12px;border:1px solid #f0f1f2;border-radius:8px;background:#fafafa}
.wf-side h4{display:flex;align-items:center;gap:6px;margin:0 0 8px;color:#4e5969;font-size:12px}
.wf-side.ai{border-color:#e8f3ff;background:#f7faff}
.wf-side.human{border-color:#d9f2e6;background:#f6fffb}
.wf-line{margin:5px 0 0;color:#4e5969;font-size:12px;line-height:1.6;word-break:break-word}
.wf-line code{padding:1px 5px;border-radius:4px;background:#f2f3f5;font-size:11px}
.wf-return-note{margin:10px 0 0;padding:8px 10px;border-radius:7px;color:#4e5969;background:#fff7e8;font-size:12px;line-height:1.7}
.wf-card-actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.expert-group .person-main>i{background:linear-gradient(145deg,#7d5cff,#4e3bd6);box-shadow:none}
.expert-state{color:#86909c}.expert-state.ok{color:#16a34a}.expert-state.bad{color:#ef4444}
/* 发起向导第一步：四个阶段各一个搜索框，选完才给「下一步」 */
.pin-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px}
.pin-block{display:grid;gap:8px;padding:12px;border:1px solid #e5e6eb;border-radius:9px;background:#fafafa}
.pin-block header{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.pin-block header b{font-size:13px}
.pin-block header small{overflow:hidden;color:#86909c;font-size:11px;text-overflow:ellipsis;white-space:nowrap}
.pin-block header small.missing{color:#ff7d00}
.pin-list{display:grid;gap:6px;max-height:190px;overflow:auto}
.pin-item{display:grid;gap:3px;padding:8px 10px;border:1px solid #e5e6eb;border-radius:7px;color:inherit;background:#fff;font-family:inherit;text-align:left;cursor:pointer}
.pin-item:hover{border-color:#94bfff}.pin-item.selected{border-color:#165dff;background:#f7faff}.pin-item.blocked{opacity:.6}
.pin-item>div{display:flex;align-items:center;gap:6px}
.pin-item b{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}
.pin-item>small{color:#86909c;font-size:10px}
.pin-empty{margin:0;padding:8px 0;color:#86909c;font-size:11px;text-align:center}
.pin-warn{margin:12px 0 0;padding:9px 11px;border:1px dashed #ff7d00;border-radius:8px;color:#4e5969;background:#fff7e8;font-size:12px;line-height:1.7}
.pin-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:16px}
.pin-summary article{padding:10px;border:1px solid #e5e6eb;border-radius:8px;background:#fafafa}
.pin-summary b,.pin-summary small{display:block}.pin-summary b{font-size:12px}
.pin-summary small{margin-top:4px;overflow:hidden;color:#86909c;font-size:11px;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:1080px){.wf-shell{grid-template-columns:1fr}.wf-flows{max-height:240px}.wf-stage-bar{grid-template-columns:repeat(2,minmax(0,1fr))}.wf-card-body,.pin-grid,.pin-summary{grid-template-columns:1fr}}
</style>
