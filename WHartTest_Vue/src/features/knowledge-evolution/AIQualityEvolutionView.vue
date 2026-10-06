<template>
  <div class="qe-page">
    <div v-if="!projectStore.currentProjectId" class="empty-page">
      <a-empty description="请先选择项目，再进入质量飞轮" />
    </div>
    <template v-else>
      <header class="hero">
        <!-- ⚠️ 横幅是**页面级**的，不是页签级的：数据飞轮的三个页签（控制台 / Skill 进化工坊 /
             Skill Hub）共用同一条横幅、统一显示「数据飞轮」；知识图谱页显示「知识图谱」。
             它回答的是"现在在哪个页面"，不能随页签切换而消失。真正重复的是**内容区**里
             各子组件再写一遍「Skill Hub / Skill 进化工坊 / 知识图谱」，那部分由子组件按
             `embedded` 隐去（见 SkillManager / SkillHubConsole / KnowledgeGraphView）。 -->
        <div class="hero-copy"><h1>{{ pageHeader.title }}</h1><p>{{ pageHeader.description }}</p></div>
        <div class="hero-actions"><a-tag color="green">人工门禁开启</a-tag><a-tag><icon-storage /> {{ projectName }}</a-tag><a-button @click="refreshAll"><template #icon><icon-refresh /></template>刷新数据</a-button></div>
      </header>

      <nav v-if="primaryView==='data'" class="quick-tabs" aria-label="数据飞轮功能">
        <button type="button" :class="{active:quickMode==='console'}" @click="quickMode='console'">控制台</button>
        <button type="button" :class="{active:quickMode==='skills'}" @click="quickMode='skills'">Skill 进化工坊</button>
        <button type="button" :class="{active:quickMode==='hub'}" @click="quickMode='hub'">Skill Hub</button>
      </nav>

      <div class="workspace-shell" :class="{'graph-layout':primaryView==='graph'}">

      <main class="workspace-content">
        <div v-if="primaryView==='data' && quickMode==='console' && workspace!=='overview'" class="console-context">
          <button type="button" @click="workspace='overview'"><icon-right/>返回控制台</button>
          <span>{{ tabs.find(item=>item.key===workspace)?.label }}</span>
        </div>
        <template v-if="primaryView === 'agents'">
          <section class="agent-filter-bar">
            <div>
              <span>Agent 筛选</span>
              <a-select
                v-model="selectedAgentStages"
                multiple
                allow-clear
                allow-search
                :max-tag-count="3"
                placeholder="全部 Agent"
                style="width:420px"
              >
                <a-option v-for="stage in AGENT_STAGE_ORDER" :key="stage" :value="stage">{{ taskTypeLabels[stage] || stage }}</a-option>
              </a-select>
            </div>
            <small>已选 {{ selectedAgentStages.length || AGENT_STAGE_ORDER.length }} / {{ AGENT_STAGE_ORDER.length }} 个 Agent · 近 {{ TREND_DAYS }} 天</small>
          </section>
          <section class="board-kpis">
            <button v-for="kpi in agentKpis" :key="kpi.key" type="button" :class="[kpi.tone,{active:selectedAgentMetric===kpi.key}]" @click="selectedAgentMetric=kpi.key">
              <span>{{ kpi.label }}</span>
              <div class="kpi-value"><b>{{ kpi.value }}</b><em v-if="kpi.delta!==null" :class="kpi.invert?(kpi.delta>=0?'down':'up'):(kpi.delta>=0?'up':'down')">{{ kpi.delta>=0?'+':'' }}{{ kpi.delta }}%</em></div>
              <small>{{ kpi.hint }}</small>
              <div class="kpi-spark" aria-hidden="true"><i v-for="(height,index) in kpi.spark" :key="index" :style="{height:`${height}%`}"/></div>
            </button>
          </section>
          <section class="panel board-chart">
            <div class="section-head chart-title"><div><span>指标大图</span><h2>{{ activeAgentKpi.label }}趋势</h2><p>{{ activeAgentKpi.hint }} · 点击上方指标卡可切换</p></div><div class="chart-total"><small>{{ activeAgentKpi.summaryLabel }}</small><b>{{ activeAgentKpi.value }}</b></div></div>
            <div v-if="hasTrendData" class="chart-body">
              <div class="chart-bars">
                <div v-for="point in activeAgentTrend" :key="point.date" class="bar-col">
                  <b>{{ point.label }}</b>
                  <div class="bar-stack"><i :class="['bar',selectedAgentMetric]" :style="{height:`${point.pct}%`}" :title="`${point.date} · ${point.label}`"/></div>
                  <small>{{ point.date }}</small>
                </div>
              </div>
              <div class="chart-axis-note"><span>{{ activeAgentKpi.label }}</span><span>{{ activeAgentTrend.length }} 个数据点</span></div>
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
          <!-- focus-node：产出详情的「内容来源」里点某条引用节点，就定位到它。
               定位能力归图谱视图（只有它知道当前数据源里有没有这个节点），这里只递 id。 -->
          <KnowledgeGraphView embedded :focus-node="graphFocusNode" />
        </section>

        <section v-else-if="quickMode === 'skills'" class="skill-hub-embed">
          <SkillHubConsole embedded :project-id="projectStore.currentProjectId!" :key="`quality-skill-hub-${projectStore.currentProjectId}`" />
        </section>

        <!-- Skill Hub：与左侧菜单的 Skill Hub 页是同一份实现（SkillManager）。
             两处入口都指向同一个组件，避免"同一套列表两处各写一遍"随后漂移。 -->
        <section v-else-if="quickMode === 'hub'" class="skill-hub-embed">
          <SkillManager embedded :project-id="projectStore.currentProjectId!" :key="`quality-skill-list-${projectStore.currentProjectId}`" />
        </section>

        <section v-else-if="workspace === 'overview'" class="panel launch-console">
          <div class="launch-heading">
            <div><span>QUICK START</span><h2>选择一项工作</h2><p>进入对应的数据、评测或治理流程。</p></div>
            <a-tag color="arcoblue">{{ projectName }}</a-tag>
          </div>
          <div class="launch-grid">
            <button type="button" @click="workspace='single'"><i><icon-experiment/></i><div><b>独立能力评测</b><small>用例审查的单次质量检查</small></div><em>约 5 分钟</em><icon-right/></button>
            <button type="button" @click="workspace='workflow'"><i><icon-branch/></i><div><b>全链路测试</b><small>{{ workflowChainText }}</small></div><em>{{ cockpit.workflows.length }} 条流程</em><icon-right/></button>
            <button type="button" @click="workspace='gold'"><i><icon-message/></i><div><b>建设评测数据</b><small>整理反馈、Badcase 与金标数据</small></div><em>{{ pendingFeedback }} 条待复核</em><icon-right/></button>
            <button type="button" @click="workspace='evaluation'"><i><icon-dashboard/></i><div><b>运行自动评测</b><small>选择数据集，执行分层质量扫描</small></div><em>{{ runs.length }} 次运行</em><icon-right/></button>
            <button type="button" @click="workspace='attribution'"><i><icon-branch/></i><div><b>分析失败轨迹</b><small>识别意图、规划、检索、模型或工具调用问题</small></div><em>{{ failedTraces.length }} 条待分析</em><icon-right/></button>
            <button type="button" @click="workspace='optimization'"><i><icon-safe/></i><div><b>优化与发布</b><small>影子验证、人工审批、灰度发布与安全回滚</small></div><em>{{ pendingOptimizationCount }} 项待处理</em><icon-right/></button>
          </div>
        </section>

        <section v-else-if="workspace === 'single'" class="panel content-panel">
          <div class="section-head toolbar"><div><span>单次评测</span><h2>独立能力质量面板</h2><p>用例审查是当前唯一的单次能力 Agent；代码审查与知识库问答属平台基础能力，只做质量观测、不进自进化。</p></div></div>
          <div class="single-grid">
            <article v-for="item in cockpit.single_capabilities" :key="item.stage">
              <header>
                <div><span>{{ capabilityCode(item.stage) }}</span><h3>{{ taskTypeLabels[item.stage] || item.stage }}</h3></div>
                <a-tag :color="item.failed ? 'red' : item.outputs ? 'green' : 'gray'">{{ item.failed ? '存在失败' : item.outputs ? '正常' : '暂无产出' }}</a-tag>
              </header>
              <div class="single-stats">
                <span><b>{{ item.outputs }}</b><small>产出</small></span>
                <span><b>{{ item.feedback }}</b><small>反馈</small></span>
                <span><b>{{ item.failed }}</b><small>失败</small></span>
              </div>
              <footer>
                <span>最近产出 {{ formatDate(item.latest_at || undefined) }}</span>
                <!-- 「能不能自进化」由后端给（注册表判定），前端不按阶段名认：
                     代码审查是复合能力、知识库问答是平台工具，两者都不该出现这个入口。 -->
                <a-button v-if="item.self_evolution" size="small" type="primary" @click="openReviewEvolution">
                  <template #icon><icon-plus/></template>发起流程
                </a-button>
              </footer>
            </article>
          </div>

          <!-- 进化结果常驻页面：只在弹窗里闪一下就没了的话，关掉后连候选版本号和
               下载入口都找不回来，用户只能去 Skill 进化工坊里翻。 -->
          <div v-if="reviewEvolutionResult" class="start-result">
            <div class="start-result-head">
              <div>
                <b>已派生候选版本 {{ reviewEvolutionResult.candidate.version }}</b>
                <small>{{ reviewEvolutionResult.skill_name }} · 基线 {{ reviewEvolutionResult.baseline_version }}</small>
              </div>
              <div class="actions">
                <a-button size="mini" type="primary" :loading="reviewEvolutionDownloading" @click="downloadReviewCandidate">
                  <template #icon><icon-download/></template>下载 Skill 包
                </a-button>
                <a-button size="mini" @click="reviewEvolutionResult=null">收起</a-button>
              </div>
            </div>
            <div class="evolution-facts">
              <article>
                <small>候选版本</small>
                <b>{{ reviewEvolutionResult.candidate.version }}</b>
                <em>{{ reviewEvolutionResult.candidate.state==='draft' ? '草稿 · 待评测与审批' : reviewEvolutionResult.candidate.state }}</em>
              </article>
              <article>
                <small>包哈希</small>
                <b class="sha">{{ (reviewEvolutionResult.candidate.package_sha256||'').slice(0,16) }}</b>
                <em>基线 {{ (reviewEvolutionResult.baseline_package_sha256||'').slice(0,16) }}</em>
              </article>
              <article>
                <small>报告采纳率</small>
                <b>{{ reviewEvolutionResult.scan.acceptance_score }}%</b>
                <em>门槛 {{ reviewEvolutionResult.threshold }}%</em>
              </article>
              <article>
                <small>活跃包</small>
                <b :class="reviewEvolutionResult.active_untouched?'':'bad'">{{ reviewEvolutionResult.active_untouched ? '未改动' : '⚠ 被改动' }}</b>
                <em>派生只写新版本目录</em>
              </article>
            </div>
            <p class="evolution-scan">
              报告解析：采纳 <b>{{ reviewEvolutionResult.scan.affirmative }}</b> 条 ·
              误报 <b>{{ reviewEvolutionResult.scan.negative }}</b> 条 ·
              说明被改写 <b>{{ reviewEvolutionResult.scan.rewritten }}</b> 条 ·
              未确认 <b>{{ reviewEvolutionResult.scan.unconfirmed }}</b> 条
            </p>
            <div class="evolution-defects">
              <article v-for="item in reviewEvolutionResult.scan.defects" :key="`${item.category}:${item.issue_type}`">
                <header>
                  <a-tag :color="item.category==='prompt_error' ? 'orange' : 'blue'" size="small">{{ attributionCategoryText(item.category) }}</a-tag>
                  <b>{{ item.issue_type }}</b>
                  <span>{{ item.count }} 条</span>
                </header>
                <!-- 这段文字会被原样写进新包的 SKILL.md 护栏小节，
                     显示出来等于让人在提交前就看见"包会被改成什么要求"。 -->
                <p>{{ item.hypothesis }}</p>
              </article>
            </div>
            <pre v-if="reviewEvolutionDiffText" class="output-content">{{ reviewEvolutionDiffText }}</pre>
            <p class="detail-note">
              候选版本<strong>尚未激活</strong>。激活是可选的钉版手段：该项目若已有激活的生产版本，
              需在 Skill 进化工坊完成评测与负责人审批后才会切到新包；若此前没有激活版本，
              新包已经是最新的可运行版本，下一次运行即按新包执行。
              回滚目标 {{ reviewEvolutionResult.rollback_target }}。
            </p>
          </div>
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
                      <!-- 自动跳转之后用户会回到本页看状态；这里保留一个直达入口，
                           免得"想再去跑一次"只能靠浏览器回退。链接里的
                           execution_context_id 过期时，业务页面会明确提示重新派发。 -->
                      <template v-if="planFor(activeFlow.workflow_id,step.stage)!.launch_url">
                        <br/><a class="dispatch-link" @click="router.push(planFor(activeFlow.workflow_id,step.stage)!.launch_url)">前往执行 →</a>
                        <small class="dispatch-ttl">（上下文有效至 {{ contextExpiryText(planFor(activeFlow.workflow_id,step.stage)!.execution_context_expires_at) }}）</small>
                      </template>
                    </p>
                    <!-- 平台没有"打回上一阶段"这个动作，就不能摆一个按了没反应的按钮。
                         不通过时人真正该做的是重跑门禁或请负责人放行，这里把出路写清楚。 -->
                    <p v-if="step.status==='failed'" class="wf-return-note">本阶段结论为不通过。平台不含「打回上一阶段」动作：请修改产出后重跑门禁测评，或由负责人填写原因强制放行。</p>
                    <footer class="wf-card-actions">
                      <a-button v-if="step.output_id" size="small" @click="openStageOutput(activeFlow.workflow_id,step.stage)">
                        <template #icon><icon-file/></template>查看结果
                      </a-button>
                      <!-- R1/R2 入口：两个按钮只在**已产出**时渲染（不显示优于禁用——
                           一个禁用按钮只会让人反复点它想知道为什么）。
                           产物是登记文件还是平台回落模板由后端判定，按钮悬浮提示如实标出。 -->
                      <a-button
                        v-if="step.output_id"
                        size="small"
                        :title="step.artifact?.source==='registered' ? `下载该阶段登记的报告产物：${step.artifact?.name}` : '该阶段没有登记产物，将下载平台按产出正文渲染的文本报告'"
                        :loading="stageArtifactBusy===`${activeFlow.workflow_id}:${step.stage}`"
                        @click="downloadStageReport(activeFlow.workflow_id,step.stage)"
                      >
                        <template #icon><icon-download/></template>下载报告
                      </a-button>
                      <a-button
                        v-if="step.output_id"
                        size="small"
                        :loading="stageFeedbackBusy===`${activeFlow.workflow_id}:${step.stage}`"
                        @click="pickStageFeedback(activeFlow.workflow_id,step.stage)"
                      >
                        <template #icon><icon-upload/></template>上传反馈
                      </a-button>
                      <a-button v-if="!step.output_id" size="small" type="primary" :disabled="!['ready','running'].includes(step.status)" :loading="executeBusy===`${activeFlow.workflow_id}:${step.stage}`" @click="executeStage(activeFlow.workflow_id,step.stage)">
                        <template #icon><icon-play-arrow/></template>跳转到Agent执行
                      </a-button>
                      <a-button v-if="step.output_id && ['pending','unscored','failed'].includes(step.status)" size="small" :loading="gateBusy===`${activeFlow.workflow_id}:${step.stage}`" @click="runGate(activeFlow.workflow_id,step.stage)">运行门禁测评</a-button>
                      <a-button v-if="step.output_id && step.scorable" size="small" @click="openStageScore(activeFlow.workflow_id,step.stage)">
                        <template #icon><icon-edit/></template>人工评分
                      </a-button>
                      <a-button v-if="step.output_id && step.confirmable" size="small" type="primary" status="success" :loading="gateBusy===`${activeFlow.workflow_id}:${step.stage}`" @click="confirmStage(activeFlow.workflow_id,step.stage)">确认进入下一阶段</a-button>
                      <a-button v-if="step.status==='failed'" size="small" status="warning" @click="openOverride(activeFlow.workflow_id,step.stage)">负责人放行</a-button>
                    </footer>
                    <!-- 上传成功后就地回显，不弹 toast 了事：采纳率是拿来和别的版本比的，
                         一闪而过的提示等于没记录。低于参考线只标黄，**不构成拦截**。 -->
                    <p v-if="stageFeedbackOf(activeFlow.workflow_id,step.stage)" class="wf-feedback-echo">
                      <b>采纳率 {{ stageFeedbackOf(activeFlow.workflow_id,step.stage)!.acceptance_score }}%</b>
                      · 参考线 {{ stageFeedbackOf(activeFlow.workflow_id,step.stage)!.acceptance_reference }}%
                      <a-tag v-if="stageFeedbackOf(activeFlow.workflow_id,step.stage)!.below_reference" size="small" color="gold">低于参考线（仅作版本对比，不影响记录）</a-tag>
                      <a-tag v-else size="small" color="green">达参考线</a-tag>
                      <a-tag v-if="!stageFeedbackOf(activeFlow.workflow_id,step.stage)!.created" size="small">同一份报告已记录过</a-tag>
                      <small>取自「{{ stageFeedbackOf(activeFlow.workflow_id,step.stage)!.acceptance_sheet }}」页</small>
                    </p>
                  </article>
                </div>
              </template>
              <a-empty v-else description="左侧选中一条流程后，这里显示它的四阶段时间线"/>
            </div>
          </div>
        </section>

        <section v-else-if="workspace === 'gold'" class="panel content-panel">
          <div class="section-head toolbar"><div><span>第一环 · 金标资产</span><h2>资产审核与数据集治理</h2><p>自动化只生成候选；进入回归集、专用集或冻结版本前必须经过人工审核。</p></div><div class="actions"><a-select v-if="goldMode==='feedback'" v-model="feedbackSignal" allow-clear placeholder="全部信号" style="width:160px" @change="loadFeedback"><a-option value="">全部信号</a-option><a-option v-for="(label,key) in signalLabels" :key="key" :value="key">{{ label }}</a-option></a-select><a-select v-if="goldMode==='review'" v-model="assetCandidateStatus" allow-clear placeholder="全部状态" style="width:150px" @change="loadAssetCandidates"><a-option value="">全部状态</a-option><a-option value="pending">待处理</a-option><a-option value="needs_review">待人工审核</a-option><a-option value="completed">已处理待补件</a-option><a-option value="failed">处理失败</a-option><a-option value="dead_letter">死信</a-option></a-select><a-button v-if="goldMode==='review' && assetCandidateStats.failed+assetCandidateStats.dead_letter" status="warning" :loading="assetRetryBusy" @click="retryAllAssetCandidates">重试失败项</a-button><a-button type="primary" @click="showSuiteModal=true"><template #icon><icon-plus/></template>新建评测集</a-button></div></div>
          <div class="asset-mode-tabs"><button type="button" :class="{active:goldMode==='review'}" @click="goldMode='review'">候选审核 <b>{{ assetCandidateStats.needs_review }}</b></button><button type="button" :class="{active:goldMode==='datasets'}" @click="goldMode='datasets';loadDatasetGovernance()">数据集版本 <b>{{ goldDatasets.length }}</b></button><button type="button" :class="{active:goldMode==='history'}" @click="goldMode='history';loadHistoryWorkspace()">历史回放 <b>{{ historyImports.length }}</b></button><button type="button" :class="{active:goldMode==='feedback'}" @click="goldMode='feedback'">原始反馈 <b>{{ feedbackEvents.length }}</b></button></div>
          <template v-if="goldMode==='review'">
            <div class="asset-queue-kpis"><article><small>待处理</small><b>{{ assetCandidateStats.pending + assetCandidateStats.processing }}</b></article><article><small>待人工审核</small><b>{{ assetCandidateStats.needs_review }}</b></article><article :class="{warn:assetCandidateStats.failed}"><small>失败</small><b>{{ assetCandidateStats.failed }}</b></article><article :class="{bad:assetCandidateStats.dead_letter}"><small>死信</small><b>{{ assetCandidateStats.dead_letter }}</b></article></div>
            <div class="asset-event-head"><span>来源</span><span>候选与预检</span><span>状态</span><span>时间</span><span>操作</span></div>
            <div v-if="assetCandidateEvents.length" class="asset-events"><article v-for="item in assetCandidateEvents" :key="item.id"><div><b>{{ item.source_type_label || item.source_type }}</b><small>{{ item.source_id }}</small></div><div><b>{{ assetCandidateTitle(item) }}</b><small>{{ assetCandidatePreflight(item) }}</small><p v-if="item.last_error">{{ item.last_error }}</p></div><a-tag :color="assetCandidateStatusColor(item.status)">{{ item.status_label || item.status }}</a-tag><time>{{ formatDate(item.created_at) }}</time><a-button v-if="['failed','dead_letter'].includes(item.status)" size="mini" :loading="assetRetryId===item.id" @click="retryOneAssetCandidate(item.id)">重试</a-button><a-button v-else-if="item.candidate" size="mini" type="primary" @click="openAssetReview(item.candidate)">人工审核</a-button><span v-else class="asset-event-result">—</span></article></div>
            <a-empty v-else description="当前筛选条件下没有候选事件"/>
          </template>
          <template v-else-if="goldMode==='datasets'">
          <div class="gold-type-nav"><button type="button" :class="{active:!goldTaskType}" @click="goldTaskType=''">全部 <b>{{ goldDatasets.length }}</b></button><button v-for="(label,key) in taskTypeLabels" :key="key" type="button" :class="{active:goldTaskType===key}" @click="goldTaskType=key">{{ label }} <b>{{ (cockpit.gold_by_type[key] || []).length }}</b></button></div>
          <div class="governance-columns"><section><h3>数据集与版本</h3><div class="asset-strip"><article v-for="dataset in filteredGoldDatasets" :key="dataset.id" class="clickable" @click="selectGoldDataset(dataset.id)"><div><b>{{ dataset.name }}</b><small>{{ taskTypeLabels[dataset.task_type] || dataset.task_type }} · {{ dataset.version_count || 0 }} 个版本 · {{ dataset.scope_type==='domain'?'项目专用':'通用' }}</small></div><a-tag :color="dataset.status==='active'?'green':'gray'">{{ dataset.status==='active'?'启用':'已归档' }}</a-tag></article><a-empty v-if="!filteredGoldDatasets.length" description="当前类型暂无金标集" /></div><div class="governance-list"><article v-for="version in goldVersions" :key="version.id"><div><b>{{ version.version }}</b><small>{{ version.case_count }} 条 · {{ version.content_hash?`sha ${version.content_hash.slice(0,10)}`:'尚未冻结' }}</small></div><a-tag :color="version.state==='frozen'?'green':'blue'">{{ version.state }}</a-tag><a-button v-if="version.state!=='frozen'&&version.state!=='retired'" size="mini" status="warning" @click="freezeVersion(version.id)">冻结</a-button></article></div></section><section><div class="section-inline"><h3>分类与关键场景</h3><a-button size="mini" type="primary" @click="showTaxonomyModal=true">新建版本</a-button></div><div class="governance-list"><article v-for="item in taxonomies" :key="item.id"><div><b>{{ item.scope_key }} · {{ item.version }}</b><small>{{ item.categories.length }} 个分类 · {{ item.critical_scenarios.length }} 个关键场景</small></div><a-tag :color="item.state==='published'?'green':item.state==='review'?'orange':'gray'">{{ item.state }}</a-tag><a-button v-if="item.state==='draft'" size="mini" @click="submitTaxonomy(item.id)">送审</a-button><a-button v-if="item.state==='review'" size="mini" type="primary" @click="publishTaxonomy(item.id)">批准发布</a-button></article></div><h3>待仲裁冲突</h3><div class="governance-list"><article v-for="item in annotationConflicts" :key="item.id"><div><b>案例 {{ item.case.slice(0,8) }}</b><small>差异：{{ item.differing_fields.join('、') }}</small></div><a-tag color="red">待仲裁</a-tag><a-button size="mini" type="primary" @click="arbitrateConflict(item)">采用复核结论</a-button></article><a-empty v-if="!annotationConflicts.length" description="没有待仲裁冲突"/></div></section></div>
          </template>
          <template v-else-if="goldMode==='history'">
            <a-alert type="info">预检只校验当前项目文件映射、哈希、缺失项和冲突，不写业务数据；确认导入后也只生成候选，仍需人工审核。</a-alert>
            <div class="history-entry"><a-input v-model="historyForm.name" placeholder="历史包名称"/><a-input v-model="historyForm.requirement" placeholder="需求文件 ID（必填）"/><a-input v-model="historyForm.plan" placeholder="真实方案文件 ID（必填）"/><a-input v-model="historyForm.caseFile" placeholder="真实用例文件 ID（必填）"/><a-button type="primary" :loading="historyBusy" @click="preflightHistory">预检</a-button></div>
            <div v-if="historyPreflight" class="preflight-card"><b>{{ historyPreflight.ok?'预检通过':'预检未通过' }}</b><span>预计生成 {{ historyPreflight.estimated_candidates||0 }} 个候选</span><span>清单哈希 {{ String(historyPreflight.manifest_hash||'').slice(0,12) }}</span><a-button v-if="historyPreflight.ok" type="primary" :loading="historyBusy" @click="confirmHistory">人工确认导入</a-button><pre v-if="Array.isArray(historyPreflight.errors)&&historyPreflight.errors.length">{{ JSON.stringify(historyPreflight.errors,null,2) }}</pre></div>
            <div class="governance-list"><article v-for="item in historyImports" :key="item.id"><div><b>{{ item.name }}</b><small>{{ item.items.length }} 个文件 · {{ item.candidate_count }} 个候选 · sha {{ item.manifest_hash.slice(0,10) }}</small></div><a-tag color="green">{{ item.status }}</a-tag><a-button size="mini" type="primary" @click="startReplay(item.id)">启动隔离回放</a-button></article></div>
            <div class="governance-list replay-list"><article v-for="item in historyReplays" :key="item.id"><div><b>回放 {{ item.id.slice(0,8) }}</b><small>配置 sha {{ item.config_hash.slice(0,10) }} · 未判定 {{ Number(item.summary?.unresolved||0) }}</small></div><a-tag :color="item.status==='passed'?'green':item.status==='blocked'?'red':'orange'">{{ item.status }}</a-tag></article></div>
          </template>
          <template v-else>
          <h3 class="subheading">待沉淀的真实反馈</h3><div class="table-head"><span>信号</span><span>反馈内容</span><span>操作人</span><span>发生时间</span></div>
          <div v-if="feedbackEvents.length" class="rows"><article v-for="item in feedbackEvents" :key="item.id"><a-tag :color="signalColor(item.signal)">{{ signalText(item.signal) }}</a-tag><div><b>{{ feedbackSummary(item) }}</b><small>{{ item.reason_code || '未填写原因编码' }}</small></div><span>{{ feedbackActor(item) }}</span><time>{{ formatDate(item.created_at) }}</time></article></div>
          <a-empty v-else description="暂无反馈信号"/>
          </template>
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
    <a-modal v-model:visible="showAssetReviewModal" title="人工审核候选资产" width="760px" ok-text="提交审核" :ok-loading="assetReviewBusy" @ok="submitAssetReview">
      <a-spin :loading="assetReviewLoading" style="width:100%"><template v-if="activeGoldCase"><a-alert type="warning">系统推荐仅供参考。本次人工结论、分区、分类和证据快照会写入审计；复核通过后才可进入冻结版本。</a-alert><div class="asset-review-facts"><article><small>候选</small><b>{{ activeGoldCase.title }}</b></article><article><small>当前状态</small><b>{{ activeGoldCase.state }}</b></article><article><small>推荐分区</small><b>{{ activeGoldCase.recommended_split || '未推荐' }}</b></article><article><small>隐私级别</small><b>{{ activeGoldCase.privacy_level }}</b></article></div><a-form layout="vertical"><a-form-item label="审核轮次"><a-radio-group v-model="assetReviewForm.round"><a-radio value="primary">初标</a-radio><a-radio value="review">负责人复核</a-radio></a-radio-group></a-form-item><a-form-item label="结论"><a-select v-model="assetReviewForm.conclusion"><a-option value="accepted">通过</a-option><a-option value="needs_changes">修改后通过</a-option><a-option value="rejected">驳回</a-option></a-select></a-form-item><a-form-item label="最终分区"><a-select v-model="assetReviewForm.split"><a-option value="gold">主集</a-option><a-option value="regression">回归集</a-option><a-option value="fresh">新鲜集</a-option><a-option value="challenge">专项挑战集</a-option></a-select></a-form-item><a-form-item label="业务分类"><a-input v-model="assetReviewForm.category" placeholder="例如 vote_submit"/></a-form-item><a-form-item label="标签（逗号分隔）"><a-input v-model="assetReviewForm.tagsText"/></a-form-item><a-form-item label="审核理由" required><a-textarea v-model="assetReviewForm.comment" :max-length="500" show-word-limit/></a-form-item></a-form><details class="asset-evidence"><summary>查看输入、期望与证据</summary><pre>{{ JSON.stringify({input:activeGoldCase.input_snapshot,expected:activeGoldCase.expected_output,evidence:activeGoldCase.evidence},null,2) }}</pre></details></template></a-spin>
    </a-modal>
    <a-modal v-model:visible="showTaxonomyModal" title="新建业务分类版本" ok-text="创建草稿" @ok="createTaxonomy"><a-alert type="warning">分类和关键场景由测试负责人维护；创建后需送审，再由测试负责人批准发布。</a-alert><a-form layout="vertical" style="margin-top:12px"><a-form-item label="业务范围"><a-input v-model="taxonomyForm.scope_key" placeholder="sse-evote"/></a-form-item><a-form-item label="版本"><a-input v-model="taxonomyForm.version" placeholder="1.0.0"/></a-form-item><a-form-item label="分类（每行一项）"><a-textarea v-model="taxonomyForm.categories"/></a-form-item><a-form-item label="关键场景（每行一项）"><a-textarea v-model="taxonomyForm.scenarios"/></a-form-item></a-form></a-modal>
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
              <header><b>{{ taskTypeLabels[item.stage] || item.label }}</b><small>{{ pinCandidates(item.stage).length }} 个匹配版本</small></header>
              <a-select
                v-model="pins[item.stage]"
                allow-search
                allow-clear
                :placeholder="`选择${taskTypeLabels[item.stage] || item.label} Skill 及版本`"
                class="pin-select"
                :not-found-content="`没有声明为${taskTypeLabels[item.stage] || item.label}的可用 Skill`"
              >
                <a-option
                  v-for="skill in pinCandidates(item.stage)"
                  :key="skill.skill_version_id"
                  :value="skill.skill_version_id"
                  :label="`${skill.skill_name} ${skill.version}`"
                  :disabled="!skill.runnable"
                >
                  <div class="pin-option">
                    <div class="pin-option-name"><b>{{ skill.skill_name }}</b><a-tag v-if="!skill.runnable" size="small" color="gray">不可运行</a-tag></div>
                    <small class="pin-option-meta">{{ skill.version || '无版本' }}<template v-if="skill.package_sha256"> · sha {{ skill.package_sha256.slice(0,10) }}</template></small>
                  </div>
                </a-option>
              </a-select>
              <div v-if="pinnedSkill(item.stage)" class="pin-selected">
                <span>已选 {{ pinnedSkill(item.stage)!.skill_name }} · {{ pinnedSkill(item.stage)!.version }}</span>
                <code>sha {{ pinnedSkill(item.stage)!.package_sha256.slice(0,10) }}</code>
              </div>
            </section>
          </div>
          <p v-if="!pinsComplete" class="pin-warn">四个阶段都要选定一个<strong>可运行</strong>的 Skill 包才能进入下一步。没有合适的包时，请先到 Skill Hub 上传该阶段的版本（上传即可用，无需先激活）。</p>
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
          <a-form-item label="流程标识 workflow_id（可选）" extra="留空即由当前项目自动派生一个可读标识（如「fly:20261004-1330-a1b2」），无需手工编造或复制上下文 ID。也可以自己填一个便于人工核对的标识，例如「交易网关-回归-20261002-01」。">
            <a-input v-model="workflowForm.workflowId" placeholder="留空自动派生" allow-clear/>
          </a-form-item>
        </a-form>
      </template>
    </a-modal>
    <!-- 用例审查自进化向导：① 选跑完的审查项目 ② 上传已确认报告（预检）
         ③ AI 候选优化点逐条定夺 ④ 确认 Skill 并发起。
         四步而不是一屏：这四件事各有各的前置条件，挤在一屏会让"为什么按钮是灰的"变成一个谜。
         第 ③ 步可降级跳过（未配置 LLM 时），但**必须明示**降级，不静默走人工路径。 -->
    <a-modal
      v-model:visible="showReviewEvolutionModal"
      :title="reviewEvolutionTitle"
      :ok-text="reviewEvolutionOkText"
      :ok-loading="reviewEvolutionSubmitting || reviewEvolutionProposing || reviewEvolutionConfirming"
      :ok-button-props="{disabled: !reviewEvolutionCanAdvance}"
      :on-before-ok="onReviewEvolutionOk"
      :mask-closable="false"
      width="820px"
    >
      <template v-if="reviewEvolutionStep===1">
        <a-alert type="info">自进化只作用于<strong>能力能被 Skill 直接迭代升级</strong>的单次能力：从选中的那次审查所用的 Skill 包上<strong>派生一个新候选版本</strong>，不会改动当前活跃版本。</a-alert>
        <a-spin :loading="reviewEvolutionLoading" style="width:100%">
          <div class="review-list">
            <button
              v-for="item in reviewEvolutionItems"
              :key="item.review_id"
              type="button"
              :class="['review-item',{selected:reviewEvolutionSelected===item.review_id,blocked:!item.evolvable}]"
              @click="reviewEvolutionSelected=item.review_id"
            >
              <div class="review-item-head">
                <b>{{ item.source_name }}</b>
                <a-tag :color="item.evolvable ? 'green' : 'gray'" size="small">{{ item.evolvable ? '可进化' : '暂不可进化' }}</a-tag>
              </div>
              <div class="review-item-meta">
                <span>{{ item.skill_name }} · {{ item.skill_version || '未锁定版本' }}</span>
                <span v-if="item.package_sha256" class="sha">sha {{ item.package_sha256.slice(0,12) }}</span>
                <span>完成 {{ formatDate(item.completed_at) }}</span>
                <span v-if="item.issues_count!==null && item.issues_count!==undefined">问题 {{ item.issues_count }} 条</span>
                <span v-if="item.derived_candidates.length">已派生 {{ item.derived_candidates.length }} 个候选</span>
              </div>
              <p v-for="text in item.blockers" :key="text" class="review-blocker">⚠ {{ text }}</p>
            </button>
            <a-empty v-if="!reviewEvolutionLoading && !reviewEvolutionItems.length" description="当前项目还没有已跑完的用例审查；先跑一次审查再回来"/>
          </div>
        </a-spin>
      </template>

      <template v-else-if="reviewEvolutionStep===2">
        <div class="review-target">
          <b>{{ selectedReview?.source_name }}</b>
          <span>{{ selectedReview?.skill_name }} · {{ selectedReview?.skill_version }}</span>
          <span v-if="selectedReview?.package_sha256" class="sha">sha {{ selectedReview.package_sha256.slice(0,12) }}</span>
        </div>
        <a-form layout="vertical" style="margin-top:12px">
          <a-form-item
            label="已确认的审查报告（平台导出的 xlsx）"
            required
            extra="在报告「问题明细」页填好「问题确认」「问题描述」「修改点」「不采纳原因」后再传回来。平台按列名读取，调整列序不影响解析。"
          >
            <a-upload
              :auto-upload="false"
              :limit="1"
              accept=".xlsx"
              :show-retry-button="false"
              :file-list="reviewEvolutionFileList"
              @change="onReviewReportChange"
              @before-remove="onReviewReportRemove"
            />
          </a-form-item>
        </a-form>
        <a-button long :loading="reviewEvolutionPreflighting" :disabled="!reviewEvolutionFile" @click="runReviewEvolutionPreflight">
          <template #icon><icon-experiment/></template>重新解析报告
        </a-button>

        <!-- 预检结果：把"能不能发起"的所有条件一次性摊开。只报一条，
             用户要来回试三次才知道真正卡在哪。 -->
        <div v-if="preflightReady || preflightBlockers.length" class="preflight">
          <div class="preflight-scan">
            <span><b>{{ reviewEvolutionPreflight?.scan.acceptance_score ?? 0 }}%</b><small>报告采纳率</small></span>
            <span><b>{{ reviewEvolutionPreflight?.scan.total_rows || 0 }}</b><small>问题行</small></span>
            <span><b>{{ reviewEvolutionPreflight?.scan.affirmative || 0 }}</b><small>人工确认</small></span>
            <span><b>{{ reviewEvolutionPreflight?.scan.negative || 0 }}</b><small>误报</small></span>
            <span><b>{{ reviewEvolutionPreflight?.scan.rewritten || 0 }}</b><small>说明被改写</small></span>
            <span><b>{{ reviewEvolutionPreflight?.scan.unconfirmed || 0 }}</b><small>未确认</small></span>
            <span><b>{{ reviewEvolutionPreflight?.scan.defect_total || 0 }}</b><small>可修复缺陷</small></span>
          </div>
          <article v-for="item in reviewEvolutionPreflight?.scan.defects || []" :key="`p-${item.category}:${item.issue_type}`" class="preflight-defect">
            <a-tag :color="item.category==='prompt_error' ? 'orange' : 'blue'" size="small">{{ attributionCategoryText(item.category) }}</a-tag>
            <b>{{ item.issue_type }}</b><span>{{ item.count }} 条</span>
          </article>
          <p v-for="text in reviewEvolutionPreflight?.scan.warnings || []" :key="text" class="warn-line">⚠ {{ text }}</p>
          <p v-for="text in preflightBlockers" :key="text" class="warn-line">✕ {{ text }}</p>
        </div>
      </template>

      <template v-else-if="reviewEvolutionStep===3">
        <!-- 第 ③ 步：AI 读四要素提候选优化点，人逐条定夺。
             把这步单列而不是并进"上传报告"里：AI 给的是**假设**，人要能看到
             "它凭什么这么猜"（置信度 / 类别 / 原话）再决定采纳，合并等于默认接受。 -->
        <div class="review-target">
          <b>{{ selectedReview?.source_name }}</b>
          <span>{{ selectedReview?.skill_name }} · {{ selectedReview?.skill_version }}</span>
          <span v-if="selectedReview?.package_sha256" class="sha">sha {{ selectedReview.package_sha256.slice(0,12) }}</span>
        </div>

        <!-- 降级必须**明说**：静默降级会让人以为"AI 看过了、没问题"，
             而真相是这一步根本没跑。 -->
        <a-alert v-if="reviewEvolutionDegraded" type="warning" style="margin-top:12px">
          未配置 LLM，已降级为人工标注：跳过 AI 候选生成，直接按报告里人工写下的结论派生。
          <template v-if="reviewEvolutionDegradedDetail">（{{ reviewEvolutionDegradedDetail }}）</template>
        </a-alert>

        <template v-else>
          <div class="proposal-head">
            <span>候选优化点 <b>{{ reviewEvolutionCandidates.length }}</b> 条</span>
            <span v-if="reviewEvolutionProposalPackage" class="sha">
              读的是 {{ reviewEvolutionProposalPackage.skill_name }} · {{ reviewEvolutionProposalPackage.version }}
              （{{ reviewEvolutionProposalPackage.files.length }} 个文件）
            </span>
            <a-button size="mini" :loading="reviewEvolutionProposing" @click="runReviewProposal">
              {{ reviewEvolutionCandidates.length ? '重新生成' : '生成候选优化点' }}
            </a-button>
          </div>

          <div v-if="reviewEvolutionCandidates.length" class="candidate-list">
            <article v-for="item in reviewEvolutionCandidates" :key="item.attribution_id" :class="['candidate-item',reviewEvolutionDecisions[item.attribution_id]?.action||'']">
              <header>
                <a-tag size="small" color="orange">{{ attributionCategoryText(item.category) }}</a-tag>
                <b>{{ item.issue_type || '未命名问题类型' }}</b>
                <span>置信度 {{ Math.round((item.confidence||0)*100) }}%</span>
                <a-tag v-if="reviewEvolutionDecisions[item.attribution_id]?.action==='accept'" size="small" color="green">已采纳</a-tag>
                <a-tag v-else-if="reviewEvolutionDecisions[item.attribution_id]?.action==='edit'" size="small" color="blue">已改写</a-tag>
                <a-tag v-else-if="reviewEvolutionDecisions[item.attribution_id]?.action==='reject'" size="small" color="gray">已驳回</a-tag>
              </header>
              <p class="candidate-hypothesis">{{ item.hypothesis }}</p>
              <!-- 改写要能在原话上改，而不是让人对着空白框重写。
                   给候选原文做初始值，改完提交的是这一条的新措辞。 -->
              <a-textarea
                v-if="reviewEvolutionDecisions[item.attribution_id]?.action==='edit'"
                v-model="reviewEvolutionDecisions[item.attribution_id]!.hypothesis"
                :auto-size="{minRows:2,maxRows:5}"
                placeholder="改写这条优化点"
              />
              <div class="candidate-actions">
                <a-button size="mini" status="success" @click="decideCandidate(item,'accept')">采纳</a-button>
                <a-button size="mini" @click="decideCandidate(item,'edit')">改写</a-button>
                <a-button size="mini" status="danger" @click="decideCandidate(item,'reject')">驳回</a-button>
              </div>
            </article>
          </div>
          <p v-else-if="!reviewEvolutionProposing" class="detail-note">
            还没有候选优化点。点「生成候选优化点」让 AI 读当前 Skill 包、本轮报告缺陷与历史归因提一轮假设；
            生成后逐条「采纳 / 改写 / 驳回」，只有采纳与改写的才会进入派生。
          </p>
          <p v-if="reviewEvolutionConfirmHint" class="warn-line">✕ {{ reviewEvolutionConfirmHint }}</p>
        </template>
      </template>

      <template v-else>
        <div class="pin-summary">
          <article>
            <b>Skill</b>
            <small>{{ selectedReview?.skill_name }}</small>
          </article>
          <article>
            <b>基线版本</b>
            <small>{{ selectedReview?.skill_version }}</small>
          </article>
          <article>
            <b>包哈希</b>
            <small class="sha">{{ (selectedReview?.package_sha256||'').slice(0,12) }}</small>
          </article>
          <article>
            <b>报告采纳率</b>
            <small>{{ reviewEvolutionPreflight?.scan.acceptance_score ?? 0 }}%</small>
          </article>
        </div>
        <!-- 派生的依据必须在这里说清楚：降级路径用的是"人工在报告里写的结论"，
             主路径用的是"人工确认过的 AI 候选"。两者产出的包不一样，混着说没人能复核。 -->
        <p class="detail-note" style="margin-top:12px">
          <template v-if="reviewEvolutionDegraded">本次走<strong>降级路径</strong>：以报告里人工写下的缺陷结论为派生依据。</template>
          <template v-else>本次派生依据：已确认的候选优化点 <strong>{{ reviewEvolutionConfirmedIds.length }}</strong> 条（已驳回 {{ reviewEvolutionRejectedCount }} 条不参与）。</template>
        </p>
        <a-alert type="warning" style="margin-top:12px">
          发起后会按这些结论，在<strong>基线的副本</strong>上生成新候选版本，
          并把每条结论写成新包 <code>SKILL.md</code> 里的受管护栏。候选是<strong>草稿</strong>：
          不会自动顶掉正在使用的版本。
        </a-alert>
        <p class="detail-note">
          若这次审查用的版本已经不是当前生效的活跃版本，派生目标就是错的，
          平台会直接拒绝而不是照旧派生。
        </p>
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
    <a-modal v-model:visible="showStageOutputModal" title="阶段结果" :footer="false" width="820px">
      <a-spin :loading="stageOutputLoading" style="width:100%">
        <template v-if="stageOutput">
          <div class="output-meta">
            <span><b>{{ stageOutput.stage_label }}</b> · 任务 {{ stageOutput.task_id }}</span>
            <span v-if="stageOutput.skill_name">{{ stageOutput.skill_name }}<template v-if="stageOutput.skill_version"> · {{ stageOutput.skill_version }}</template></span>
            <span v-if="stageOutput.package_sha256" class="sha">sha {{ stageOutput.package_sha256.slice(0,12) }}</span>
            <span>{{ formatDate(stageOutput.created_at) }}</span>
            <a-button size="mini" :loading="stageArtifactBusy===`modal:${stageOutput.output_id}`" @click="downloadStageReportByOutput(stageOutput)">
              <template #icon><icon-download/></template>下载报告
            </a-button>
          </div>
          <div v-if="stageOutput.gate" class="output-gate">
            <a-tag :color="gateColor(stageOutput.gate.status)">{{ gateText(stageOutput.gate.status) }}</a-tag>
            <span>{{ stageOutput.gate.reason || '门禁暂无说明' }}</span>
            <span v-if="stageOutput.gate.decided_by">操作人 {{ stageOutput.gate.decided_by }}</span>
          </div>
          <pre class="output-content">{{ stageOutput.content || '（本阶段产出正文为空）' }}</pre>
          <p v-if="stageOutput.truncated" class="detail-note">正文共 {{ stageOutput.content_length }} 字，此处只展示前 4000 字。</p>

          <!-- 采纳率按版本横向列出：单看一个当前值回答不了"这一版比上一版好了没有"，
               而 skill 是一点点优化出来的，这个趋势才是采纳率的用处。 -->
          <section v-if="stageOutput.acceptance_history?.length" class="acceptance-strip">
            <h4>采纳率 · 按版本对照</h4>
            <div class="acceptance-bars">
              <div v-for="item in [...stageOutput.acceptance_history].reverse()" :key="item.version_id || item.at" class="acceptance-col">
                <b>{{ item.score }}%</b>
                <i :style="{height:`${Math.max(6,Math.min(100,item.score))}%`}"/>
                <small>{{ item.version || '未命名版本' }}</small>
              </div>
            </div>
            <p class="detail-note">采纳率是版本间对比的评分维度，不是上传门槛；低于参考线的版本照常入库。</p>
          </section>

          <!-- 内容来源（T10）：通道实况 + 引用条目 + Skill 包摘要。
               与"闭环走到哪一步"分开呈现——把两者合成一段，会让人把"链路已闭环"
               误读成"内容已被验证"，而后者才是这份产出能不能被信任的关键。 -->
          <section class="sources-panel">
            <div class="section-head"><div><span>内容来源</span><h4>这份产出参考了什么</h4></div>
              <a-button size="mini" :loading="lineageLoading" @click="loadLineageForOutput(stageOutput.output_id)">{{ lineage ? '重新读取' : '读取来源' }}</a-button>
            </div>
            <template v-if="lineage">
              <div class="source-channel-row">
                <a-tag v-for="(detail,key) in lineage.sources.channels" :key="key" :color="channelColor(detail)">
                  {{ channelLabel(key) }} · {{ channelSummary(detail) }}
                </a-tag>
                <small v-if="!Object.keys(lineage.sources.channels).length">该产出未记录检索通道实况</small>
              </div>
              <div v-if="lineage.sources.citations.length" class="citation-list">
                <article v-for="cite in lineage.sources.citations" :key="cite.citation_id">
                  <a-tag size="small" color="arcoblue">{{ sourceTypeText(cite.source_type) }}</a-tag>
                  <div><b>{{ cite.title || cite.source_id || cite.citation_id }}</b>
                    <small v-if="cite.document_id">文档 {{ cite.document_id }}<template v-if="cite.chunk_index!==null"> · 分块 {{ cite.chunk_index }}</template></small>
                    <small v-else-if="cite.node_id">节点 {{ cite.node_id }}</small>
                  </div>
                  <span v-if="cite.rank">#{{ cite.rank }}</span>
                  <!-- 有节点 id 才能跳。跳不了的条目给不出链接就不给——一个点了没反应的
                       链接比一句"该引用没有可跳转的节点"更让人困惑。 -->
                  <a-button v-if="cite.node_id" size="mini" @click="jumpToGraphNode(cite.node_id)">看图谱</a-button>
                </article>
              </div>
              <p v-else class="detail-note">该产出没有记录引用条目。</p>
              <div v-if="lineage.sources.graph_nodes.length" class="graph-node-row">
                <span>涉及图谱节点：</span>
                <button v-for="node in lineage.sources.graph_nodes" :key="node.node_id" type="button" :class="['node-chip',{unresolved:!node.resolved}]" @click="jumpToGraphNode(node.node_id)">
                  {{ node.resolved ? `${node.label || node.node_id}` : `${node.node_id.slice(0,8)}（节点已清理）` }}
                </button>
              </div>
              <div class="skill-content-note">
                <b>Skill 包摘要</b>
                <small v-if="lineage.sources.skill_content.skill_version_id">
                  {{ lineage.sources.skill_content.version || '未命名版本' }} · sha {{ (lineage.sources.skill_content.package_sha256||'').slice(0,12) }}
                  · {{ lineage.sources.skill_content.files.length }} 个文本文件<template v-if="lineage.sources.skill_content.truncated">（已截断）</template>
                </small>
                <small v-else>该产出没有绑定 Skill 版本，无从追溯包内容</small>
                <ul>
                  <li v-for="file in lineage.sources.skill_content.files.slice(0,8)" :key="file.path">
                    <code>{{ file.path }}</code><span>sha {{ (file.sha256||'').slice(0,10) }}</span>
                  </li>
                </ul>
              </div>
            </template>
            <p v-else class="detail-note">尚未读取；点「读取来源」查看这份产出的检索通道、引用条目与 Skill 包摘要。</p>
          </section>
        </template>
      </a-spin>
    </a-modal>

    <!-- 「上传反馈」的取文件入口。用隐藏 input 而不是 a-upload：反馈是**就地**动作、
         不弹窗，往四张卡片里各塞一个完整上传组件会把卡片挤乱。 -->
    <input ref="stageFeedbackInput" type="file" accept=".xlsx,.xlsm" class="hidden-file-input" @change="onStageFeedbackPicked"/>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Message } from '@arco-design/web-vue';
