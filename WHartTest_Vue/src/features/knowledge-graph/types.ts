export type GraphSourceType = 'code_repository' | 'knowledge_document' | 'requirement' | 'test_case';

export interface GraphSource {
  id: string;
  type: GraphSourceType;
  name: string;
  description: string;
  project: { id: number; name: string };
  snapshot: { id: string; commit: string; base_commit: string; created_at: string; crg_version: string };
  status: string;
  stats: { nodes: number; edges: number; changed_symbols: number; affected_files: number; related_tests: number };
  capabilities: string[];
  provenance: { source_type: GraphSourceType; source_id: string; snapshot_id: string; analysis_task_id?: string };
}

export interface GraphProvenance {
  source_type: GraphSourceType;
  source_id: string;
  snapshot_id: string;
  confidence: number;
  location: string;
}

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  qualified_name: string;
  path: string;
  location: { line_start?: number | null; line_end?: number | null };
  language: string;
  is_test: boolean;
  properties: {
    signature?: string;
    parent_name?: string;
    return_type?: string;
    community_id?: number | null;
    risk_score?: number;
    caller_count?: number;
    test_coverage?: string;
    [key: string]: any;
  };
  provenance: GraphProvenance;
}

export interface GraphEdge {
  id: string;
  kind: string;
  source: string;
  target: string;
  confidence: number;
  properties: { line?: number; confidence_tier?: string; target_resolution?: string };
  provenance: GraphProvenance;
}

export interface GraphSnapshot {
  status: string;
  source: GraphSource;
  stats: { nodes: number; edges: number };
  facets: { node_kinds: Record<string, number>; edge_kinds: Record<string, number>; languages: Record<string, number> };
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  duration_ms: number;
  crg_version: string;
}

export interface GraphSourceList {
  results: GraphSource[];
  source_types: Array<{ value: GraphSourceType; label: string; enabled: boolean }>;
}

export interface GraphQuery {
  search?: string;
  nodeKinds?: string[];
  edgeKinds?: string[];
  center?: string;
  depth?: number;
  limit?: number;
}
