<template>
  <div class="governance">
    <div class="governance__head">
      <div class="governance__title">
        <span>发布治理</span>
        <a-tag v-if="approvalView" size="small" :color="releaseStateColor(approvalView.state)">
          {{ approvalView.state_label || approvalView.state }}
        </a-tag>
      </div>
      <div class="governance__role">
        当前角色：<strong>{{ roleLabel }}</strong>
      </div>
    </div>

    <div class="governance__body">
      <div v-if="!releaseId" class="governance__state">
        选择一个候选版本后，这里显示它的门禁证据与可执行的治理动作
      </div>

      <template v-else>
        <div v-if="loadingApproval" class="governance__state"><a-spin dot /> 读取治理状态…</div>
        <div v-else-if="approvalError" class="governance__state governance__state--error">
          <p>{{ approvalError }}</p>
          <a-button size="mini" @click="emit('refresh')">重试</a-button>
        </div>

        <template v-else-if="approvalView">
          <!-- 缺失条件：唯一真值来自后端 -->
          <section class="card">
            <div class="card__title">
              缺失条件
              <span class="card__count">{{ missingConditions.length }}</span>
            </div>
            <ul v-if="missingConditions.length" class="missing">
              <li v-for="item in missingConditions" :key="item.code">
                <span class="missing__label">{{ item.label }}</span>
                <span class="missing__detail">{{ item.detail }}</span>
              </li>
            </ul>
            <p v-else class="card__ok">前置条件已齐备</p>

            <p v-if="approvalView.evidence_stale" class="card__warn">
              审批依据已变化：提交审批后被重新评测，必须重新提交审批才能激活。
            </p>
          </section>

          <!-- 门禁逐项 -->
          <section class="card">
            <div class="card__title">
              评测门禁
              <span v-if="gateSnapshot" class="card__hint">
                快照 {{ shortHash(gateSnapshot.content_hash) }}
              </span>
            </div>
            <div v-if="gateChecks.length" class="checks">
              <div v-for="item in gateChecks" :key="item.code" class="checks__row">
                <span class="checks__mark" :class="item.ok ? 'checks__mark--ok' : 'checks__mark--bad'">
                  {{ item.ok ? '✓' : '✗' }}
                </span>
                <div class="checks__main">
                  <span class="checks__label">{{ item.label }}</span>
                  <span class="checks__detail">{{ item.detail }}</span>
                </div>
              </div>
            </div>
            <p v-else class="card__muted">尚未执行质量门禁</p>
          </section>

          <!-- 基线 / 候选指标对比 -->
          <section class="card">
            <div class="card__title">基线 / 候选指标</div>
            <table v-if="metricRows.length" class="metrics">
              <thead>
                <tr>
                  <th>指标</th>
                  <th>基线</th>
                  <th>候选</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in metricRows" :key="row.label">
                  <td>{{ row.label }}</td>
                  <td class="mono">{{ row.baseline }}</td>
                  <td class="mono" :class="row.worse ? 'metrics__worse' : ''">{{ row.candidate }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="card__muted">没有可对比的基线 / 候选指标</p>
          </section>

          <!-- 治理动作：禁用时明确说明缺什么 -->
          <section class="card">
            <div class="card__title">治理操作</div>
            <div class="actions">
              <div v-for="action in actions" :key="action.key" class="actions__row">
                <a-button
                  size="small"
                  :type="action.danger ? 'outline' : 'outline'"
                  :status="action.danger ? 'danger' : undefined"
                  :disabled="!action.enabled"
                  @click="emit('action', action.key)"
                >
                  {{ action.label }}
                </a-button>
                <span class="actions__hint" :class="{ 'actions__hint--blocked': !action.enabled }">
                  {{ action.hint }}
                </span>
              </div>
            </div>
          </section>

          <!-- 审批历史 -->
          <section v-if="approvalView.decisions.length" class="card">
            <div class="card__title">审批历史</div>
            <ul class="decisions">
              <li v-for="(item, index) in approvalView.decisions" :key="index">
                <span class="decisions__tag">{{ decisionLabel(item.decision) }}</span>
                <span class="decisions__reason">{{ item.reason || '（未填原因）' }}</span>
                <span class="decisions__meta">{{ item.actor || '—' }} · {{ formatTime(item.created_at) }}</span>
              </li>
            </ul>
          </section>

          <!-- 观察窗口 -->
          <section v-if="approvalView.observations.length" class="card">
            <div class="card__title">灰度观察窗口</div>
            <ul class="decisions">
              <li v-for="(item, index) in approvalView.observations" :key="index">
                <span class="decisions__tag" :class="item.status === 'breached' ? 'decisions__tag--bad' : ''">
                  {{ item.status === 'breached' ? '超阈值' : '健康' }}
                </span>
                <span class="decisions__reason">{{ item.window_key }}</span>
                <span class="decisions__meta">{{ formatTime(item.created_at) }}</span>
              </li>
            </ul>
          </section>
        </template>
      </template>

      <!-- 审计时间线 -->
      <section class="card">
        <div class="card__title">
          审计时间线
          <span class="card__hint">{{ auditLogs.length }} 条</span>
        </div>
        <div v-if="loadingAudit" class="card__muted">读取中…</div>
        <div v-else-if="auditError" class="card__muted card__muted--error">{{ auditError }}</div>
        <ul v-else-if="auditLogs.length" class="audit">
          <li v-for="item in auditLogs" :key="item.id" class="audit__row">
            <span class="audit__action">{{ auditActionLabel(item.action) }}</span>
            <span class="audit__state">
              {{ item.from_state || '—' }} → {{ item.to_state || '—' }}
            </span>
            <span class="audit__reason" :title="item.reason">{{ item.reason || '' }}</span>
            <span class="audit__time">{{ formatTime(item.created_at) }}</span>
          </li>
        </ul>
        <p v-else class="card__muted">暂无审计记录</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type {
  AuditLogEntry,
  GovernanceAction,
  ReleaseApprovalView,
} from '../../types/hub'
import { releaseStateColor } from '../../types/hub'
import { auditActionLabel } from '../../utils/stages'

const props = defineProps<{
  releaseId: string
  approvalView: ReleaseApprovalView | null
  auditLogs: AuditLogEntry[]
  actions: GovernanceAction[]
  roleLabel: string
  loadingApproval: boolean
  loadingAudit: boolean
  approvalError: string
  auditError: string
}>()

const emit = defineEmits<{
  (e: 'action', key: string): void
  (e: 'refresh'): void
}>()

function formatTime(value?: string): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

function shortHash(value?: string): string {
  if (!value) return '—'
  return value.slice(0, 12)
}

function decisionLabel(decision: string): string {
  const map: Record<string, string> = {
    approved: '通过',
    rejected: '驳回',
    rollback: '回滚',
    quarantine: '隔离',
  }
  return map[decision] || decision
}

const missingConditions = computed(() => props.approvalView?.missing_conditions ?? [])
const gateSnapshot = computed(() => props.approvalView?.gate ?? null)
const gateChecks = computed(() => gateSnapshot.value?.checks ?? [])

const METRIC_LABELS: Record<string, string> = {
  quality: '质量均分',
  sample_count: '样本数',
  latency_ms: '平均耗时(ms)',
  token_usage: '平均 Token',
  stability: '稳定性',
  false_negative_rate: '漏报率',
  false_positive_rate: '误报率',
  adoption_rate: '采纳率',
}

/** 基线 / 候选指标对比。数值变差用橙色标出，但不做"是否超阈值"的二次判定。 */
const metricRows = computed(() => {
  const metrics = gateSnapshot.value?.metrics as
    | { baseline?: Record<string, unknown>; candidate?: Record<string, unknown> }
    | undefined
  const baseline = metrics?.baseline
  const candidate = metrics?.candidate
  if (!baseline || !candidate) return []

  const keys = ['quality', 'sample_count', 'latency_ms', 'token_usage', 'stability', 'false_negative_rate', 'false_positive_rate']
  const higher = new Set(['quality', 'stability', 'adoption_rate'])
  const rows: { label: string; baseline: string; candidate: string; worse: boolean }[] = []

  for (const key of keys) {
    const baseValue = baseline[key]
    const candValue = candidate[key]
    if (baseValue == null && candValue == null) continue
    const baseNumber = Number(baseValue)
    const candNumber = Number(candValue)
    const comparable = Number.isFinite(baseNumber) && Number.isFinite(candNumber)
    const worse = comparable
      ? (higher.has(key) ? candNumber < baseNumber : candNumber > baseNumber)
      : false
    rows.push({
      label: METRIC_LABELS[key] || key,
      baseline: baseValue == null ? '—' : String(baseValue),
      candidate: candValue == null ? '—' : String(candValue),
      worse,
    })
  }
  return rows
})
</script>

<style scoped>
.governance {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.governance__head {
  padding: 12px;
  border-bottom: 1px solid var(--color-border-1);
}

.governance__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.governance__role {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-3);
}

.governance__role strong {
  color: #1f2937;
}

.governance__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.governance__state {
  padding: 24px 12px;
  font-size: 12px;
  color: var(--color-text-3);
  text-align: center;
}

.governance__state--error {
  color: rgb(var(--red-6));
}

.card {
  border: 1px solid var(--color-border-1);
  border-radius: 6px;
  background: var(--color-bg-2);
  padding: 10px 12px;
}

.card__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 8px;
}