import type { FileItem } from '@arco-design/web-vue/es/upload/interfaces';
import { IconBranch, IconDashboard, IconDownload, IconEdit, IconExperiment, IconFile, IconMessage, IconPlayArrow, IconPlus, IconRefresh, IconRight, IconRobot, IconSafe, IconStorage, IconUpload, IconUser } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { SkillHubConsole, SkillManager } from '@/features/skills';
import KnowledgeGraphView from '@/features/knowledge-graph/KnowledgeGraphView.vue';
import { WORKFLOW_STAGES as DEFAULT_WORKFLOW_STAGES } from '@/features/skills/utils/stages';
import { annotateGoldCase, confirmWorkflowStage, createEvaluationRun, createEvaluationSuite, createTestAssetTaxonomy, confirmHistoryImport, downloadSkillPackage, downloadStageArtifact, evaluateWorkflowStage, evolveCaseReview, executeWorkflowStage, freezeGoldDatasetVersion, generateCandidatesFromRun, getAssetCandidateStats, getGenerationOutputLineage, getGoldCase, getProjectQualityCockpit, getStageOutput, getWorkflowStageCatalog, getWorkflowStatus, listAnnotationConflicts, listAssetCandidateEvents, listCapabilityReleases, listCaseReviewEvolutionCandidates, listEvaluationResults, listEvaluationRuns, listEvaluationSuites, listExecutionSpans, listFailureAttributions, listFeedbackEvents, listGoldDatasets, listGoldDatasetVersions, listHistoryImports, listHistoryReplays, listKnowledgeCandidates, listOptimizationProposals, listRetrievalTraces, listTestAssetTaxonomies, openFlywheelRun, overrideWorkflowStage, preflightCaseReviewEvolution, preflightHistoryImport, proposeCaseReviewOptimizations, publishTestAssetTaxonomy, confirmCaseReviewOptimizations, resolveAnnotationConflict, retryAssetCandidate, retryFailedAssetCandidates, scoreWorkflowStage, startHistoryReplay, startWorkflow, submitTestAssetTaxonomy, updateCandidateState, uploadStageFeedback } from './service';
import type { AnnotationConflict, AssetCandidateEvent, AssetCandidateStats, CapabilityRelease, CaseReviewEvolutionCandidate, CaseReviewEvolutionPreflight, CaseReviewEvolutionResult, EvaluationResult, EvaluationRun, EvaluationSuite, ExecutionSpan, FailureAttribution, FeedbackEvent, GoldCase, GoldDataset, GoldDatasetVersion, HistoryImportBatch, HistoryReplay, KnowledgeCandidate, OptimizationCandidate, OptimizationProposal, OptimizationProposalResult, OutputLineageView, ProjectQualityCockpit, ProjectQualityPerson, ProjectWorkflowView, RetrievalTrace, StageExecutionPlan, StageFeedbackResult, StageOutputView, StartWorkflowResult, TestAssetTaxonomy, WorkflowCatalogSkill, WorkflowStageCatalog, WorkflowStageGateView } from './types';

