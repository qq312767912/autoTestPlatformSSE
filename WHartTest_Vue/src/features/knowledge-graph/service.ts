import { request } from '@/utils/request';
import type { GraphQuery, GraphSnapshot, GraphSourceList } from './types';

const base = '/code-analysis/graph-sources';

export async function getGraphSources(project?: number): Promise<GraphSourceList> {
  const response = await request<GraphSourceList>({
    url: `${base}/`, method: 'GET', params: project ? { project } : undefined,
  });
  if (!response.success || !response.data) throw new Error(response.error || '读取图谱数据源失败');
  return response.data;
}

export async function getGraphSnapshot(sourceId: string, query: GraphQuery = {}): Promise<GraphSnapshot> {
  const response = await request<GraphSnapshot>({
    url: `${base}/${encodeURIComponent(sourceId)}/graph/`,
    method: 'GET',
    params: {
      search: query.search || undefined,
      node_kinds: query.nodeKinds?.join(',') || undefined,
      edge_kinds: query.edgeKinds?.join(',') || undefined,
      center: query.center || undefined,
      depth: query.depth || 1,
      limit: query.limit || 120,
    },
  });
  if (!response.success || !response.data) throw new Error(response.error || '读取图谱失败');
  return response.data;
}
