import type { UserBrief } from './common';

export interface ApiEnvironment {
  id: number;
  name: string;
  base_url: string;
  verify_ssl: boolean;
  description: string;
  project: number;
  parent: number | null;
  database_config: number | null;
  is_active: boolean;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}

export type EnvironmentVariableType =
  | 'string'
  | 'integer'
  | 'float'
  | 'boolean'
  | 'json'
  | 'list'
  | 'dict';

export interface ApiEnvironmentVariable {
  id: number;
  environment: number;
  name: string;
  value: string;
  type: EnvironmentVariableType;
  description: string;
  is_sensitive: boolean;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}

export interface ApiGlobalRequestHeader {
  id: number;
  name: string;
  value: string;
  description: string;
  is_enabled: boolean;
  project: number;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}