type Workspace='overview'|'single'|'workflow'|'gold'|'evaluation'|'attribution'|'optimization';
type PrimaryView='agents'|'data'|'graph';
type QuickMode='console'|'skills'|'hub';
const projectStore=useProjectStore(); const projectName=computed(()=>projectStore.currentProject?.name||'当前项目'); const workspace=ref<Workspace>('overview');
const route=useRoute(),router=useRouter();
const routeView=():PrimaryView=>['agents','data','graph'].includes(String(route.query.view))?String(route.query.view) as PrimaryView:'agents';
const primaryView=ref<PrimaryView>(routeView()),quickMode=ref<QuickMode>('console');
/** 横幅抬头（h1 + 描述）。
 *  ⚠️ 数据飞轮下**按页签分别展示**，不是三个页签共用一句「数据飞轮」——
 *     用户 2026-10-03 明确：共用的横幅看不出当前切到了哪个页签。
 *  描述沿用各子组件原本的抬头文案（它们的 header 已按 `embedded` 隐去），
 *  信息既不丢、也不再出现"横幅写一遍 + 内容区再写一遍"的重复。 */
const pageHeader=computed(()=>{
  if(primaryView.value==='graph')return {title:'知识图谱',description:'统一沉淀代码、文档与测试知识，为影响分析和 LLM 检索提供可追踪语义关系。'};
  if(primaryView.value==='data'){
    if(quickMode.value==='skills')return {title:'Skill 进化工坊',description:'让 Skill 进化：候选版本、评测门禁与发布治理。'};
    if(quickMode.value==='hub')return {title:'Skill Hub',description:'统一管理平台 Skill 的导入、分类、版本迭代与使用状态，为各测试阶段提供可追溯的标准化能力。'};
    return {title:'数据飞轮',description:'沉淀反馈、评测、归因与优化数据。'};
  }
  return {title:'Agent总览',description:'查看运行、评测、失败样本与待处理改进。'};
});
watch(()=>route.query.view,()=>{primaryView.value=routeView();workspace.value='overview'});
async function openData(target:Workspace){primaryView.value='data';quickMode.value='console';await router.replace({path:'/knowledge-evolution',query:{view:'data'}});workspace.value=target}
const suites=ref<EvaluationSuite[]>([]),runs=ref<EvaluationRun[]>([]),results=ref<EvaluationResult[]>([]),feedbackEvents=ref<FeedbackEvent[]>([]),candidates=ref<KnowledgeCandidate[]>([]),allTraces=ref<RetrievalTrace[]>([]);
const goldDatasets=ref<GoldDataset[]>([]),selectedSpans=ref<ExecutionSpan[]>([]),attributions=ref<FailureAttribution[]>([]),proposals=ref<OptimizationProposal[]>([]),releases=ref<CapabilityRelease[]>([]);
const goldMode=ref<'review'|'datasets'|'history'|'feedback'>('review'),assetCandidateEvents=ref<AssetCandidateEvent[]>([]),assetCandidateStatus=ref(''),assetRetryId=ref(''),assetRetryBusy=ref(false);
const emptyAssetCandidateStats=():AssetCandidateStats=>({total:0,pending:0,processing:0,needs_review:0,completed:0,failed:0,dead_letter:0});
const assetCandidateStats=ref<AssetCandidateStats>(emptyAssetCandidateStats());
const showAssetReviewModal=ref(false),assetReviewLoading=ref(false),assetReviewBusy=ref(false),activeGoldCase=ref<GoldCase|null>(null);
const assetReviewForm=ref<{round:'primary'|'review';conclusion:'accepted'|'rejected'|'needs_changes';split:string;category:string;tagsText:string;comment:string}>({round:'primary',conclusion:'accepted',split:'regression',category:'',tagsText:'',comment:''});
const goldVersions=ref<GoldDatasetVersion[]>([]),taxonomies=ref<TestAssetTaxonomy[]>([]),annotationConflicts=ref<AnnotationConflict[]>([]);
const showTaxonomyModal=ref(false),taxonomyForm=ref({scope_key:'sse-evote',version:'1.0.0',categories:'',scenarios:''});
const historyImports=ref<HistoryImportBatch[]>([]),historyReplays=ref<HistoryReplay[]>([]),historyBusy=ref(false),historyPreflight=ref<Record<string,any>|null>(null);
const historyForm=ref({name:'',requirement:'',plan:'',caseFile:''});
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
/** 标题里的链路说明取自后端给的默认序列；后端没给时才回落到展示层那份同名副本，
 *  不在本文件里再抄第三份阶段名——抄出来的那份会在口径变更后静默变成错的。 */
