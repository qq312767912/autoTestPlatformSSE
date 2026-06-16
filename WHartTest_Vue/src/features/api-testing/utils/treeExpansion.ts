export interface TreeNodeLike<T extends TreeNodeLike<T> = any> {
  id: number;
  children?: T[];
}

const findBranchById = <T extends TreeNodeLike<T>>(
  nodes: T[],
  targetId: number,
): T | null => {
  for (const node of nodes) {
    if (node.id === targetId) {
      return node;
    }

    if (node.children?.length) {
      const matched = findBranchById(node.children, targetId);
      if (matched) {
        return matched;
      }
    }
  }

  return null;
};

const collectBranchIds = <T extends TreeNodeLike<T>>(node: T): number[] => {
  const ids = [node.id];

  if (node.children?.length) {
    for (const child of node.children) {
      ids.push(...collectBranchIds(child));
    }
  }

  return ids;
};

export const collapseTreeBranchIds = <T extends TreeNodeLike<T>>(
  tree: T[],
  expandedIds: number[],
  rootId: number,
): number[] => {
  const branch = findBranchById(tree, rootId);
  if (!branch) {
    return expandedIds.filter((id) => id !== rootId);
  }

  const idsToCollapse = new Set(collectBranchIds(branch));
  return expandedIds.filter((id) => !idsToCollapse.has(id));
};
