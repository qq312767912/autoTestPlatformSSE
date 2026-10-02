<template>
  <div class="skills-management-view" :class="{ 'skills-management-view--console': mode === 'console' }">
    <div class="page-header">
      <div class="page-header__main">
        <h2>{{ pageText.pageTitle }}</h2>
        <p class="page-description">{{ pageText.pageDescription }}</p>
      </div>
      <a-radio-group v-if="currentProjectId" v-model="mode" type="button" size="small">
        <a-radio value="console">{{ pageText.consoleTab }}</a-radio>
        <a-radio value="legacy">{{ pageText.legacyTab }}</a-radio>
      </a-radio-group>
    </div>

    <div v-if="currentProjectId" class="skills-container" :class="{ 'skills-container--tight': mode === 'console' }">
      <!--
        默认进生产控制台（T18 / R10）。存量管理页保留为第二个页签：商店浏览安装、
        API Key 确认、启停与删除这些动作只在那边有入口，直接删掉会变成功能回退。
      -->
      <SkillHubConsole v-if="mode === 'console'" :project-id="currentProjectId" :key="`console-${currentProjectId}`" />
      <SkillManager v-else :project-id="currentProjectId" :key="`legacy-${currentProjectId}`" />
    </div>
    <div v-else class="empty-state">
      <icon-apps style="font-size: 64px; color: #c0c4cc" />
      <p>{{ pageText.selectProjectFirst }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { SkillHubConsole, SkillManager } from '@/features/skills'
import { useProjectStore } from '@/store/projectStore'
import { useAppI18n } from '@/composables/useAppI18n'

const projectStore = useProjectStore()
const { isEnglish } = useAppI18n()
const currentProjectId = computed(() => projectStore.currentProjectId)
const mode = ref<'console' | 'legacy'>('console')

const pageText = computed(() => (
  isEnglish.value
    ? {
        pageTitle: 'Skill Hub',
        pageDescription: 'Skill catalog, version diff, evaluation gate and release governance. Candidates cannot go live without passing the gate and lead approval.',
        selectProjectFirst: 'Select a project from the navigation first',
        consoleTab: 'Console',
        legacyTab: 'Inventory',
      }
    : {
        pageTitle: 'Skill Hub',
        pageDescription: '能力目录、版本差异、评测门禁与发布治理。候选不可能绕过门禁与测试负责人审批直接生效。',
        selectProjectFirst: '请先在导航栏选择一个项目',
        consoleTab: '生产控制台',
        legacyTab: '存量管理',
      }
))
</script>

<style scoped>
.skills-management-view {
  padding: 24px;
}

.skills-management-view--console {
  /* 控制台是三栏工作区，需要撑满可视高度；页签切换回存量管理时不受影响。 */
  display: flex;
  flex-direction: column;
  height: 100vh;
  box-sizing: border-box;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 24px;
}

.skills-management-view--console .page-header {
  margin-bottom: 12px;
}

.page-header h2 {
  margin: 0 0 8px 0;
}

.page-description {
  color: var(--color-text-2);
  margin: 0;
}

.skills-container {
  background: var(--color-bg-2);
  border-radius: 8px;
  min-height: 400px;
}

.skills-container--tight {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: var(--color-text-3);
}
</style>
