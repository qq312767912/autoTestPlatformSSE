import assert from 'node:assert/strict';
import test from 'node:test';

import {
  deduplicateById,
  mergeVisiblePermissionSelection,
  permissionIdToKey,
} from '../src/components/permission/permissionTreeState.ts';

test('搜索后取消可见权限不会移除隐藏权限', () => {
  const result = mergeVisiblePermissionSelection([1, 2, 3], [2], []);

  assert.deepEqual(result, [1, 3]);
});

test('搜索后勾选可见权限只新增当前权限', () => {
  const result = mergeVisiblePermissionSelection(
    [1, 3],
    [2],
    [permissionIdToKey(2), 'model:20'],
  );

  assert.deepEqual(result, [1, 3, 2]);
});

test('重复接口记录按权限 ID 去重', () => {
  const result = deduplicateById([
    { id: 1, name: '查看项目' },
    { id: 1, name: '查看项目' },
    { id: 2, name: '修改项目' },
  ]);

  assert.deepEqual(result, [
    { id: 1, name: '查看项目' },
    { id: 2, name: '修改项目' },
  ]);
});
