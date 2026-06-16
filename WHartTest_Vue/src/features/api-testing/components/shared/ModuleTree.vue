<template>
  <div class="module-tree">
    <div class="tree-header">
      <span class="tree-title">{{ title }}</span>
      <a-button
        v-if="showAdd"
        type="text"
        size="mini"
        @click="$emit('add')"
      >
        <template #icon><icon-plus /></template>
      </a-button>
    </div>
    <a-input
      v-if="showSearch"
      v-model="searchText"
      :placeholder="searchPlaceholder"
      size="small"
      allow-clear
      class="tree-search"
    >
      <template #prefix><icon-search /></template>
    </a-input>
    <a-tree
      :data="filteredTree"
      :selected-keys="selectedKeys"
      :show-line="showLine"
      block-node
      @select="handleSelect"
    >
      <template #title="nodeData">
        <span class="tree-node-title">{{ nodeData.title }}</span>
      </template>
      <template #extra="nodeData">
        <slot name="extra" :node="nodeData" />
      </template>
    </a-tree>
    <a-empty v-if="filteredTree.length === 0" class="tree-empty" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';

interface TreeNode {
  key: number | string;
  title: string;
  children?: TreeNode[];
  [k: string]: any;
}

const props = withDefaults(defineProps<{
  treeData: TreeNode[];
  selectedKeys?: (number | string)[];
  title?: string;
  showAdd?: boolean;
  showSearch?: boolean;
  showLine?: boolean;
  searchPlaceholder?: string;
}>(), {
  selectedKeys: () => [],
  title: '',
  showAdd: false,
  showSearch: true,
  showLine: false,
  searchPlaceholder: 'Search...',
});

const emit = defineEmits<{
  (e: 'select', keys: (number | string)[], node: any): void;
  (e: 'add'): void;
}>();

const searchText = ref('');

function filterNodes(nodes: TreeNode[], query: string): TreeNode[] {
  if (!query) return nodes;
  const lq = query.toLowerCase();
  return nodes.reduce<TreeNode[]>((acc, node) => {
    const childMatches = node.children ? filterNodes(node.children, query) : [];
    if (node.title.toLowerCase().includes(lq) || childMatches.length > 0) {
      acc.push({ ...node, children: childMatches.length > 0 ? childMatches : node.children });
    }
    return acc;
  }, []);
}

const filteredTree = computed(() => filterNodes(props.treeData, searchText.value));

function handleSelect(keys: (number | string)[], extra: any) {
  emit('select', keys, extra);
}
</script>

<style scoped>
.module-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
}
.tree-title {
  font-weight: 600;
  font-size: 14px;
}
.tree-search {
  margin-bottom: 8px;
}
.tree-empty {
  margin-top: 24px;
}
.tree-node-title {
  user-select: none;
}
</style>
