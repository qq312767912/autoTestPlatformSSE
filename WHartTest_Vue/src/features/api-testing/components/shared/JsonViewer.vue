<template>
  <div class="json-viewer">
    <div v-if="showToolbar" class="json-toolbar">
      <a-button-group size="mini">
        <a-button @click="expandAll">{{ expandLabel }}</a-button>
        <a-button @click="collapseAll">{{ collapseLabel }}</a-button>
        <a-button @click="copyJson">
          <template #icon><icon-copy /></template>
        </a-button>
      </a-button-group>
    </div>
    <pre ref="preRef" class="json-content"><code>{{ formattedJson }}</code></pre>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { Message } from '@arco-design/web-vue';

const props = withDefaults(defineProps<{
  data: any;
  indent?: number;
  showToolbar?: boolean;
  expandLabel?: string;
  collapseLabel?: string;
  maxHeight?: string;
}>(), {
  indent: 2,
  showToolbar: true,
  expandLabel: 'Expand',
  collapseLabel: 'Collapse',
  maxHeight: '400px',
});

const preRef = ref<HTMLPreElement>();
const collapsed = ref(false);

const formattedJson = computed(() => {
  try {
    if (collapsed.value) {
      return JSON.stringify(props.data);
    }
    return JSON.stringify(props.data, null, props.indent);
  } catch {
    return String(props.data);
  }
});

function expandAll() {
  collapsed.value = false;
}

function collapseAll() {
  collapsed.value = true;
}

async function copyJson() {
  try {
    const text = JSON.stringify(props.data, null, props.indent);
    await navigator.clipboard.writeText(text);
    Message.success('Copied');
  } catch {
    Message.error('Copy failed');
  }
}
</script>

<style scoped>
.json-viewer {
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
  overflow: hidden;
}
.json-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 4px 8px;
  background: var(--color-fill-1);
  border-bottom: 1px solid var(--color-border-2);
}
.json-content {
  margin: 0;
  padding: 12px;
  max-height: v-bind(maxHeight);
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  background: var(--color-bg-2);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
