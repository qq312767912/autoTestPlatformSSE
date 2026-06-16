<script setup lang="ts">
import type { ApiModule } from '../../types/module'
import ModuleTreeSelect from './ModuleTreeSelect.vue'

interface Props {
  modules: ApiModule[]
  expandedIds: number[]
  selectedId?: number
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false
})

const emit = defineEmits<{
  (e: 'select', module: ApiModule): void
  (e: 'toggle-expand', moduleId: number): void
}>()

const handleSelect = (module: ApiModule) => {
  emit('select', module)
}

const handleToggleExpand = (moduleId: number) => {
  emit('toggle-expand', moduleId)
}
</script>

<template>
  <div class="space-y-1">
    <ModuleTreeSelect
      v-for="module in modules"
      :key="module.id"
      :module="module"
      :expanded-ids="expandedIds"
      :selected-id="selectedId"
      :loading="loading"
      @select="handleSelect"
      @toggle-expand="handleToggleExpand"
    />
  </div>
</template> 