.card__count {
  min-width: 18px;
  text-align: center;
  border-radius: 9px;
  background: rgba(255, 125, 0, 0.15);
  color: #ff7d00;
  font-size: 11px;
  line-height: 18px;
}

.card__hint,
.card__muted {
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-3);
}

.card__muted {
  margin: 0;
}

.card__muted--error,
.card__warn {
  color: #ff7d00;
}

.card__warn {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.6;
}

.card__ok {
  margin: 0;
  font-size: 12px;
  color: #00b42a;
}

.missing {
  margin: 0;
  padding: 0;
  list-style: none;
}

.missing li {
  padding: 6px 8px;
  margin-bottom: 4px;
  border-left: 3px solid #ff7d00;
  background: rgba(255, 125, 0, 0.07);
  border-radius: 0 4px 4px 0;
}

.missing__label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #1f2937;
}

.missing__detail {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-text-3);
  line-height: 1.5;
}

.checks__row {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px dashed var(--color-border-1);
}

.checks__row:last-child {
  border-bottom: none;
}

.checks__mark {
  flex: none;
  width: 14px;
  text-align: center;
  font-size: 12px;
}

.checks__mark--ok { color: #00b42a; }
.checks__mark--bad { color: rgb(var(--red-6)); }

.checks__main {
  min-width: 0;
}

.checks__label {
  display: block;
  font-size: 12px;
  color: #1f2937;
}

.checks__detail {
  display: block;
  font-size: 11px;
  color: var(--color-text-3);
  line-height: 1.5;
  word-break: break-word;
}

.metrics {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.metrics th {
  text-align: left;
  padding: 3px 4px;
  color: var(--color-text-3);
  font-weight: 400;
  border-bottom: 1px solid var(--color-border-1);
}

.metrics td {
  padding: 3px 4px;
  color: #1f2937;
  border-bottom: 1px dashed var(--color-border-1);
}

.metrics__worse {
  color: #ff7d00;
}

.mono {
  font-family: var(--font-mono, 'SFMono-Regular', Menlo, Consolas, monospace);
}

.actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.actions__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.actions__hint {
  flex: 1;
  font-size: 11px;
  color: var(--color-text-3);
  line-height: 1.5;
}

.actions__hint--blocked {
  color: #ff7d00;
}

.decisions,
.audit {
  margin: 0;
  padding: 0;
  list-style: none;
}

.decisions li {
  display: flex;
  gap: 6px;
  align-items: baseline;
  flex-wrap: wrap;
  padding: 4px 0;
  font-size: 11px;
  border-bottom: 1px dashed var(--color-border-1);
}

.decisions__tag {
  flex: none;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--color-fill-2);
  color: var(--color-text-2);
  font-size: 11px;
}

.decisions__tag--bad {
  background: rgba(245, 63, 63, 0.12);
  color: rgb(var(--red-6));
}

.decisions__reason {
  flex: 1;
  min-width: 80px;
  color: #1f2937;
  word-break: break-word;
}

.decisions__meta {
  flex: none;
  color: var(--color-text-4);
}

.audit__row {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 8px;
  padding: 5px 0;
  font-size: 11px;
  border-bottom: 1px dashed var(--color-border-1);
}

.audit__action {
  font-weight: 600;
  color: #1f2937;
}

.audit__state {
  color: var(--color-text-3);
  font-family: var(--font-mono, monospace);
}

.audit__reason {
  grid-column: 1 / -1;
  color: var(--color-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit__time {
  grid-column: 1 / -1;
  color: var(--color-text-4);
}
</style>
