<template>
  <div class="catalog">
    <div class="catalog__filters">
      <a-input-search
        :model-value="keyword"
        :placeholder="'搜索 Skill 名称或描述'"
        allow-clear
        size="small"
        @update:model-value="(value: string) => emit('update:keyword', value)"
      />
      <a-select
        :model-value="stageFilter"
        size="small"
        allow-clear
        placeholder="按业务阶段筛选"
        @update:model-value="(value: any) => emit('update:stageFilter', value ?? '')"
      >
        <a-option v-for="item in stageOptions" :key="item.value" :value="item.value">
          {{ item.label }}
        </a-option>
      </a-select>
    </div>

    <div class="catalog__summary">
      <span>{{ total }} 个 Skill</span>
      <a-link size="small" :disabled="loading" @click="emit('refresh')">
        {{ loading ? '加载中…' : '刷新' }}
      </a-link>
    </div>

    <div class="catalog__body">
      <!-- 加载态 -->
      <div v-if="loading && !entries.length" class="catalog__state">
        <a-spin dot />
        <p>正在读取能力目录…</p>
      </div>

      <!-- 错误态：如实报错，不用空列表冒充"没有 Skill" -->
      <div v-else-if="error" class="catalog__state catalog__state--error">
        <icon-exclamation-circle />
        <p>{{ error }}</p>
        <a-button size="mini" @click="emit('refresh')">重试</a-button>
      </div>

      <!-- 空态 -->
      <div v-else-if="!total" class="catalog__state">
        <icon-apps />
        <p>{{ hasFilter ? '没有符合筛选条件的 Skill' : '该项目还没有 Skill' }}</p>
        <p v-if="!hasFilter" class="catalog__hint">可用上方「上传候选」按两阶段流程引入</p>
      </div>

      <template v-else>
        <div v-for="group in groups" :key="group.key" class="catalog__group">
          <div class="catalog__group-title">
            <span>{{ group.label }}</span>
            <span class="catalog__group-count">{{ group.items.length }}</span>
          </div>
          <button
            v-for="item in group.items"
            :key="item.id"
            type="button"
            class="catalog__item"
            :class="{ 'catalog__item--active': item.id === selectedSkillId }"
            @click="emit('select', item.id)"
          >
            <div class="catalog__item-head">
              <span class="catalog__item-name" :title="item.name">{{ item.name }}</span>
              <span v-if="!item.is_active" class="catalog__flag catalog__flag--muted">已停用</span>
            </div>
            <div class="catalog__item-desc">{{ item.description || '无描述' }}</div>
            <div class="catalog__item-meta">
              <span v-if="item.activeVersion" class="catalog__flag catalog__flag--active">
                v{{ item.activeVersion.version }} 生效中
              </span>
              <span v-else class="catalog__flag catalog__flag--muted">无生效版本</span>
              <span v-if="item.candidateCount" class="catalog__flag catalog__flag--pending">
                {{ item.candidateCount }} 个候选
              </span>
              <span v-if="item.quarantinedCount" class="catalog__flag catalog__flag--danger">
                {{ item.quarantinedCount }} 个隔离
              </span>
            </div>
            <div v-if="item.versionsError" class="catalog__item-error">
              {{ item.versionsError }}
            </div>
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  IconApps,
  IconExclamationCircle,
} from '@arco-design/web-vue/es/icon'

import type { SkillCatalogEntry } from '../../types/hub'
import { STAGE_LABELS, UNSTAGED_KEY, WORKFLOW_STAGES, stageLabel } from '../../utils/stages'

const props = defineProps<{
  entries: SkillCatalogEntry[]
  loading: boolean
  error: string
  selectedSkillId: number | null
  keyword: string
  stageFilter: string
}>()

const emit = defineEmits<{
  (e: 'select', skillId: number): void
  (e: 'refresh'): void
  (e: 'update:keyword', value: string): void
  (e: 'update:stageFilter', value: string): void
}>()

const stageOptions = computed(() => {
  const values = Object.keys(STAGE_LABELS)
  return values.map((value) => ({ value, label: STAGE_LABELS[value] }))
})

const hasFilter = computed(() => Boolean(props.keyword || props.stageFilter))
const total = computed(() => props.entries.length)

/** 按业务阶段分组，全链路测试阶段按执行顺序排在前面，其余按字典序。 */
const groups = computed(() => {
  const buckets = new Map<string, SkillCatalogEntry[]>()
  for (const entry of props.entries) {
    const key = entry.stage || UNSTAGED_KEY
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key)!.push(entry)
  }

  // 链路阶段顺序从展示层那份唯一副本取，不在本文件另抄一份字面量。
  const order = WORKFLOW_STAGES
  const keys = [...buckets.keys()].sort((a, b) => {
    const ai = order.indexOf(a)
    const bi = order.indexOf(b)
    if (ai !== -1 && bi !== -1) return ai - bi
    if (ai !== -1) return -1
    if (bi !== -1) return 1
    if (a === UNSTAGED_KEY) return 1
    if (b === UNSTAGED_KEY) return -1
    return a.localeCompare(b)
  })

  return keys.map((key) => ({
    key,
    label: key === UNSTAGED_KEY ? '未声明阶段' : stageLabel(key),
    items: buckets.get(key)!.sort((a, b) => a.name.localeCompare(b.name)),
  }))
})
</script>

<style scoped>
.catalog {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.catalog__filters {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 12px 8px;
  border-bottom: 1px solid var(--color-border-1);
}

.catalog__summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--color-text-3);
  border-bottom: 1px solid var(--color-border-1);
}

.catalog__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
}

.catalog__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 48px 16px;
  color: var(--color-text-3);
  font-size: 13px;
  text-align: center;
}

.catalog__state--error {
  color: rgb(var(--red-6));
}

.catalog__hint {
  font-size: 12px;
  color: var(--color-text-4);
  margin: 0;
}

.catalog__group + .catalog__group {
  margin-top: 12px;
}

.catalog__group-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-3);
  letter-spacing: 0.02em;
}

.catalog__group-count {
  font-weight: 400;
}

.catalog__item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 10px;
  margin-bottom: 6px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s;
  font: inherit;
  color: inherit;
}

.catalog__item:hover {
  background: var(--color-fill-1);
}

.catalog__item--active {
  background: rgba(22, 119, 255, 0.06);
  border-color: #1677ff;
}

.catalog__item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.catalog__item-name {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog__item-desc {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog__item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.catalog__item-error {
  margin-top: 6px;
  font-size: 11px;
  color: rgb(var(--red-6));
}

.catalog__flag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 3px;
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
}

.catalog__flag--active {
  color: #00b42a;
  background: rgba(0, 180, 42, 0.1);
}

.catalog__flag--pending {
  color: #ff7d00;
  background: rgba(255, 125, 0, 0.12);
}

.catalog__flag--danger {
  color: rgb(var(--red-6));
  background: rgba(245, 63, 63, 0.1);
}

.catalog__flag--muted {
  color: var(--color-text-3);
  background: var(--color-fill-2);
}
</style>
