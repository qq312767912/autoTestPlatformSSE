import type { UserBrief } from './common';

export type SyncMode = 'manual' | 'auto';
export type SyncType = 'manual' | 'auto' | 'batch';
export type SyncStatus = 'success' | 'failed' | 'partial';

export interface ApiSyncConfig {
  id: number;
  name: string;
  description: string;
  interface: number;
  testcase: number;
  step: number;
  sync_fields: string[];
  sync_enabled: boolean;
  sync_mode: SyncMode;
  sync_trigger: Record<string, any>;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}

export interface ApiSyncHistory {
  id: number;
  sync_config: number;
  sync_type: SyncType;
  sync_status: SyncStatus;
  sync_fields: string[];
  old_data: Record<string, any>;
  new_data: Record<string, any>;
  error_message: string;
  operator: UserBrief | null;
  sync_time: string;
  [key: string]: any;
}

export interface ApiGlobalSyncConfig {
  id: number;
  name: string;
  description: string;
  project: number;
  sync_fields: string[];
  sync_enabled: boolean;
  sync_mode: SyncMode;
  is_active: boolean;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}
