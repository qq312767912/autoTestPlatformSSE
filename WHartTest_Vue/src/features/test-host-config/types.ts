export interface TestHostMapping {
  id: number;
  system_name: string;
  hostname: string;
  ipv4: string;
  enabled: boolean;
  remark: string;
  created_by_name: string;
  updated_by_name: string;
  created_at: string;
  updated_at: string;
}

export interface MappingPayload {
  system_name: string;
  hostname: string;
  ipv4: string;
  enabled: boolean;
  remark: string;
}

export interface NodeCounts {
  synced: number;
  pending: number;
  failed: number;
  offline: number;
}

export interface TestHostOverview {
  draft_revision: number;
  published_version: number | null;
  published_checksum: string;
  published_at: string | null;
  published_by: string;
  pending_changes: number;
  node_counts: NodeCounts;
}

export interface SnapshotRow {
  system_name: string;
  hostname: string;
  ipv4: string;
  remark: string;
}

export interface MappingChange {
  before: SnapshotRow;
  after: SnapshotRow;
}

export interface TestHostDiff {
  added: SnapshotRow[];
  changed: MappingChange[];
  removed: SnapshotRow[];
  count: number;
}

export interface TestHostVersion {
  id: number;
  version: number;
  source_draft_revision: number;
  snapshot: SnapshotRow[];
  checksum: string;
  status: string;
  source_version_number: number | null;
  created_by_name: string;
  created_at: string;
  published_at: string | null;
  error_message: string;
}

export interface TestHostNode {
  node_id: string;
  node_type: string;
  display_name: string;
  applied_version: number | null;
  applied_checksum: string;
  status: string;
  effective_status: 'synced' | 'pending' | 'failed' | 'offline';
  message: string;
  details: Record<string, unknown>;
  last_seen_at: string;
  updated_at: string;
}

export interface TestHostDiagnosis {
  id: number;
  mapping: number;
  hostname: string;
  ipv4: string;
  status: 'pending' | 'running' | 'success' | 'failed';
  result: Record<string, unknown>;
  error_code: string;
  error_message: string;
  duration_ms: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
