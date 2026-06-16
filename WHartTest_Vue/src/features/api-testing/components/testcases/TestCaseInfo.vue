<script setup lang="ts">
import {
  IconEdit,
  IconFire,
  IconApps,
  IconTags,
  IconFolder,
} from '@arco-design/web-vue/es/icon'
import type { ApiTestCase } from '../../types/testcase'

interface Props {
  testCase: ApiTestCase
}

defineProps<Props>()
</script>

<template>
  <div class="space-y-4">
    <!-- 基本信息 -->
    <div class="flex items-center gap-6">
      <div class="flex items-center gap-2">
        <icon-edit class="info-secondary" />
        <span class="text-xl font-medium">{{ testCase.name }}</span>
      </div>
      <a-tag :color="testCase.priority === 'P0' ? 'red' : testCase.priority === 'P1' ? 'orange' : testCase.priority === 'P2' ? 'blue' : 'green'">
        <template #icon>
          <icon-fire />
        </template>
        {{ testCase.priority }}
      </a-tag>
    </div>

    <!-- 描述信息 -->
    <div v-if="testCase.description" class="info-secondary">
      {{ testCase.description }}
    </div>

    <!-- 分组和标签信息 -->
    <div class="flex items-center gap-6">
      <div v-if="(testCase as any).group_info" class="flex items-center gap-2 info-secondary">
        <icon-folder />
        <span>{{ (testCase as any).group_info.name }}</span>
      </div>
      <div v-if="(testCase as any).module_info" class="flex items-center gap-2 info-secondary">
        <icon-apps />
        <span>{{ (testCase as any).module_info.name }}</span>
      </div>
      <div v-if="(testCase as any).tags_info?.length" class="flex items-center gap-2">
        <icon-tags class="info-secondary" />
        <a-space>
          <a-tag
            v-for="tag in (testCase as any).tags_info"
            :key="tag.id"
            :color="tag.color"
          >
            {{ tag.name }}
          </a-tag>
        </a-space>
      </div>
    </div>

    <!-- 创建和更新信息 -->
    <div class="flex items-center gap-6 text-sm info-secondary">
      <span>创建人：{{ (testCase as any).created_by_name }}</span>
      <span>创建时间：{{ new Date(testCase.created_at).toLocaleString() }}</span>
      <span>更新时间：{{ new Date(testCase.updated_at).toLocaleString() }}</span>
    </div>
  </div>
</template>

<style scoped>
.info-secondary {
  color: var(--color-text-3);
}
</style>
