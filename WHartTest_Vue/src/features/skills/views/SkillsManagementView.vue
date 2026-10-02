<template>
  <div class="skills-management-view">
    <div class="page-header">
      <div class="page-header__main">
        <h2>{{ pageText.pageTitle }}</h2>
        <p class="page-description">{{ pageText.pageDescription }}</p>
      </div>
    </div>

    <div v-if="currentProjectId" class="skills-container">
      <!--
        这一页只负责"管理项目可用的通用 Skill"：上传、Git 导入、从 Skill 中心安装、
        启停与删除。Skill 的生产控制台（候选版本、评测门禁、发布治理）不在这一页——
        它属于数据飞轮，入口在「数据飞轮 → Skill Hub」，避免同一套版本治理两处并存。
      -->
      <SkillManager :project-id="currentProjectId" :key="`skills-${currentProjectId}`" />
    </div>
    <div v-else class="empty-state">
      <icon-apps style="font-size: 64px; color: #c0c4cc" />
      <p>{{ pageText.selectProjectFirst }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { SkillManager } from '@/features/skills'
import { useProjectStore } from '@/store/projectStore'
import { useAppI18n } from '@/composables/useAppI18n'

const projectStore = useProjectStore()
const { isEnglish } = useAppI18n()
const currentProjectId = computed(() => projectStore.currentProjectId)

const pageText = computed(() => (
  isEnglish.value
    ? {
        pageTitle: 'Skills Management',
        pageDescription: 'Manage the general Skills available to this project: upload a package, import from Git, install from the Skill Hub, then enable, disable or delete them.',
        selectProjectFirst: 'Select a project from the navigation first',
      }
    : {
        pageTitle: 'Skills 管理',
        pageDescription: '管理本项目可用的通用 Skill：上传、从 Git 导入、从 Skill 中心安装，以及启停与删除。',
        selectProjectFirst: '请先在导航栏选择一个项目',
      }
))
</script>

<style scoped>
.skills-management-view {
  padding: 24px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
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

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: var(--color-text-3);
}
</style>
