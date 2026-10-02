<template>
  <div class="skills-management-view">
    <div v-if="currentProjectId" class="skills-container">
      <!--
        Skill Hub（本页）负责"上传和发现公开 Skill"：上传、Git 导入、从 Skill 商店安装、
        按来源与能力阶段筛选，以及启停与删除。Skill 的进化（候选版本、评测门禁、发布治理）
        不在这一页——它在数据飞轮的「Skill 进化工坊」页签里，避免同一套版本治理两处并存。
        数据飞轮下也挂了同名页签，与本页是同一份实现（`SkillManager`）。
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
        selectProjectFirst: 'Select a project from the navigation first',
      }
    : {
        selectProjectFirst: '请先在导航栏选择一个项目',
      }
))
</script>

<style scoped>
.skills-management-view {
  padding: 24px;
  /* 2026-10-03：父级 .content 的约定是「固定高度 + overflow: hidden，让子组件自行控制滚动」，
     但本页此前只设了 padding、既没撑满也没滚动 → 内容一高就被直接裁掉且滚不动
     （用户反馈「skillhub 没有滚轮」，页面放大时立刻暴露）。
     补上「撑满容器 + 自己滚」即可，别再往父级加滚动（会影响所有页面）。 */
  height: 100%;
  box-sizing: border-box;
  overflow-y: auto;
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
