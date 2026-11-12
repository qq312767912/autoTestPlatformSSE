<template>
  <div :class="['diff-cell', row ? `diff-${row.kind}` : 'diff-empty']">
    <span class="diff-number">{{ row ? (side === 'old' ? row.old_line : row.new_line) : '' }}</span>
    <span class="diff-sign">{{ row?.kind === 'delete' ? '−' : row?.kind === 'add' ? '+' : '' }}</span>
    <div v-if="row" class="diff-content">
      <div v-if="row.table" class="table-wrap">
        <table><tbody>
          <tr v-for="(cells, i) in row.table" :key="i">
            <td v-for="(cell, j) in cells" :key="j" :colspan="Math.min(Math.max(cell.span, 1), 50)">{{ cell.text }}</td>
          </tr>
        </tbody></table>
      </div>
      <div v-else class="diff-text"><template v-if="row.segments"><span v-for="(part, i) in row.segments" :key="i" :class="{ 'word-change': part.changed }">{{ part.text }}</span></template><template v-else>{{ row.text }}</template></div>
      <ComparisonImage v-for="image in row.images || []" :key="image.rid" :session-id="sessionId" :side="side" :rid="image.rid" :digest="image.digest" />
    </div>
  </div>
</template>
<script setup lang="ts">
import type { DiffRow } from '../services/comparisonService';
import ComparisonImage from './ComparisonImage.vue';
defineProps<{ row: DiffRow | null; side: 'old' | 'new'; sessionId: string }>();
</script>
<style scoped>
.diff-cell { display: grid; grid-template-columns: 42px 20px minmax(0, 1fr); min-width: 0; min-height: 30px; font-size: 13px; line-height: 1.65; }
.diff-number { padding: 5px 6px; text-align: right; color: var(--color-text-3); border-right: 1px solid var(--color-border-2); font-family: ui-monospace, monospace; user-select: none; }
.diff-sign { padding: 5px 4px; font-family: ui-monospace, monospace; }
.diff-content { min-width: 0; padding: 5px 10px; }
.diff-text { white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.diff-add { background: #ecfdf3; color: #12633f; }
.diff-delete { background: #fff1f1; color: #a12834; }
.diff-context { color: var(--color-text-2); background: var(--color-bg-2); }
.diff-empty { background: var(--color-fill-1); }
.diff-add .word-change { background: #b5ebc8; border-radius: 2px; }
.diff-delete .word-change { background: #ffc7cb; border-radius: 2px; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; color: inherit; }
td { border: 1px solid var(--color-border-3); padding: 5px 7px; white-space: pre-wrap; overflow-wrap: anywhere; vertical-align: top; }
</style>
