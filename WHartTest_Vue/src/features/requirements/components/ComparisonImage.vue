<template>
  <div ref="container" class="comparison-image">
    <img v-if="url" :src="url" :alt="`${side === 'old' ? '旧版' : '新版'}图片 ${digest}`" />
    <span v-else>{{ error || '图片预览加载中…' }}</span>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { comparisonImage } from '../services/comparisonService';
const props = defineProps<{ sessionId: string; side: 'old' | 'new'; rid: string; digest: string }>();
const container = ref<HTMLElement>();
const url = ref('');
const error = ref('');
let observer: IntersectionObserver | undefined;
let generation = 0;
let disposed = false;
async function load() {
  const current = ++generation;
  try {
    const blob = await comparisonImage(props.sessionId, props.side, props.rid);
    if (disposed || current !== generation) return;
    if (url.value) URL.revokeObjectURL(url.value);
    url.value = URL.createObjectURL(blob);
  } catch {
    if (!disposed && current === generation) error.value = '此图片暂无法预览，原图仍保留在导出文档中。';
  }
}
onMounted(() => {
  observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting)) { observer?.disconnect(); void load(); }
  }, { rootMargin: '150px' });
  if (container.value) observer.observe(container.value);
});
watch(() => [props.sessionId, props.side, props.rid], () => {
  if (url.value) URL.revokeObjectURL(url.value);
  url.value = ''; error.value = '';
  void load();
});
onBeforeUnmount(() => { disposed = true; generation++; observer?.disconnect(); if (url.value) URL.revokeObjectURL(url.value); });
</script>
<style scoped>
.comparison-image { min-height: 40px; margin: 8px 0; color: var(--color-text-3); font-size: 12px; }
.comparison-image img { display: block; max-width: 100%; max-height: 320px; object-fit: contain; border: 1px solid var(--color-border-2); background: white; }
</style>
