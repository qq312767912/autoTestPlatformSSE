export type PermissionTreeKey = string | number;

export function permissionIdToKey(id: number): string {
  return `permission:${id}`;
}

export function permissionKeyToId(key: PermissionTreeKey): number | null {
  if (typeof key !== 'string' || !key.startsWith('permission:')) return null;
  const id = Number(key.slice('permission:'.length));
  return Number.isInteger(id) && id > 0 ? id : null;
}

export function mergeVisiblePermissionSelection(
  currentPermissionIds: readonly number[],
  visiblePermissionIds: readonly number[],
  checkedKeys: readonly PermissionTreeKey[],
): number[] {
  const visibleIds = new Set(visiblePermissionIds);
  const nextIds = currentPermissionIds.filter(id => !visibleIds.has(id));

  checkedKeys.forEach(key => {
    const id = permissionKeyToId(key);
    if (id !== null && visibleIds.has(id)) nextIds.push(id);
  });

  return [...new Set(nextIds)];
}

export function deduplicateById<T extends { id: number }>(items: readonly T[]): T[] {
  return [...new Map(items.map(item => [item.id, item])).values()];
}