const workflowChainText=computed(()=>{
  const order=cockpit.value.stage_order?.length?cockpit.value.stage_order:DEFAULT_WORKFLOW_STAGES;
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
/**
 * 上下文有效期的人读文本（T02）。
 *
 * 只精确到分钟：上下文是一次临时凭据，"有效至 14:03:27.123"级别的精度既没有
 * 决策价值，又会让人以为它是个需要精确对齐的时间点。显示不出来就如实说"未知"，
 * 不猜一个看起来像样的时间。
 */
const contextExpiryText=(iso:string)=>{
  if(!iso)return '未知';
  const date=new Date(iso);
  if(Number.isNaN(date.getTime()))return '未知';
  const pad=(n:number)=>String(n).padStart(2,'0');
  return `${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
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
    await loadCockpit();
    // T03 §4.2：派发成功后直接跳到后端给的业务页面。用户不必再手工复制
    // workflow_id / module_key —— 那样既容易抄错，也让"流程 ID 是可信凭据"这件事
    // 变成一句空话。URL 里只带一个 execution_context_id：流程、阶段、锁定的
    // SkillVersion 全部由服务端按这个 id 解析，前端带不过去也改不了。
    // 必须先 await loadCockpit()：跳转之后本页不再刷新，而阶段条上的
    // "已派发、等待执行"应该在上一步就已经落定，而不是等用户跳回来才补上。
    if(plan.launch_url){
      Message.success(`已派发本阶段，正在跳转到${plan.entry}`);
      router.push(plan.launch_url);
      return;
    }
    // 还没有对应业务页面的阶段：如实说明该去哪、带什么参数，而不是给一个
    // 点了没反应的按钮。平台只有测试执行有内部执行器，其余阶段由 agent 提交产出。
    if(plan.channel==='platform')Message.info(`已校验前置阶段并下发执行参数：请到「${plan.entry}」选用例套件执行，workflow_id 填 ${plan.workflow_id}`);
    else Message.info(`已派发本阶段（${plan.stage_label}）：请到「${plan.entry}」以 module_key = ${plan.module_key} 执行，产出回写后本阶段自动亮起`);
  }catch{Message.error('派发本阶段失败：请确认上一阶段已放行，或该流程已启用质量飞轮')}
  finally{executeBusy.value=''}
}
async function openStageOutput(workflowId:string,stage:string){
  if(!projectStore.currentProjectId)return;
  showStageOutputModal.value=true;stageOutputLoading.value=true;stageOutput.value=null;
  // 换了产出就把上一次的来源清掉：留着它会让 B 的页面显示 A 的引用条目。
  lineage.value=null;
  try{
    stageOutput.value=await getStageOutput(projectStore.currentProjectId,workflowId,stage);
    // 顺手把来源预取回来：这一块在弹窗底部，等用户滚下去再点一次「读取来源」才出现，
    // 等于把"这份产出参考了什么"藏在一个没人会点的按钮后面。
    const outputId=stageOutput.value?.output_id;
    if(outputId)void loadLineageForOutput(outputId,true);
  }
  catch(error:any){Message.error(errorText(error,'读取阶段结果失败'))}
  finally{stageOutputLoading.value=false}
}
// ---- 阶段报告出口 / 反馈入口（R1/R2）：两个动作都**挂在产出上**，没有产出就没有"这一版"
const stageArtifactBusy=ref(''),stageFeedbackBusy=ref('');
const stageFeedback=ref<Record<string,StageFeedbackResult>>({});
/** 采纳率就地回显的读法：按 `workflowId:stage` 取，避免四张卡片互相串台。 */
const stageFeedbackOf=(workflowId:string,stage:string)=>stageFeedback.value[`${workflowId}:${stage}`];
async function downloadStageReport(workflowId:string,stage:string){
  const flow=cockpit.value.workflows.find(v=>v.workflow_id===workflowId);
  const step=flow?.stages.find(v=>v.stage===stage);
  await downloadStageReportByOutput({workflow_id:workflowId,stage,output_id:step?.output_id||''});
}
/** 弹窗里下载时只有产出 id，没有 workflow_id；stage 由当前产出自己带上。 */
async function downloadStageReportByOutput(view:{workflow_id:string;stage:string;output_id:string}){
  if(!projectStore.currentProjectId||!view.output_id)return;
  const key=view.workflow_id?`${view.workflow_id}:${view.stage}`:`modal:${view.output_id}`;
  stageArtifactBusy.value=key;
  try{
    const filename=await downloadStageArtifact(projectStore.currentProjectId,view.workflow_id,view.stage);
    Message.success(`已下载 ${filename}`);
  }catch(error:any){
    // 后端的"暂无产出可下载"是业务拒绝（400），不是服务故障——原文照显才查得下去。
    Message.error(errorText(error,'下载阶段报告失败'));
  }finally{stageArtifactBusy.value=''}
}
const stageFeedbackInput=ref<HTMLInputElement|null>(null);
const stageFeedbackTarget=ref<{workflowId:string;stage:string}|null>(null);
function pickStageFeedback(workflowId:string,stage:string){
  stageFeedbackTarget.value={workflowId,stage};
  // 同一个 input 反复选同一份文件不会触发 change，必须先清空 value。
  if(stageFeedbackInput.value){stageFeedbackInput.value.value='';stageFeedbackInput.value.click()}
}
async function onStageFeedbackPicked(){
  const input=stageFeedbackInput.value,target=stageFeedbackTarget.value,file=input?.files?.[0];
  if(!projectStore.currentProjectId||!input||!target||!file)return;
  const key=`${target.workflowId}:${target.stage}`;
  stageFeedbackBusy.value=key;
  try{
    const result=await uploadStageFeedback(projectStore.currentProjectId,target.workflowId,target.stage,file);
    stageFeedback.value={...stageFeedback.value,[key]:result};
    // 低于参考线只提示、不改结论：skill 是一点点优化出来的，把它做成硬阻断
    // 等于要求每个中间版本一次跨过同一条线。
    if(result.created===false)Message.info(`已记录过同一份报告，采纳率 ${result.acceptance_score}%`);
    else if(result.below_reference)Message.warning(`已记录反馈：采纳率 ${result.acceptance_score}%，低于参考线 ${result.acceptance_reference}%（仅作版本对比，不影响记录）`);
    else Message.success(`已记录反馈：采纳率 ${result.acceptance_score}%`);
  }catch(error:any){
    Message.error(errorText(error,'上传阶段反馈失败'));
  }finally{
    stageFeedbackBusy.value='';
    if(input.value)input.value='';
    stageFeedbackTarget.value=null;
  }
}
// ---- 内容来源（T10）：与"闭环走到哪一步"分开呈现，不合成一段
const lineage=ref<OutputLineageView|null>(null),lineageLoading=ref(false);
/** `silent`：打开结果时顺手预取。预取失败不该对着只想看结果正文的人弹错误提示。 */
async function loadLineageForOutput(outputId:string,silent=false){
  if(!outputId)return;
  lineageLoading.value=true;
  try{lineage.value=await getGenerationOutputLineage(outputId)}
  catch(error:any){lineage.value=null;if(!silent)Message.error(errorText(error,'读取产出内容来源失败'))}
  finally{lineageLoading.value=false}
}
/** 跳图谱：把 `focusNode` 交给图谱视图聚焦，而不是在这边拼一个自己都算不准的 URL。 */
const graphFocusNode=ref('');
function jumpToGraphNode(nodeId:string){
  if(!nodeId)return;
  graphFocusNode.value=nodeId;
  primaryView.value='graph';
  router.replace({path:'/knowledge-evolution',query:{view:'graph',node:nodeId}});
}
const sourceTypeText=(v:string)=>({graph:'图谱节点',document:'知识文档',requirement:'需求',test_case:'测试用例'} as Record<string,string>)[v]||'未标注来源';
const channelLabel=(v:string)=>({dense:'向量',sparse:'关键词',graph:'图谱',structured:'结构化',historical:'历史'} as Record<string,string>)[v]||v;
/** 通道实况一句话：说清"跑没跑、命中几条、为什么没跑"。只说"关闭"会让人去查配置。 */
function channelSummary(detail:unknown):string{
  if(!detail||typeof detail!=='object')return String(detail??'—');
  const item=detail as Record<string,unknown>;
  const enabled=item.enabled===true;
  const hits=item.hits??item.count;
  if(!enabled){
    const reason=(item.reason as string)||'';
    const reasonText=({policy_never:'策略关闭',not_applicable_task_type:'不适用该任务类型',direct_recall_confident:'直连检索已足够',disabled_by_config:'配置关闭',not_selected:'未选中'} as Record<string,string>)[reason]||reason||'未启用';
    return `未启用 · ${reasonText}`;
  }
  return typeof hits==='number'?`命中 ${hits} 条`:'已启用';
}
const channelColor=(detail:unknown)=>{
  if(!detail||typeof detail!=='object')return 'gray';
  return (detail as Record<string,unknown>).enabled===true?'green':'gray';
};
// ---- 全链路测试：发起流程（入口固定在 数据飞轮 → 控制台 → 全链路测试）
// 两步向导：① 逐阶段选 Skill 包（选完才能下一步）② 填 workflow_id 并发起。
const showWorkflowStartModal=ref(false),workflowStarting=ref(false),workflowStartResult=ref<StartWorkflowResult|null>(null);
const workflowForm=ref({workflowId:''});
const workflowStep=ref<1|2>(1),catalogLoading=ref(false);
const emptyCatalog=():WorkflowStageCatalog=>({stage_order:[],all_stage_order:[],stages:[],skills:[]});
const catalog=ref<WorkflowStageCatalog>(emptyCatalog());
/** 阶段 → 选定的 SkillVersion ID。这是发起动作的实质内容，会被原样送进 `pins`。 */
const pins=ref<Record<string,string>>({});
const versionById=(versionId:string):WorkflowCatalogSkill|undefined=>catalog.value.skills.find(v=>v.skill_version_id===versionId);
const pinnedSkill=(stage:string):WorkflowCatalogSkill|undefined=>versionById(pins.value[stage]||'');
/**
 * 每个下拉框只呈现声明为当前阶段的 Skill 版本，避免方案生成里混入执行、报告等类型。
 * 同一 Skill 的多个版本分别展示，由项目明确选择具体版本。
 */
function pinCandidates(stage:string):WorkflowCatalogSkill[]{
  return catalog.value.skills
    .filter(v=>v.declared_stage===stage)
    .slice()
    .sort((a,b)=>{
      if(a.runnable!==b.runnable)return a.runnable?-1:1;
      const byName=a.skill_name.localeCompare(b.skill_name);
      return byName||b.version.localeCompare(a.version,undefined,{numeric:true});
    });
}
/** 四阶段都选到了**可运行**的包才算选完。选了个锁不上的包等于没选——必须在这里拦住。 */
const pinsComplete=computed(()=>{
  const stages=catalog.value.stage_order;
  return stages.length>0&&stages.every(stage=>pinnedSkill(stage)?.runnable===true);
});
async function loadStageCatalog(){
  if(!projectStore.currentProjectId)return;
  catalogLoading.value=true;
  try{
    catalog.value=await getWorkflowStageCatalog(projectStore.currentProjectId);
    // 默认项取后端按 manifest 解析的包，不在前端自己挑一个"看起来最像"的：
    // 两处各写一套"哪个包管哪个阶段"，改一处就会出现默认项与实际锁定项不一致。
    const nextPins:Record<string,string>={};
    catalog.value.stages.forEach(item=>{
      nextPins[item.stage]=item.default?.skill_version_id||'';
    });
    pins.value=nextPins;
  }catch{Message.error('加载阶段候选 Skill 失败')}
  finally{catalogLoading.value=false}
}
async function openWorkflowStart(){
  // 默认留空：workflow_id 由后端按「质量飞轮」入口派生（T06）。预填一个建议值会让人
  // 以为"必须改点什么才能继续"，而这正是要消掉的"手工维护上下文 ID"负担。
  workflowForm.value={workflowId:''};
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
  if(!projectStore.currentProjectId)return false;
  let workflowId=workflowForm.value.workflowId.trim();
  workflowStarting.value=true;
  try{
    if(!workflowId){
      // 没填就由入口派生：页面不再要求用户先编一个流程标识（T06 / R4）。
      // 派生规则只在后端一处，跨入口汇入同一条链才成立。
      const opened=await openFlywheelRun({project:projectStore.currentProjectId,entry_type:'flywheel'});
      workflowId=opened.workflow_id;
    }
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
// ---- 用例审查自进化（T23 + T08）：独立能力面板 → 用例审查 → 发起流程
// 四步而不是一屏：选项目、传报告预检、定夺 AI 候选、确认 Skill 各有各的前置条件，
// 挤在一屏会让"按钮为什么是灰的"变成一个谜。
const showReviewEvolutionModal=ref(false),reviewEvolutionStep=ref<1|2|3|4>(1);
const reviewEvolutionLoading=ref(false),reviewEvolutionPreflighting=ref(false),reviewEvolutionSubmitting=ref(false),reviewEvolutionDownloading=ref(false);
const reviewEvolutionItems=ref<CaseReviewEvolutionCandidate[]>([]);
const reviewEvolutionThreshold=ref(70),reviewEvolutionSelected=ref('');
const reviewEvolutionFileList=ref<FileItem[]>([]);
const reviewEvolutionPreflight=ref<CaseReviewEvolutionPreflight|null>(null);
const reviewEvolutionResult=ref<CaseReviewEvolutionResult|null>(null);
const selectedReview=computed(()=>reviewEvolutionItems.value.find(v=>v.review_id===reviewEvolutionSelected.value));
const reviewEvolutionFile=computed<File|null>(()=>reviewEvolutionFileList.value[0]?.file||null);
const preflightBlockers=computed(()=>reviewEvolutionPreflight.value?.blockers||[]);
const preflightReady=computed(()=>reviewEvolutionPreflight.value?.ready===true);
// ---- 第 ③ 步：AI 候选优化点 + 人工逐条确认
const reviewEvolutionProposing=ref(false),reviewEvolutionConfirming=ref(false);
const reviewEvolutionCandidates=ref<OptimizationCandidate[]>([]);
const reviewEvolutionDegraded=ref(false),reviewEvolutionDegradedDetail=ref('');
const reviewEvolutionProposalPackage=ref<OptimizationProposalResult['package']|null>(null);
/** 逐条决定，按 attribution_id 存（不用下标：下标只在某一次响应里有意义）。 */
const reviewEvolutionDecisions=ref<Record<string,{action:'accept'|'edit'|'reject';hypothesis:string}>>({});
const reviewEvolutionConfirmedIds=ref<string[]>([]);
const reviewEvolutionConfirmHint=ref('');
const reviewEvolutionRejectedCount=computed(()=>Object.values(reviewEvolutionDecisions.value).filter(v=>v.action==='reject').length);
/** 有候选但一条都没定夺时不许下一步——那等于把 AI 的假设当结论直接送进派生。 */
const reviewEvolutionUndecidedCount=computed(()=>reviewEvolutionCandidates.value.filter(v=>!reviewEvolutionDecisions.value[v.attribution_id]).length);
const reviewEvolutionTitle=computed(()=>{
  if(reviewEvolutionStep.value===1)return '发起自进化 · 1/4 选择已跑完的审查项目';
  if(reviewEvolutionStep.value===2)return '发起自进化 · 2/4 上传已确认报告（预检）';
  if(reviewEvolutionStep.value===3)return '发起自进化 · 3/4 AI 候选优化点（人工逐条确认）';
  return '发起自进化 · 4/4 确认 Skill 并派生';
});
const reviewEvolutionOkText=computed(()=>reviewEvolutionStep.value===1?'下一步：上传已确认报告':reviewEvolutionStep.value===2?'下一步：查看 AI 优化建议':reviewEvolutionStep.value===3?'下一步：确认 Skill 版本':'发起自进化');
const reviewEvolutionCanAdvance=computed(()=>{
  if(reviewEvolutionStep.value===1)return !!reviewEvolutionSelected.value;
  // 第二步必须预检通过才放行：让"报告里没有可修复缺陷"这类结论在提交前就暴露，
  // 而不是等用户点完「发起」再吃一个 400。
  if(reviewEvolutionStep.value===2)return preflightReady.value;
  // 第三步：降级路径直接过；有候选时必须逐条定夺完。
  if(reviewEvolutionStep.value===3)return reviewEvolutionDegraded.value||!reviewEvolutionCandidates.value.length||reviewEvolutionUndecidedCount.value===0;
  return true;
});
/** diff 预览只取**第一个**被改的文本文件：派生通常只动 SKILL.md，
 *  全量渲染会把几百行 diff 糊满屏幕，反而没人看关键那几行。 */
const reviewEvolutionDiffText=computed(()=>{
  const diff=reviewEvolutionResult.value?.diff;
  const summary=diff?.summary||'';
  const first=(diff?.text_diffs||[]).find((v:any)=>typeof v.unified_diff==='string'&&v.unified_diff);
  if(!first)return summary;
  return `${summary}\n\n${(first as any).path}\n${(first as any).unified_diff}`;
});
/** 把 axios 错误翻成一句话。后端业务拒绝回的是 `{"detail": "..."}`，
 *  但字段级错误是对象，直接塞给 Message 会显示成 [object Object]。 */
function errorText(error:any,fallback:string):string{
  const detail=error?.response?.data?.detail;
  if(typeof detail==='string'&&detail)return detail;
  if(detail&&typeof detail==='object')return Object.values(detail).flat().join('；');
  return error?.message||fallback;
}
async function openReviewEvolution(){
  reviewEvolutionStep.value=1;
  reviewEvolutionSelected.value='';
  reviewEvolutionFileList.value=[];
  reviewEvolutionPreflight.value=null;
  reviewEvolutionResult.value=null;
  resetReviewProposal();
  showReviewEvolutionModal.value=true;
  await loadReviewEvolutionItems();
}
/** 清空第 ③ 步的候选与决定。换报告/换项目时必须清：留着上一轮的结论去派生，
 *  派生的依据就是另一份报告的东西。 */
function resetReviewProposal(){
  reviewEvolutionCandidates.value=[];
  reviewEvolutionDecisions.value={};
  reviewEvolutionDegraded.value=false;
  reviewEvolutionDegradedDetail.value='';
  reviewEvolutionProposalPackage.value=null;
  reviewEvolutionConfirmedIds.value=[];
  reviewEvolutionConfirmHint.value='';
}
async function loadReviewEvolutionItems(){
  if(!projectStore.currentProjectId)return;
  reviewEvolutionLoading.value=true;
  try{
    const data=await listCaseReviewEvolutionCandidates(projectStore.currentProjectId);
    reviewEvolutionItems.value=data.items;
    reviewEvolutionThreshold.value=data.threshold;
    // 默认选中第一条可进化的：人点「发起流程」就是要发起，
    // 让他在一堆不可进化的条目里自己找那一条是多余的。
    const first=reviewEvolutionItems.value.find(v=>v.evolvable);
    if(first)reviewEvolutionSelected.value=first.review_id;
  }catch(error:any){Message.error(errorText(error,'加载用例审查项目失败'))}
  finally{reviewEvolutionLoading.value=false}
}
async function onReviewReportChange(fileList:FileItem[]){
  reviewEvolutionFileList.value=(fileList||[]).slice(-1);
  // 换了文件，之前的预检结论就不成立了——留着它会让人拿着 A 的解析结果去提交 B。
  reviewEvolutionPreflight.value=null;
  // 同理，上一份报告生成的 AI 候选也不能留：它读的是那份报告的缺陷。
  resetReviewProposal();
  if(reviewEvolutionFile.value)await runReviewEvolutionPreflight();
}
function onReviewReportRemove(){reviewEvolutionPreflight.value=null;resetReviewProposal();return true}
// 换审查项目同样要清：候选与决定都是绑在"某一次审查的产出"上的。
watch(reviewEvolutionSelected,()=>{
  reviewEvolutionPreflight.value=null;
  reviewEvolutionFileList.value=[];
  resetReviewProposal();
});
async function runReviewEvolutionPreflight(){
  const file=reviewEvolutionFile.value;
  if(!projectStore.currentProjectId||!file||!reviewEvolutionSelected.value)return;
  reviewEvolutionPreflighting.value=true;
  try{
    reviewEvolutionPreflight.value=await preflightCaseReviewEvolution(
      projectStore.currentProjectId,reviewEvolutionSelected.value,file,
      reviewEvolutionThreshold.value,
    );
  }catch(error:any){
    reviewEvolutionPreflight.value=null;
    Message.error(errorText(error,'解析报告失败'));
  }finally{reviewEvolutionPreflighting.value=false}
}
async function onReviewEvolutionOk():Promise<boolean>{
  if(reviewEvolutionStep.value===1){
    if(!reviewEvolutionSelected.value){Message.warning('请选择一个已跑完的审查项目');return false}
    reviewEvolutionStep.value=2;return false;
  }
  if(reviewEvolutionStep.value===2){
    if(!reviewEvolutionReadyOrWarn())return false;
    // 进第 ③ 步就把候选拉下来：让"AI 想改什么"和"派生什么"两件事在时间上分开，
    // 用户才有机会在两屏之间想一遍。
    reviewEvolutionStep.value=3;
    await runReviewProposal();
    return false;
  }
  if(reviewEvolutionStep.value===3){
    if(!reviewEvolutionDegraded.value&&reviewEvolutionUndecidedCount.value>0){
      Message.warning(`还有 ${reviewEvolutionUndecidedCount.value} 条候选没有定夺；请逐条选择「采纳 / 改写 / 驳回」`);
      return false;
    }
    if(!(await confirmReviewDecisions()))return false;
    reviewEvolutionStep.value=4;return false;
  }
  return submitReviewEvolution();
}
function reviewEvolutionReadyOrWarn():boolean{
  if(preflightReady.value)return true;
  Message.warning('请先点「解析报告并预检」，预检通过后才能进入下一步');
  return false;
}
/** 第 ③ 步入口：生成候选。无 LLM 时后端回 `degraded=true`（不是错误），
 *  这里如实把降级告诉用户，并允许直接跳到第 ④ 步。 */
async function runReviewProposal(){
  const file=reviewEvolutionFile.value;
  if(!projectStore.currentProjectId||!file||!reviewEvolutionSelected.value)return;
  reviewEvolutionProposing.value=true;
  try{
    const result=await proposeCaseReviewOptimizations(projectStore.currentProjectId,reviewEvolutionSelected.value,file);
    reviewEvolutionDegraded.value=result.degraded===true;
    reviewEvolutionDegradedDetail.value=result.detail||'';
    reviewEvolutionCandidates.value=result.candidates||[];
    reviewEvolutionProposalPackage.value=result.package||null;
    // 重新生成会换掉候选 id，旧决定不能再留——它指向的条目已经不存在了。
    reviewEvolutionDecisions.value={};
    reviewEvolutionConfirmHint.value='';
    if(result.degraded)Message.warning('未配置 LLM，已降级为人工标注：将直接按报告里的结论派生');
    else if(!reviewEvolutionCandidates.value.length)Message.info('AI 没有给出候选优化点：报告里可能没有可归因到本 Skill 的问题');
  }catch(error:any){
    reviewEvolutionCandidates.value=[];
    reviewEvolutionProposalPackage.value=null;
    Message.error(errorText(error,'生成候选优化点失败'));
  }finally{reviewEvolutionProposing.value=false}
}
function decideCandidate(item:OptimizationCandidate,action:'accept'|'edit'|'reject'){
  const current=reviewEvolutionDecisions.value[item.attribution_id];
  // 再点一次同一个动作 = 取消决定，回到"未定夺"：误点后没有退路，人就只能刷新页面。
  if(current&&current.action===action){
    const next={...reviewEvolutionDecisions.value};delete next[item.attribution_id];
    reviewEvolutionDecisions.value=next;return;
  }
  reviewEvolutionDecisions.value={
    ...reviewEvolutionDecisions.value,
    [item.attribution_id]:{action,hypothesis:action==='edit'?(current?.hypothesis||item.hypothesis):item.hypothesis},
  };
}
/** 第 ③ → ④：把人工决定落库（accept/edit → confirmed，reject → rejected）。
 *  逐条返回错误不整批失败——一条 id 失效不该把其余已做完的确认全丢掉。 */
async function confirmReviewDecisions():Promise<boolean>{
  reviewEvolutionConfirmHint.value='';
  if(reviewEvolutionDegraded.value)return true;
  if(!projectStore.currentProjectId||!reviewEvolutionSelected.value)return false;
  const decisions=Object.entries(reviewEvolutionDecisions.value).map(([attribution_id,value])=>({
    attribution_id,action:value.action,
    ...(value.action==='edit'?{hypothesis:value.hypothesis}:{}),
  }));
  if(!decisions.length)return true;
  reviewEvolutionConfirming.value=true;
  try{
    const result=await confirmCaseReviewOptimizations(projectStore.currentProjectId,reviewEvolutionSelected.value,decisions);
    reviewEvolutionConfirmedIds.value=result.confirmed_ids||[];
    const failed=result.results.filter(v=>v.error);
    if(failed.length){
      reviewEvolutionConfirmHint.value=`${failed.length} 条未能落库（可能已被重新生成替换），请重新生成候选后再确认`;
      return false;
    }
    if(!reviewEvolutionConfirmedIds.value.length){
      Message.warning('没有采纳或改写的候选优化点；请至少保留一条，或重新生成候选');
      return false;
    }
    return true;
  }catch(error:any){
    Message.error(errorText(error,'确认候选优化点失败'));
    return false;
  }finally{reviewEvolutionConfirming.value=false}
}
async function submitReviewEvolution():Promise<boolean>{
  const file=reviewEvolutionFile.value;
  if(!projectStore.currentProjectId||!file||!reviewEvolutionSelected.value)return false;
  reviewEvolutionSubmitting.value=true;
  try{
    reviewEvolutionResult.value=await evolveCaseReview(
      projectStore.currentProjectId,reviewEvolutionSelected.value,file,
      {
        threshold:reviewEvolutionThreshold.value,
        // 主路径只拿人工确认过的候选当依据；降级路径不传，后端会回落到"采信报告里的结论"。
        attributionIds:reviewEvolutionDegraded.value?undefined:reviewEvolutionConfirmedIds.value,
      },
    );
    showReviewEvolutionModal.value=false;
    reviewEvolutionStep.value=1;
    resetReviewProposal();
    await loadCockpit();
    Message.success(`已派生候选版本 ${reviewEvolutionResult.value.candidate.version}（草稿，待评测与审批）`);
    return true;
  }catch(error:any){
    Message.error(errorText(error,'发起自进化失败'));
    return false;
  }finally{reviewEvolutionSubmitting.value=false}
}
async function downloadReviewCandidate(){
  const result=reviewEvolutionResult.value;
  if(!result)return;
  reviewEvolutionDownloading.value=true;
  try{
    const filename=await downloadSkillPackage(result.download_url);
    Message.success(`已下载 ${filename}`);
  }catch(error:any){Message.error(errorText(error,'下载 Skill 包失败'))}
  finally{reviewEvolutionDownloading.value=false}
}
const suiteForm=ref({name:'',description:'',suite_type:'regression',task_type:'code_review'}),candidateForm=ref({threshold:.5,minFailureCount:1});
const suiteTypeLabels:Record<string,string>={seed:'种子集',gold:'金标集',regression:'回归集',fresh:'新鲜集',challenge:'挑战集'};
/** 阶段中文名的**短标签**（质量飞轮页面专用）。四阶段的叫法按用户口径：
 *  方案生成 → 用例生成 → 测试执行 → 报告产出。
 *  Skill 进化工坊那边用同名的**完整**标签（`features/skills/utils/stages.ts`：
 *  测试方案生成 / 测试用例生成 / …），两边只是繁简不同，指的都是同一个 stage。
 *  ⚠️ 这里只负责"显示成什么"，不负责"哪四个阶段是链路"——那是后端 stage_order 的事。 */
const taskTypeLabels:Record<string,string>={case_review:'用例审查',code_review:'代码审查',knowledge_query:'知识库问答',risk_identification:'风险识别',test_plan_generation:'方案生成',testcase_generation:'用例生成',test_execution:'测试执行',report_generation:'报告产出',issue_tracking:'问题跟踪'};
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
type AgentMetricKey='sessions'|'users'|'tokens'|'latency'|'failed';
interface AgentKpi{key:AgentMetricKey;label:string;value:string;summaryLabel:string;hint:string;delta:number|null;spark:number[];tone:string;invert?:boolean}
const selectedAgentStages=ref<string[]>([]),selectedAgentMetric=ref<AgentMetricKey>('sessions');
const filteredAgentTraces=computed(()=>selectedAgentStages.value.length
  ?allTraces.value.filter(trace=>selectedAgentStages.value.includes(trace.task_type))
  :allTraces.value,
);
function traceLatencyMs(v:RetrievalTrace):number{const values=Object.entries(v.timings||{}).filter(([,n])=>typeof n==='number') as [string,number][];return values.reduce((sum,[,n])=>sum+n,0)}
function dayLabel(v:Date):string{return `${String(v.getMonth()+1).padStart(2,'0')}-${String(v.getDate()).padStart(2,'0')}`}
function formatLatency(ms:number):string{if(ms<=0)return'-';return ms>=1000?`${(ms/1000).toFixed(2)}s`:`${Math.round(ms)}ms`}
function formatCompact(v:number):string{return new Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(v)}
function sparkHeights(values:number[]):number[]{const max=Math.max(...values,1);return values.map(v=>v>0?Math.max(14,Math.round(v/max*100)):3)}
interface DailyBucket{sessions:number;tokens:number;users:Set<number>;latencySum:number;latencyCount:number;failed:number}
const agentDaily=computed(()=>{
  const days:string[]=[];const now=new Date();
  for(let i=TREND_DAYS-1;i>=0;i-=1){const d=new Date(now);d.setDate(now.getDate()-i);days.push(dayLabel(d))}
  const buckets=new Map<string,DailyBucket>();
  days.forEach(day=>buckets.set(day,{sessions:0,tokens:0,users:new Set<number>(),latencySum:0,latencyCount:0,failed:0}));
  filteredAgentTraces.value.forEach(trace=>{
    const slot=buckets.get(dayLabel(new Date(trace.created_at)));if(!slot)return;
    slot.sessions+=1;slot.tokens+=trace.token_usage||0;
    if(trace.status!=='completed')slot.failed+=1;
    if(trace.user!=null)slot.users.add(trace.user);
    const latency=traceLatencyMs(trace);if(latency>0){slot.latencySum+=latency;slot.latencyCount+=1}
  });
  return days.map(day=>({date:day,...buckets.get(day)!}));
});
const hasTrendData=computed(()=>agentDaily.value.some(d=>d.sessions>0||d.tokens>0));
/** 环比取「近 7 天 vs 前 7 天」。前一周没有数据时返回 null——不编一个 0%。 */
function weekDelta(pick:(list:RetrievalTrace[])=>number):number|null{
  const now=Date.now();
  const recent=filteredAgentTraces.value.filter(v=>now-new Date(v.created_at).getTime()<=7*DAY_MS);
  const previous=filteredAgentTraces.value.filter(v=>{const age=now-new Date(v.created_at).getTime();return age>7*DAY_MS&&age<=14*DAY_MS});
  const base=pick(previous);if(!base)return null;
  return Math.round((pick(recent)-base)/base*1000)/10;
}
const agentKpis=computed<AgentKpi[]>(()=>{
  const list=filteredAgentTraces.value;
  const sessionsOf=(items:RetrievalTrace[])=>items.length;
  const usersOf=(items:RetrievalTrace[])=>new Set(items.map(v=>v.user).filter((v):v is number=>v!=null)).size;
  const tokensOf=(items:RetrievalTrace[])=>items.reduce((sum,v)=>sum+(v.token_usage||0),0);
  const avgLatencyOf=(items:RetrievalTrace[])=>{const valid=items.map(traceLatencyMs).filter(v=>v>0);return valid.length?valid.reduce((a,b)=>a+b,0)/valid.length:0};
  const failRateOf=(items:RetrievalTrace[])=>items.length?items.filter(v=>v.status!=='completed').length/items.length*100:0;
  const tokens=tokensOf(list);
  return [
    {key:'sessions',label:'会话数',value:sessionsOf(list).toLocaleString(),summaryLabel:'筛选范围内总会话',hint:`${new Set(list.map(v=>v.task_id)).size} 个任务`,delta:weekDelta(sessionsOf),spark:sparkHeights(agentDaily.value.map(d=>d.sessions)),tone:''},
    {key:'users',label:'活跃用户',value:usersOf(list).toLocaleString(),summaryLabel:'筛选范围内去重用户',hint:'按轨迹用户去重',delta:weekDelta(usersOf),spark:sparkHeights(agentDaily.value.map(d=>d.users.size)),tone:''},
    {key:'tokens',label:'Token 消耗',value:tokens.toLocaleString(),summaryLabel:'筛选范围内 Token 总量',hint:tokens?'已回传用量':'调用未回传用量',delta:weekDelta(tokensOf),spark:sparkHeights(agentDaily.value.map(d=>d.tokens)),tone:'blue'},
    {key:'latency',label:'平均耗时',value:formatLatency(avgLatencyOf(list)),summaryLabel:'筛选范围内平均耗时',hint:`${list.filter(v=>traceLatencyMs(v)>0).length} 条有时长`,delta:weekDelta(avgLatencyOf),spark:sparkHeights(agentDaily.value.map(d=>d.latencyCount?d.latencySum/d.latencyCount:0)),tone:''},
    {key:'failed',label:'失败率',value:`${failRateOf(list).toFixed(1)}%`,summaryLabel:'筛选范围内失败率',hint:`${list.filter(v=>v.status!=='completed').length} 条未完成`,delta:weekDelta(failRateOf),spark:sparkHeights(agentDaily.value.map(d=>d.sessions?d.failed/d.sessions*100:0)),tone:'warn',invert:true},
  ];
});
const activeAgentKpi=computed(()=>agentKpis.value.find(item=>item.key===selectedAgentMetric.value)??agentKpis.value[0]);
const activeAgentTrend=computed(()=>{
  const raw=agentDaily.value.map(day=>{
    if(selectedAgentMetric.value==='sessions')return day.sessions;
    if(selectedAgentMetric.value==='users')return day.users.size;
    if(selectedAgentMetric.value==='tokens')return day.tokens;
    if(selectedAgentMetric.value==='latency')return day.latencyCount?day.latencySum/day.latencyCount:0;
    return day.sessions?day.failed/day.sessions*100:0;
  });
  const max=Math.max(...raw,1);
  return agentDaily.value.map((day,index)=>({
    date:day.date,
    pct:raw[index]>0?Math.max(4,Math.round(raw[index]/max*100)):1,
    label:selectedAgentMetric.value==='latency'?formatLatency(raw[index])
      :selectedAgentMetric.value==='failed'?`${raw[index].toFixed(1)}%`
      :selectedAgentMetric.value==='tokens'?formatCompact(raw[index])
      :raw[index].toLocaleString(),
  }));
});
/** Agent 台账：5 个阶段各一行，按会话数 / 用户数 / Token / 耗时 / 失败率聚合。 */
const agentRows=computed(()=>{
  const visibleStages=selectedAgentStages.value.length?AGENT_STAGE_ORDER.filter(stage=>selectedAgentStages.value.includes(stage)):AGENT_STAGE_ORDER;
  const rows=visibleStages.map(stage=>{
    const items=filteredAgentTraces.value.filter(v=>v.task_type===stage);
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
const assetCandidateStatusColor=(status:string)=>['completed','needs_review'].includes(status)?'green':status==='dead_letter'?'red':status==='failed'?'orange':status==='processing'?'blue':'gray';
const assetCandidateTitle=(item:AssetCandidateEvent)=>String(item.payload?.title||item.payload?.name||item.signal||'待解析候选');
const assetCandidatePreflight=(item:AssetCandidateEvent)=>{
  const value=item.preflight||{};
  if(value.privacy_blocked)return '隐私预检阻断，禁止进入优化资产';
  if(value.conflict)return '检测到预期冲突，需人工仲裁';
  if(value.duplicate)return '检测到重复资产，已执行合并预检';
  return item.candidate?'已生成候选，等待人工初标与复核':'等待完整性、隐私和去重预检';
};
async function loadAssetCandidates(){
  if(!projectStore.currentProjectId)return;
  try{[assetCandidateEvents.value,assetCandidateStats.value]=await Promise.all([listAssetCandidateEvents(projectStore.currentProjectId,assetCandidateStatus.value||undefined),getAssetCandidateStats(projectStore.currentProjectId)])}
  catch{Message.error('加载资产候选队列失败')}
}
async function retryOneAssetCandidate(id:string){assetRetryId.value=id;try{await retryAssetCandidate(id);await loadAssetCandidates();Message.success('候选事件已重新处理')}catch{Message.error('候选事件重试失败')}finally{assetRetryId.value=''}}
async function retryAllAssetCandidates(){if(!projectStore.currentProjectId)return;assetRetryBusy.value=true;try{const result=await retryFailedAssetCandidates(projectStore.currentProjectId);await loadAssetCandidates();Message.success(`已重试 ${result.retried} 条候选事件`)}catch{Message.error('批量重试失败')}finally{assetRetryBusy.value=false}}
async function openAssetReview(id:string){showAssetReviewModal.value=true;assetReviewLoading.value=true;activeGoldCase.value=null;try{const item=await getGoldCase(id);activeGoldCase.value=item;assetReviewForm.value={round:item.annotations.some(v=>v.round==='primary')?'review':'primary',conclusion:'accepted',split:item.recommended_split||item.split||'regression',category:'',tagsText:(item.recommended_tags||item.tags||[]).join(','),comment:''}}catch{Message.error('读取候选资产失败')}finally{assetReviewLoading.value=false}}
async function submitAssetReview(){const item=activeGoldCase.value;if(!item)return false;if(!assetReviewForm.value.comment.trim()){Message.warning('请填写审核理由');return false}assetReviewBusy.value=true;try{await annotateGoldCase(item.id,{round:assetReviewForm.value.round,answer:item.expected_output,evidence:item.evidence,conclusion:assetReviewForm.value.conclusion,comment:assetReviewForm.value.comment.trim(),tags:assetReviewForm.value.tagsText.split(',').map(v=>v.trim()).filter(Boolean),split:assetReviewForm.value.split,category:assetReviewForm.value.category.trim()});showAssetReviewModal.value=false;await loadAssetCandidates();Message.success(assetReviewForm.value.round==='review'?'负责人复核已记录':'初标已记录，仍需负责人复核');return true}catch{Message.error('提交人工审核失败');return false}finally{assetReviewBusy.value=false}}
async function loadDatasetGovernance(){if(!projectStore.currentProjectId)return;try{[taxonomies.value,annotationConflicts.value]=await Promise.all([listTestAssetTaxonomies(projectStore.currentProjectId),listAnnotationConflicts({state:'open'})]);if(!goldVersions.value.length&&goldDatasets.value[0])await selectGoldDataset(goldDatasets.value[0].id)}catch{Message.error('加载数据集治理信息失败')}}
async function selectGoldDataset(id:string){try{goldVersions.value=await listGoldDatasetVersions(id)}catch{Message.error('加载数据集版本失败')}}
async function freezeVersion(id:string){try{await freezeGoldDatasetVersion(id);await selectGoldDataset(goldVersions.value.find(v=>v.id===id)?.dataset||'');Message.success('数据集版本已冻结')}catch{Message.error('冻结失败：请检查双轮审核、冲突和关键场景覆盖')}}
async function createTaxonomy(){if(!projectStore.currentProjectId||!taxonomyForm.value.scope_key||!taxonomyForm.value.version){Message.warning('请填写业务范围和版本');return false}try{await createTestAssetTaxonomy({project:projectStore.currentProjectId,scope_key:taxonomyForm.value.scope_key,version:taxonomyForm.value.version,categories:taxonomyForm.value.categories.split('\n').map(v=>v.trim()).filter(Boolean),critical_scenarios:taxonomyForm.value.scenarios.split('\n').map(v=>v.trim()).filter(Boolean)});showTaxonomyModal.value=false;await loadDatasetGovernance();Message.success('分类草稿已创建');return true}catch{Message.error('创建分类版本失败');return false}}
async function submitTaxonomy(id:string){try{await submitTestAssetTaxonomy(id);await loadDatasetGovernance();Message.success('已送审')}catch{Message.error('送审失败')}}
async function publishTaxonomy(id:string){try{await publishTestAssetTaxonomy(id);await loadDatasetGovernance();Message.success('分类版本已批准发布')}catch{Message.error('批准发布失败')}}
async function arbitrateConflict(item:AnnotationConflict){const review=item.review_annotation;try{await resolveAnnotationConflict(item.id,{answer:review.answer,evidence:review.evidence,conclusion:review.conclusion,comment:'测试负责人仲裁：采用复核结论',tags:review.tags,split:review.split,category:review.category});await loadDatasetGovernance();Message.success('冲突已仲裁')}catch{Message.error('冲突仲裁失败')}}
async function loadHistoryWorkspace(){if(!projectStore.currentProjectId)return;try{[historyImports.value,historyReplays.value]=await Promise.all([listHistoryImports(projectStore.currentProjectId),listHistoryReplays(projectStore.currentProjectId)])}catch{Message.error('加载历史回放工作区失败')}}
async function preflightHistory(){if(!projectStore.currentProjectId)return;const form=historyForm.value;if(!form.requirement||!form.plan||!form.caseFile){Message.warning('需求、真实方案和真实用例文件均为必填');return}historyBusy.value=true;try{historyPreflight.value=await preflightHistoryImport(projectStore.currentProjectId,{name:form.name||'历史资料包',task_type:'testcase_generation',files:[{role:'requirement',file_id:Number(form.requirement)},{role:'plan',file_id:Number(form.plan)},{role:'case',file_id:Number(form.caseFile)}]})}catch{Message.error('历史包预检失败')}finally{historyBusy.value=false}}
async function confirmHistory(){if(!projectStore.currentProjectId||!historyPreflight.value?.confirmation_token)return;historyBusy.value=true;try{await confirmHistoryImport(projectStore.currentProjectId,String(historyPreflight.value.confirmation_token));historyPreflight.value=null;await loadHistoryWorkspace();await loadAssetCandidates();Message.success('历史包已导入，候选已进入人工审核队列')}catch{Message.error('历史包确认导入失败')}finally{historyBusy.value=false}}
async function startReplay(batch:string){if(!projectStore.currentProjectId)return;try{await startHistoryReplay({project:projectStore.currentProjectId,batch,workflow_id:`history-${batch}-${Date.now()}`,config:{mode:'structured_compare'}});await loadHistoryWorkspace();Message.success('隔离回放已启动')}catch{Message.error('启动历史回放失败')}}
async function loadCandidates(){if(!projectStore.currentProjectId)return;try{candidates.value=await listKnowledgeCandidates({project:projectStore.currentProjectId,state:candidateState.value||undefined})}catch{Message.error('加载改进候选失败')}}
async function loadTraces(){if(!projectStore.currentProjectId)return;try{allTraces.value=await listRetrievalTraces({project:projectStore.currentProjectId})}catch{Message.error('加载运行轨迹失败')}}
async function loadGovernance(){if(!projectStore.currentProjectId)return;try{[goldDatasets.value,attributions.value,proposals.value,releases.value]=await Promise.all([listGoldDatasets(projectStore.currentProjectId),listFailureAttributions(projectStore.currentProjectId),listOptimizationProposals(projectStore.currentProjectId),listCapabilityReleases({project:projectStore.currentProjectId})]);await loadAssetCandidates()}catch{Message.error('加载飞轮治理数据失败')}}
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
watch(()=>projectStore.currentProjectId,async id=>{
  assetCandidateEvents.value=[];assetCandidateStats.value=emptyAssetCandidateStats();activeGoldCase.value=null;
  goldVersions.value=[];taxonomies.value=[];annotationConflicts.value=[];historyImports.value=[];historyReplays.value=[];historyPreflight.value=null;
  selectedSuiteId.value=undefined;selectedRunId.value='';selectedTraceId.value='';
  if(id)await bootstrap();
},{immediate:true});
</script>

<style scoped>
.qe-page{height:100%;box-sizing:border-box;padding:20px;overflow:auto;color:var(--theme-page-text,var(--color-text-1));background:var(--theme-page-bg,var(--color-fill-1));--blue:#165dff;--navy:#102a43;--teal:#0f766e;--orange:#ff7d00}.empty-page{height:100%;display:grid;place-items:center}.hero{position:relative;display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding:22px 26px;overflow:hidden;border-radius:12px;color:#fff;background:linear-gradient(118deg,var(--navy),#176b87 62%,var(--teal));box-shadow:0 12px 30px rgb(16 42 67/16%)}.hero>div:first-child>span{font-size:10px;letter-spacing:.18em;color:#9fe1dd}.hero h1{margin:5px 0 3px;font-size:26px}.hero p{margin:0;color:#d8edf0}.hero-actions{z-index:1;display:flex;align-items:center;gap:8px}.hero-actions :deep(.arco-tag){color:#fff;background:rgb(255 255 255/12%);border-color:rgb(255 255 255/16%)}.tabs{display:flex;gap:4px;margin:14px 0;padding:5px;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.tabs button{display:flex;align-items:center;justify-content:center;gap:7px;min-width:130px;padding:10px 15px;border:0;border-radius:7px;color:var(--color-text-2);background:transparent;cursor:pointer}.tabs button:hover{background:var(--color-fill-2)}.tabs button.active{color:var(--blue);background:rgb(var(--arcoblue-1));font-weight:600}.tabs small{min-width:18px;padding:1px 5px;border-radius:10px;background:var(--color-fill-3)}main{min-height:620px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}.metrics article{position:relative;padding:17px 19px;overflow:hidden;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.metrics article:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--teal)}.metrics .warn:before{background:var(--orange)}.metrics .blue:before{background:var(--blue)}.metrics span,.metrics small{display:block;color:var(--color-text-3)}.metrics b{display:block;margin:5px 0 3px;font-size:25px}.panel{border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2);box-shadow:0 6px 18px rgb(29 33 41/4%)}.padded,.loop-panel,.content-panel,.suite-panel,.eval-panel{padding:18px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.section-head span{font-size:11px;letter-spacing:.08em;color:var(--color-text-3)}.section-head h2{margin:3px 0 0;font-size:17px}.loop{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.loop button{position:relative;display:flex;min-width:0;align-items:flex-start;gap:8px;padding:13px 10px 39px;text-align:left;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1);cursor:pointer}.loop button:hover{border-color:var(--blue);background:rgb(var(--arcoblue-1))}.loop em{font-size:10px;font-weight:700;color:var(--blue);font-style:normal}.loop button div{min-width:0}.loop b,.loop small{display:block}.loop small{margin-top:4px;overflow:hidden;color:var(--color-text-3);text-overflow:ellipsis;white-space:nowrap}.loop .arco-tag{position:absolute;right:8px;bottom:8px}.overview-grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(340px,1fr);gap:12px;margin-top:12px}.task-list{display:grid;gap:8px}.task-list button{display:grid;grid-template-columns:38px 1fr auto 14px;align-items:center;gap:11px;padding:12px;text-align:left;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1);cursor:pointer}.task-list button:hover{border-color:var(--blue)}.task-list i{display:grid;width:34px;height:34px;place-items:center;border-radius:7px;font-style:normal}.task-list i.blue{color:var(--blue);background:rgb(var(--arcoblue-1))}.task-list i.orange{color:var(--orange);background:rgb(var(--orange-1))}.task-list i.teal{color:var(--teal);background:rgb(var(--green-1))}.task-list b,.task-list small{display:block}.task-list small{margin-top:3px;color:var(--color-text-3)}.task-list strong{font-size:18px}.roles article{display:flex;align-items:center;gap:11px;margin-top:9px;padding:12px;border:1px solid var(--color-border-2);border-radius:8px}.roles article>svg{flex:none;font-size:22px;color:var(--blue)}.roles article:first-of-type>svg{color:var(--orange)}.roles article div{min-width:0;flex:1}.roles b,.roles small{display:block}.roles small{margin-top:3px;color:var(--color-text-3)}.roles .agent{background:var(--color-fill-1)}.roles .agent>span{font-size:11px;color:var(--color-text-3)}.roles .agent>span i{display:inline-block;width:6px;height:6px;margin-right:5px;border-radius:50%;background:#00b42a}.content-panel{min-height:570px}.toolbar{padding-bottom:14px;border-bottom:1px solid var(--color-border-2)}.actions{display:flex;gap:8px}.table-head,.rows article{display:grid;grid-template-columns:110px minmax(260px,1fr) 150px 150px;align-items:center;gap:12px}.table-head{padding:9px 12px;color:var(--color-text-3);background:var(--color-fill-1)}.rows article{padding:13px 12px;border-bottom:1px solid var(--color-border-1)}.rows b,.rows small{display:block}.rows small,.rows time{margin-top:3px;color:var(--color-text-3)}.split-view{display:grid;grid-template-columns:290px minmax(0,1fr);gap:12px;min-height:620px}.suite{display:flex;width:100%;align-items:center;justify-content:space-between;margin-top:7px;padding:12px;text-align:left;border:1px solid transparent;border-radius:8px;background:var(--color-fill-1);cursor:pointer}.suite:hover,.suite.active{border-color:var(--blue);background:rgb(var(--arcoblue-1))}.suite b,.suite small{display:block}.suite small{margin-top:4px;color:var(--color-text-3)}.run-list{display:grid;gap:7px}.run-list button{display:grid;grid-template-columns:minmax(180px,1fr) auto 72px;align-items:center;gap:12px;padding:12px;text-align:left;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1);cursor:pointer}.run-list button.active{border-color:var(--blue)}.run-list b,.run-list small{display:block}.run-list small{margin-top:3px;color:var(--color-text-3)}.run-list button>span{display:flex;gap:4px}.run-list i{padding:3px 5px;border-radius:4px;color:var(--color-text-2);background:var(--color-fill-3);font-size:11px;font-style:normal}.result-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}.result-summary div{padding:12px;border-left:3px solid var(--blue);background:var(--color-fill-1)}.result-summary span,.result-summary b{display:block}.result-summary span{color:var(--color-text-3)}.result-summary b{margin-top:5px;font-size:20px}.failures article{display:grid;grid-template-columns:54px 1fr 90px;gap:10px;padding:9px;border-top:1px solid var(--color-border-1)}.failures small{color:var(--color-text-3)}.candidate-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.candidate-grid>article{display:flex;min-height:170px;flex-direction:column;padding:15px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}.candidate-grid header,.candidate-grid footer{display:flex;align-items:center;justify-content:space-between;gap:8px}.candidate-grid h3{margin:14px 0 6px;font-size:15px}.candidate-grid p{margin:0;color:var(--color-text-3)}.candidate-grid footer{margin-top:auto;padding-top:14px}.candidate-grid footer>span{font-size:11px;color:var(--color-text-3)}.candidate-grid footer div{display:flex;gap:6px}.governance{display:flex;min-height:560px;flex-direction:column;align-items:center;justify-content:center;text-align:center}.governance>svg{font-size:48px;color:var(--blue)}.governance>span{margin-top:18px;letter-spacing:.12em;color:var(--color-text-3)}.governance h2{margin:8px 0;font-size:24px}.governance p{max-width:620px;color:var(--color-text-3);line-height:1.7}.governance div{display:flex;gap:8px;margin-top:12px}@media(max-width:1200px){.loop{grid-template-columns:repeat(3,1fr)}.candidate-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:900px){.hero{align-items:flex-start;flex-direction:column}.tabs{overflow:auto}.metrics,.overview-grid,.split-view{grid-template-columns:1fr}.loop,.candidate-grid{grid-template-columns:1fr}.table-head{display:none}.rows article{grid-template-columns:90px 1fr}.rows article time{grid-column:2}.run-list button>span{display:none}}
.loop{grid-template-columns:repeat(4,1fr)}
.qe-page{padding:16px;background:#f5f7fa;--blue:#1677ff;--navy:#1f2937;--teal:#0f766e;--orange:#f59e0b}.hero{align-items:center;padding:16px 20px;border:1px solid #dbe7f5;border-radius:10px;color:var(--color-text-1);background:var(--color-bg-2);box-shadow:none}.hero:before{content:"";width:4px;align-self:stretch;border-radius:3px;background:var(--blue)}.hero>div:first-child>span{color:var(--blue)}.hero h1{font-size:23px}.hero p{color:var(--color-text-3)}.hero-actions :deep(.arco-tag){color:var(--color-text-2);background:var(--color-fill-1);border-color:var(--color-border-2)}.workspace-shell{display:grid;grid-template-columns:292px minmax(0,1fr);align-items:start;gap:14px;margin-top:14px}.stage-rail{position:sticky;top:0;min-height:calc(100vh - 150px);padding:14px}.rail-title{padding:4px 6px 12px;border-bottom:1px solid var(--color-border-2)}.rail-title span,.rail-title b{display:block}.rail-title span{font-size:11px;letter-spacing:.08em;color:var(--color-text-3)}.rail-title b{margin-top:4px}.stage-rail .tabs{display:grid;gap:5px;margin:12px 0;padding:0;border:0;background:transparent}.stage-rail .tabs button{display:grid;grid-template-columns:32px 1fr auto;justify-content:initial;width:100%;min-width:0;padding:10px;text-align:left}.stage-rail .tabs button>i{display:grid;width:30px;height:30px;place-items:center;border-radius:7px;color:var(--color-text-3);background:var(--color-fill-2);font-style:normal}.stage-rail .tabs button>span{display:block;min-width:0}.stage-rail .tabs button b,.stage-rail .tabs button small{display:block}.stage-rail .tabs button small{margin-top:2px;overflow:hidden;color:var(--color-text-3);text-overflow:ellipsis;white-space:nowrap}.stage-rail .tabs button em{min-width:22px;padding:2px 6px;border-radius:11px;text-align:center;color:var(--color-text-3);background:var(--color-fill-3);font-size:11px;font-style:normal}.stage-rail .tabs button.active{color:var(--blue);background:rgb(var(--arcoblue-1))}.stage-rail .tabs button.active>i{color:#fff;background:var(--blue)}.workspace-content{min-width:0}.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}.metrics article{box-shadow:none}.loop-panel{padding:18px}.loop button{min-height:112px;background:var(--color-bg-2)}.loop button:after{content:"";position:absolute;top:50%;right:-9px;width:9px;border-top:1px dashed rgb(var(--arcoblue-5))}.loop button:last-child:after{display:none}.overview-grid{grid-template-columns:1fr}.source-grid article{background:var(--color-bg-2)}.panel{box-shadow:none}.content-panel,.split-view{animation:panel-in .18s ease-out}@keyframes panel-in{from{opacity:.4;transform:translateY(4px)}to{opacity:1;transform:none}}
.source-panel{margin-top:12px;padding:18px}.source-panel>.section-head>small{color:var(--color-text-3)}.source-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.source-grid article{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:11px 12px;border:1px solid rgb(var(--green-3));border-radius:8px;background:rgb(var(--green-1))}.source-grid article.pending{border-color:var(--color-border-2);background:var(--color-fill-1)}.source-grid b,.source-grid small{display:block}.source-grid small{margin-top:3px;color:var(--color-text-3)}
.toolbar p{max-width:780px;margin:6px 0 0;color:var(--color-text-3)}.trace-head,.trace-rows article{display:grid;grid-template-columns:120px minmax(260px,1fr) 150px 100px 80px 90px;align-items:center;gap:12px}.trace-head{padding:9px 12px;color:var(--color-text-3);background:var(--color-fill-1)}.trace-rows article{padding:13px 12px;border-bottom:1px solid var(--color-border-1)}.trace-rows b,.trace-rows small{display:block}.trace-rows small{margin-top:3px;color:var(--color-text-3)}
.roadmap{display:flex;align-items:center;gap:12px;margin-top:18px;padding:14px;border:1px dashed rgb(var(--orange-5));border-radius:9px;background:rgb(var(--orange-1))}.roadmap>svg{flex:none;font-size:28px;color:var(--orange)}.roadmap>div{min-width:0;flex:1}.roadmap b,.roadmap small{display:block}.roadmap small{margin-top:4px;color:var(--color-text-3)}
.subheading{margin:18px 0 10px;font-size:15px}.asset-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-bottom:14px}.asset-strip>article{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.asset-strip b,.asset-strip small{display:block}.asset-strip small{margin-top:4px;color:var(--color-text-3)}.trace-rows article{cursor:pointer}.trace-rows article:hover,.trace-rows article.selected{background:rgb(var(--arcoblue-1))}.trace-detail{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(310px,.65fr);gap:14px;margin-top:18px;padding-top:16px;border-top:1px solid var(--color-border-2)}.trace-detail h3{margin:0 0 10px;font-size:15px}.span-line{display:grid;gap:7px}.span-line article{display:grid;grid-template-columns:10px 1fr auto;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.span-line i{width:8px;height:8px;border-radius:50%;background:var(--color-fill-4)}.span-line i.completed{background:#16a34a}.span-line i.failed{background:#ef4444}.span-line i.running{background:#1677ff}.span-line b,.span-line small{display:block}.span-line small{margin-top:3px;color:var(--color-text-3)}.trace-detail aside>article{margin-bottom:8px;padding:12px;border:1px solid var(--color-border-2);border-radius:8px}.trace-detail aside header{display:flex;justify-content:space-between;margin-bottom:8px}.trace-detail aside p{margin:6px 0;color:var(--color-text-2)}.trace-detail aside small{color:var(--color-text-3)}.governance-board{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}.governance-board>section{padding:14px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}.mini-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}.mini-head span{display:grid;min-width:22px;height:22px;place-items:center;border-radius:11px;color:var(--blue);background:rgb(var(--arcoblue-1))}.governance-board section>article{margin-top:8px;padding:11px;border:1px solid var(--color-border-2);border-radius:7px;background:var(--color-bg-2)}.governance-board article>div{display:flex;justify-content:space-between}.governance-board article>b,.governance-board article>small{display:block;margin-top:8px}.governance-board article>p{margin:5px 0;color:var(--color-text-3)}.governance-board article>small{color:var(--color-text-3)}
.asset-mode-tabs{display:flex;gap:6px;margin:14px 0}.asset-mode-tabs button{padding:8px 13px;border:1px solid var(--color-border-2);border-radius:7px;color:var(--color-text-2);background:var(--color-fill-1);cursor:pointer}.asset-mode-tabs button.active{border-color:var(--blue);color:var(--blue);background:rgb(var(--arcoblue-1))}.asset-mode-tabs b{margin-left:5px}.asset-queue-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:14px}.asset-queue-kpis article{padding:12px 14px;border-left:3px solid var(--blue);background:var(--color-fill-1)}.asset-queue-kpis article.warn{border-color:#ff7d00}.asset-queue-kpis article.bad{border-color:#f53f3f}.asset-queue-kpis small,.asset-queue-kpis b{display:block}.asset-queue-kpis small{color:var(--color-text-3)}.asset-queue-kpis b{margin-top:4px;font-size:20px}.asset-event-head,.asset-events article{display:grid;grid-template-columns:150px minmax(260px,1fr) 100px 140px 110px;align-items:center;gap:12px}.asset-event-head{padding:9px 12px;color:var(--color-text-3);background:var(--color-fill-1)}.asset-events article{padding:12px;border-bottom:1px solid var(--color-border-1)}.asset-events b,.asset-events small{display:block}.asset-events small{margin-top:3px;color:var(--color-text-3)}.asset-events p{margin:5px 0 0;color:#f53f3f;font-size:11px}.asset-event-result{color:var(--color-text-3);font-size:11px}
.asset-review-facts{display:grid;grid-template-columns:2fr repeat(3,1fr);gap:8px;margin:14px 0}.asset-review-facts article{padding:10px;border:1px solid var(--color-border-2);border-radius:7px;background:var(--color-fill-1)}.asset-review-facts small,.asset-review-facts b{display:block}.asset-review-facts small{color:var(--color-text-3)}.asset-review-facts b{margin-top:4px}.asset-evidence{margin-top:10px}.asset-evidence summary{cursor:pointer;color:var(--blue)}.asset-evidence pre{max-height:260px;padding:12px;overflow:auto;border-radius:7px;background:#101828;color:#d1e9ff;font-size:11px}
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
/* 派发后的"前往执行"入口（T03）。做成链接而不是按钮：它是对已有派发的再次进入，
   不是又一次操作，用主按钮会让人以为点了会重新派发一轮。 */
.dispatch-link{cursor:pointer;color:rgb(var(--arcoblue-6));font-weight:600}
.dispatch-link:hover{text-decoration:underline}
.dispatch-ttl{margin-left:6px;color:var(--color-text-3)}
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
.qe-page{min-height:100%;padding:18px 18px 24px;background:#f7f8fa;color:#1d2129}
.hero{display:flex;margin:0 0 14px;padding:24px 28px;border:0;border-radius:18px;color:#fff;background:linear-gradient(125deg,#102a43 0%,#176b87 62%,#1b8f8a 100%);box-shadow:0 14px 40px rgb(16 42 67/18%)}
/* （2026-10-03 曾短暂加过 .hero--compact：横幅只留右侧动作时收窄。
   最终改为横幅始终完整展示、标题按页签切换，该样式不再需要，已移除。） */
.hero:before{display:none}.hero-copy{min-width:0}.hero h1{display:block;margin:5px 0 4px;font-size:26px}.hero p{display:block;margin:0;color:#d8edf0;font-size:13px}.hero-actions{margin-left:auto}.hero-actions :deep(.arco-tag),.hero-actions :deep(.arco-btn){display:inline-flex;height:36px;box-sizing:border-box;align-items:center;justify-content:center;gap:6px;padding:0 15px;border-radius:6px;font-size:14px;line-height:34px;white-space:nowrap}.hero-actions :deep(.arco-btn){color:#526273;border-color:rgb(255 255 255/50%);background:rgb(255 255 255/94%)}
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
@media(max-width:720px){.qe-page{padding-right:10px;padding-left:10px}.hero{margin-right:0;margin-left:0;padding:20px;align-items:flex-start}.hero p{display:block;margin:5px 0 0}.hero-actions{width:100%;margin:10px 0 0}.workspace-tabs{border-radius:8px}.metrics{grid-template-columns:1fr}.metrics article{border-right:0;border-bottom:1px solid #f0f1f2!important}.loop{display:grid;grid-template-columns:1fr}.loop button{border-right:0;border-bottom:1px solid #e5e6eb}.team-panel .people-group{display:block}}
.primary-tabs{display:flex;gap:24px;margin:0 -18px 14px;padding:0 22px;border-bottom:1px solid #e5e6eb;background:#fff}.primary-tabs button{position:relative;display:flex;height:48px;align-items:center;gap:7px;padding:0 2px;border:0;color:#4e5969;background:transparent;cursor:pointer;font-size:14px}.primary-tabs button:hover{color:#165dff}.primary-tabs button.active{color:#1d2129;font-weight:600}.primary-tabs button.active:after{content:"";position:absolute;right:0;bottom:-1px;left:0;height:2px;border-radius:2px;background:#165dff}.primary-tabs svg{font-size:16px}
.quick-tabs{display:flex;width:max-content;gap:3px;margin:0 0 14px;padding:3px;border-radius:8px;background:#e5e6eb}.quick-tabs button{min-width:88px;padding:7px 16px;border:0;border-radius:6px;color:#4e5969;background:transparent;cursor:pointer}.quick-tabs button.active{color:#1d2129;background:#fff;box-shadow:0 1px 4px rgb(29 33 41/10%);font-weight:600}
.skill-hub-embed{display:flex;flex-direction:column}
.launch-console{min-height:560px;padding:24px}.launch-heading{display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:20px;border-bottom:1px solid #e5e6eb}.launch-heading span{font-size:10px;font-weight:700;letter-spacing:.14em;color:#165dff}.launch-heading h2{margin:5px 0 4px;font-size:20px}.launch-heading p{margin:0;color:#86909c}.launch-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:20px}.launch-grid button{display:grid;grid-template-columns:42px minmax(0,1fr) auto 14px;align-items:center;gap:12px;min-height:116px;padding:18px;text-align:left;border:1px solid #e5e6eb;border-radius:9px;background:#fff;cursor:pointer}.launch-grid button:hover{border-color:#94bfff;background:#f7faff;box-shadow:0 8px 20px rgb(22 93 255/7%)}.launch-grid button>i{display:grid;width:40px;height:40px;place-items:center;border-radius:8px;color:#165dff;background:#e8f3ff;font-size:18px;font-style:normal}.launch-grid b,.launch-grid small{display:block}.launch-grid b{font-size:14px}.launch-grid small{margin-top:5px;color:#86909c;line-height:1.45}.launch-grid em{align-self:start;padding:3px 7px;border-radius:10px;color:#86909c;background:#f2f3f5;font-size:10px;font-style:normal;white-space:nowrap}.launch-grid>button>svg{color:#86909c}
.console-context{display:flex;align-items:center;gap:12px;margin-bottom:10px}.console-context button{display:flex;align-items:center;gap:4px;padding:6px 0;border:0;color:#165dff;background:transparent;cursor:pointer}.console-context button svg{transform:rotate(180deg)}.console-context span{color:#86909c;font-size:12px}.skill-hub-embed{min-height:680px;overflow:hidden;border:1px solid #e5e6eb;border-radius:10px;background:#fff}.knowledge-graph-embed{min-width:0}.graph-layout{grid-template-columns:minmax(0,1fr)}.knowledge-graph-embed :deep(.knowledge-graph-page){padding:0}.skill-hub-embed :deep(.skill-hub-console){border:0}
@media(max-width:1320px){.launch-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:720px){.primary-tabs{gap:16px;margin-right:-10px;margin-left:-10px;padding:0 12px}.quick-tabs{width:100%}.quick-tabs button{flex:1}.launch-grid{grid-template-columns:1fr}.launch-console{padding:16px}}
.board-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:12px}.board-kpis article{position:relative;padding:16px 18px 34px;overflow:hidden;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.board-kpis article:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--teal)}.board-kpis .warn:before{background:var(--orange)}.board-kpis .blue:before{background:var(--blue)}.board-kpis span{display:block;font-size:12px;color:var(--color-text-3)}.kpi-value{display:flex;align-items:baseline;gap:8px;margin:6px 0 2px}.kpi-value b{font-size:22px;line-height:1.1}.kpi-value em{font-size:12px;font-style:normal}.kpi-value em.up{color:rgb(var(--green-6))}.kpi-value em.down{color:rgb(var(--red-6))}.board-kpis small{display:block;font-size:12px;color:var(--color-text-3)}.kpi-spark{position:absolute;left:18px;right:18px;bottom:12px;display:flex;align-items:flex-end;gap:2px;height:18px}.kpi-spark i{flex:1;min-height:2px;border-radius:1px;background:rgb(var(--arcoblue-3));opacity:.6}.board-kpis .warn .kpi-spark i{background:rgb(var(--orange-3))}.chart-body{margin-top:6px}.chart-legend{display:flex;gap:16px;font-size:12px;color:var(--color-text-3)}.chart-legend span{display:flex;align-items:center;gap:6px}.chart-legend .dot{display:inline-block;width:8px;height:8px;border-radius:2px}.chart-legend .dot.session{background:rgb(var(--arcoblue-5))}.chart-legend .dot.token{background:var(--teal)}.chart-bars{display:flex;align-items:flex-end;gap:6px;height:180px;margin-top:12px}.bar-col{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;height:100%}.bar-stack{display:flex;align-items:flex-end;gap:2px;width:100%;height:100%}.bar{flex:1;min-height:2px;border-radius:2px 2px 0 0}.bar.session{background:rgb(var(--arcoblue-5))}.bar.token{background:var(--teal)}.bar-col small{font-size:10px;color:var(--color-text-3)}.board-table .agent-head,.board-table .agent-rows article{display:grid;grid-template-columns:1.6fr .8fr .8fr 1fr .9fr .7fr;gap:12px;align-items:center}.agent-head{padding:8px 14px;border-bottom:1px solid var(--color-border-2);font-size:12px;color:var(--color-text-3)}.agent-rows article{padding:12px 14px;border-bottom:1px solid var(--color-border-1)}.agent-rows article:last-child{border-bottom:0}.agent-rows article.idle{opacity:.55}.agent-name b{display:block;font-size:14px}.agent-name small{display:block;font-size:12px;color:var(--color-text-3)}.cell{display:flex;flex-direction:column;gap:5px}.cell b{font-size:13px}.meter{display:block;height:4px;overflow:hidden;border-radius:2px;background:var(--color-fill-2)}.meter u{display:block;height:100%;border-radius:2px;background:rgb(var(--arcoblue-6));text-decoration:none}.start-result{margin-bottom:12px;padding:14px 16px;border:1px solid var(--color-border-2);border-radius:10px;background:var(--color-bg-2)}.start-result-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.start-result-head b{font-size:14px}.start-result-head small{display:block;font-size:12px;color:var(--color-text-3)}.binding-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.binding{padding:10px 12px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.binding.locked{border-color:rgb(var(--green-6));background:rgb(var(--green-1))}.binding.unmanaged{border-color:rgb(var(--orange-6));background:rgb(var(--orange-1))}.binding b{display:block;margin-bottom:4px;font-size:13px}.binding small{display:block;font-size:12px;color:var(--color-text-2);word-break:break-all}.binding .sha{color:var(--color-text-3)}.warn-line{margin:10px 0 0;font-size:12px;color:rgb(var(--orange-6))}
.agent-filter-bar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:12px;padding:13px 16px;border:1px solid #e5e6eb;border-radius:10px;background:#fff}.agent-filter-bar>div{display:flex;align-items:center;gap:12px}.agent-filter-bar>div>span{font-size:13px;font-weight:600;white-space:nowrap}.agent-filter-bar>small{color:#86909c;font-size:12px;white-space:nowrap}
.board-table>.section-head{margin:0;padding:18px 22px 16px;border-bottom:1px solid #f0f1f2}.board-table>.section-head h2{margin-top:5px}.board-table>.section-head>small{padding-top:3px}.board-table .agent-head,.board-table .agent-rows article{padding-right:22px;padding-left:22px}
.board-kpis button{position:relative;min-width:0;padding:16px 18px 34px;overflow:hidden;border:1px solid #e5e6eb;border-radius:10px;color:inherit;background:#fff;font-family:inherit;text-align:left;cursor:pointer;transition:border-color .16s ease,box-shadow .16s ease,transform .16s ease}.board-kpis button:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:#14b8a6}.board-kpis button.warn:before{background:#f59e0b}.board-kpis button.blue:before{background:#165dff}.board-kpis button:hover{border-color:#94bfff;transform:translateY(-1px)}.board-kpis button.active{border-color:#165dff;box-shadow:0 0 0 2px rgb(22 93 255/10%),0 8px 22px rgb(22 93 255/8%)}.board-kpis button.active:after{content:"";position:absolute;top:10px;right:10px;width:6px;height:6px;border-radius:50%;background:#165dff}
.board-chart{margin-bottom:12px;padding:20px 22px}.chart-title{align-items:flex-start}.chart-title p{margin:5px 0 0;color:#86909c;font-size:12px}.chart-total{min-width:150px;padding-left:18px;text-align:right;border-left:1px solid #e5e6eb}.chart-total small,.chart-total b{display:block}.chart-total small{color:#86909c;font-size:11px}.chart-total b{margin-top:3px;font-size:25px;letter-spacing:-.02em}.chart-bars{height:250px;margin-top:20px;padding:18px 12px 0;border-top:1px solid #f0f1f2;border-bottom:1px solid #e5e6eb;background:repeating-linear-gradient(to bottom,transparent 0,transparent 59px,#f2f3f5 60px)}.bar-col{position:relative;gap:7px}.bar-col>b{min-height:16px;color:#4e5969;font-size:10px;font-weight:500}.bar-stack{justify-content:center}.bar-stack .bar{width:min(38px,76%);flex:none;background:#165dff;opacity:.82;transition:opacity .16s ease,height .25s ease}.bar-stack .bar.users{background:#14b8a6}.bar-stack .bar.tokens{background:#0f6b78}.bar-stack .bar.latency{background:#f59e0b}.bar-stack .bar.failed{background:#f53f3f}.bar-col:hover .bar{opacity:1}.chart-axis-note{display:flex;justify-content:space-between;padding-top:10px;color:#86909c;font-size:11px}
@media(max-width:1100px){.board-kpis{grid-template-columns:repeat(3,1fr)}.agent-filter-bar{align-items:flex-start}.agent-filter-bar>div{align-items:flex-start;flex-direction:column}.agent-filter-bar :deep(.arco-select){width:340px!important}}
@media(max-width:720px){.agent-filter-bar{align-items:stretch;flex-direction:column}.agent-filter-bar :deep(.arco-select){width:100%!important}.board-kpis{grid-template-columns:1fr 1fr}.board-chart{padding:16px}.chart-bars{gap:3px;overflow-x:auto}.bar-col{min-width:34px}.chart-title{gap:12px}.chart-total{min-width:110px}.board-table>.section-head,.board-table .agent-head,.board-table .agent-rows article{padding-right:16px;padding-left:16px}}
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
/* 发起向导第一步：四阶段纵向排列；候选只在下拉框展开时出现 */
.pin-grid{display:grid;grid-template-columns:1fr;gap:10px;margin-top:16px}
.pin-block{display:grid;grid-template-columns:minmax(130px,180px) minmax(0,1fr);align-items:center;gap:10px 16px;padding:12px 14px;border:1px solid #e5e6eb;border-radius:9px;background:#fafafa}
.pin-block header{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.pin-block header b{font-size:13px}
.pin-block header small{overflow:hidden;color:#86909c;font-size:11px;text-overflow:ellipsis;white-space:nowrap}
.pin-select{width:100%}
.pin-option{display:flex;align-items:center;justify-content:space-between;gap:16px;width:100%;padding:2px 0}.pin-option-name{display:flex;min-width:0;align-items:center;gap:6px}.pin-option-name b{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.pin-option-meta{flex:none;color:#86909c;font-size:10px;white-space:nowrap}
.pin-selected{grid-column:2;display:flex;align-items:center;justify-content:space-between;gap:10px;color:#4e5969;font-size:11px}
.pin-selected code{padding:1px 5px;border-radius:4px;color:#86909c;background:#f2f3f5;font-size:10px}
.pin-warn{margin:12px 0 0;padding:9px 11px;border:1px dashed #ff7d00;border-radius:8px;color:#4e5969;background:#fff7e8;font-size:12px;line-height:1.7}
.pin-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:16px}
.pin-summary article{padding:10px;border:1px solid #e5e6eb;border-radius:8px;background:#fafafa}
.pin-summary b,.pin-summary small{display:block}.pin-summary b{font-size:12px}
.pin-summary small{margin-top:4px;overflow:hidden;color:#86909c;font-size:11px;text-overflow:ellipsis;white-space:nowrap}
/* ---- 用例审查自进化向导 */
.single-grid footer{display:flex;align-items:center;justify-content:space-between;gap:10px}
.review-list{display:flex;flex-direction:column;gap:8px;max-height:360px;margin-top:12px;overflow:auto}
.review-item{display:block;width:100%;padding:11px 13px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-bg-2);text-align:left;cursor:pointer;transition:border-color .15s ease,background .15s ease}
.review-item:hover{border-color:rgb(var(--arcoblue-4))}
.review-item.selected{border-color:var(--blue);background:rgb(var(--arcoblue-1))}
.review-item.blocked{background:var(--color-fill-1);opacity:.86}
.review-item-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.review-item-head b{font-size:13px;color:var(--color-text-1)}
.review-item-meta{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;color:var(--color-text-3);font-size:11px}
.review-item-meta .sha{font-family:monospace}
.review-blocker{margin:6px 0 0;color:rgb(var(--orange-6));font-size:11px;line-height:1.6}
.review-target{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;background:var(--color-fill-1);font-size:12px}
.review-target b{color:var(--color-text-1)}
.review-target span{color:var(--color-text-3)}
.review-target .sha{font-family:monospace}
.preflight{margin-top:14px;padding:12px;border:1px solid var(--color-border-2);border-radius:9px;background:var(--color-fill-1)}
.preflight-scan{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-bottom:10px}
.preflight-scan span{display:flex;flex-direction:column;align-items:center;padding:7px 4px;border-radius:7px;background:var(--color-bg-2)}
.preflight-scan b{font-size:15px;color:var(--color-text-1)}
.preflight-scan small{color:var(--color-text-3);font-size:11px}
.preflight-defect{display:flex;align-items:center;gap:8px;padding:6px 0;font-size:12px}
.preflight-defect span{color:var(--color-text-3)}
.evolution-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:12px 0}
.evolution-facts article{padding:10px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-bg-2)}
.evolution-facts small,.evolution-facts b,.evolution-facts em{display:block}
.evolution-facts small{color:var(--color-text-3);font-size:11px}
.evolution-facts b{margin-top:4px;font-size:14px;color:var(--color-text-1)}
.evolution-facts b.bad{color:#cb2634}
.evolution-facts b.sha{font-family:monospace;font-size:12px}
.evolution-facts em{margin-top:2px;color:var(--color-text-3);font-size:11px;font-style:normal}
.evolution-scan{margin:0 0 10px;color:var(--color-text-2);font-size:12px}
.evolution-defects article{margin-bottom:8px;padding:9px 11px;border-radius:8px;background:var(--color-fill-1)}
.evolution-defects header{display:flex;align-items:center;gap:8px;margin-bottom:5px;font-size:12px}
.evolution-defects header span{color:var(--color-text-3)}
.evolution-defects p{margin:0;color:var(--color-text-2);font-size:12px;line-height:1.65}
/* ---- 阶段报告出口 / 反馈入口（T05）
   「上传反馈」的取文件入口：藏在卡片外的隐藏 input，卡片里只留一个按钮。 */
.hidden-file-input{display:none}
.wf-feedback-echo{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:10px 0 0;padding:8px 10px;border-radius:7px;color:#4e5969;background:#f6fffb;border:1px solid #d9f2e6;font-size:12px}
.wf-feedback-echo b{color:#0f766e;font-size:13px}
.wf-feedback-echo small{color:#86909c}
/* ---- 采纳率按版本对照（采纳率 = 版本间对比的评分维度，不是门槛） */
.acceptance-strip{margin-top:14px;padding-top:12px;border-top:1px solid var(--color-border-2)}
.acceptance-strip h4{margin:0 0 10px;font-size:13px;color:var(--color-text-1)}
.acceptance-bars{display:flex;align-items:flex-end;gap:10px;height:110px;padding:0 4px}
.acceptance-col{display:flex;flex:1 1 0;min-width:0;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;gap:4px}
.acceptance-col b{font-size:12px;color:var(--color-text-1)}
.acceptance-col i{display:block;width:100%;max-width:44px;border-radius:4px 4px 0 0;background:linear-gradient(180deg,#4080ff,#165dff)}
.acceptance-col small{overflow:hidden;max-width:100%;color:var(--color-text-3);font-size:10px;text-overflow:ellipsis;white-space:nowrap}
/* ---- 内容来源（T10）：通道实况 + 引用条目 + Skill 包摘要 */
.sources-panel{margin-top:14px;padding-top:12px;border-top:1px solid var(--color-border-2)}
.sources-panel .section-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.sources-panel h4{margin:3px 0 0;font-size:13px}
.source-channel-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px}
.source-channel-row small{color:var(--color-text-3);font-size:11px}
.citation-list{display:grid;gap:6px}
.citation-list article{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;align-items:center;gap:9px;padding:8px 10px;border:1px solid var(--color-border-2);border-radius:7px;background:var(--color-fill-1)}
.citation-list b,.citation-list small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.citation-list b{font-size:12px}
.citation-list small{margin-top:2px;color:var(--color-text-3);font-size:10px}
.citation-list article>span{color:var(--color-text-3);font-size:11px}
.graph-node-row{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:10px;color:var(--color-text-3);font-size:11px}
.node-chip{padding:3px 9px;border:1px solid rgb(var(--arcoblue-3));border-radius:12px;color:var(--blue);background:rgb(var(--arcoblue-1));font-family:inherit;font-size:11px;cursor:pointer}
.node-chip:hover{border-color:var(--blue)}
.node-chip.unresolved{border-color:var(--color-border-3);color:var(--color-text-3);background:var(--color-fill-2)}
.skill-content-note{margin-top:10px;padding:9px 11px;border-radius:7px;background:var(--color-fill-1)}
.skill-content-note b,.skill-content-note small{display:block}
.skill-content-note b{font-size:12px}
.skill-content-note small{margin-top:3px;color:var(--color-text-3);font-size:11px}
.skill-content-note ul{margin:7px 0 0;padding-left:16px}
.skill-content-note li{display:flex;gap:8px;color:var(--color-text-2);font-size:11px;line-height:1.7}
.skill-content-note li code{font-family:ui-monospace,MENLO,monospace}
.skill-content-note li span{color:var(--color-text-3);font-family:ui-monospace,MENLO,monospace}
/* ---- 第 ③ 步：AI 候选优化点 */
.proposal-head{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:12px;font-size:12px}
.proposal-head .sha{overflow:hidden;color:var(--color-text-3);font-size:11px;text-overflow:ellipsis;white-space:nowrap}
.proposal-head .arco-btn{margin-left:auto}
.candidate-list{display:grid;gap:8px;max-height:340px;margin-top:10px;overflow:auto}
.candidate-item{padding:10px 12px;border:1px solid var(--color-border-2);border-left:3px solid var(--color-border-3);border-radius:8px;background:var(--color-bg-2)}
.candidate-item.accept{border-left-color:#16a34a}
.candidate-item.edit{border-left-color:#165dff}
.candidate-item.reject{opacity:.6;border-left-color:#86909c}
.candidate-item header{display:flex;align-items:center;flex-wrap:wrap;gap:8px;font-size:12px}
.candidate-item header b{font-size:12px}
.candidate-item header span{margin-left:auto;color:var(--color-text-3);font-size:11px}
.candidate-hypothesis{margin:6px 0 0;color:var(--color-text-2);font-size:12px;line-height:1.7;white-space:pre-wrap}
.candidate-actions{display:flex;gap:8px;margin-top:8px}
.governance-columns{display:grid;grid-template-columns:1.1fr .9fr;gap:18px;margin-top:14px}.governance-columns h3{margin:0 0 10px;font-size:14px}.section-inline{display:flex;align-items:center;justify-content:space-between}.asset-strip .clickable{cursor:pointer}.governance-list{display:grid;gap:8px;margin-top:10px}.governance-list>article{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:10px;padding:11px 13px;border:1px solid #e5e6eb;border-radius:8px;background:#fafafa}.governance-list b,.governance-list small{display:block}.governance-list small{margin-top:3px;color:#86909c;font-size:11px}.history-entry{display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:8px;margin:14px 0}.preflight-card{display:flex;align-items:center;flex-wrap:wrap;gap:14px;padding:13px;border:1px solid #94bfff;border-radius:8px;background:#f7faff}.preflight-card span{color:#4e5969;font-size:12px}.preflight-card pre{width:100%;max-height:180px;overflow:auto}.replay-list{margin-top:18px}
@media(max-width:1080px){.governance-columns{grid-template-columns:1fr}.history-entry{grid-template-columns:1fr 1fr}.history-entry .arco-btn{grid-column:1/-1}}
@media(max-width:1080px){.wf-shell{grid-template-columns:1fr}.wf-flows{max-height:240px}.wf-stage-bar{grid-template-columns:repeat(2,minmax(0,1fr))}.wf-card-body,.pin-summary{grid-template-columns:1fr}.pin-block{grid-template-columns:1fr}.pin-selected{grid-column:1}}
</style>
