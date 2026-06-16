<script setup lang="ts">
import { IconClose } from '@arco-design/web-vue/es/icon'

defineEmits(['close'])
</script>

<template>
  <div class="dialog-header px-4 py-3 flex justify-between items-center">
    <span class="dialog-header__title font-medium">添加接口</span>
    <a-button class="dialog-header__close !border-0 !p-0" @click="$emit('close')">
      <template #icon>
        <icon-close />
      </template>
    </a-button>
  </div>
</template>

<style scoped>
.dialog-header {
  background: var(--asd-panel-bg);
  border: 1px solid var(--asd-panel-border);
  border-radius: 0.75rem;
}

.dialog-header__title {
  color: var(--asd-text);
}

.dialog-header__close {
  background: transparent !important;
  color: var(--asd-text-subtle) !important;

  &:hover {
    background: var(--asd-panel-hover) !important;
    color: var(--asd-text) !important;
  }
}
</style>
