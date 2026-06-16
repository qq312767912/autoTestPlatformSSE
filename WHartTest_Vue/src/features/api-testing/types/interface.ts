import type { UserBrief } from './common';

export type InterfaceType = 'http' | 'sql';
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
export type SqlMethod = 'fetchone' | 'fetchmany' | 'fetchall' | 'insert' | 'update' | 'delete';
export type ApiBodyType = 'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary';

export interface ApiKeyValuePair {
  key: string;
  value: string;
  enabled?: boolean;
  description?: string;
  [key: string]: any;
}

export interface ApiRequestBody {
  type: ApiBodyType;
  content: any;
}

export interface ApiInterface {
  id: number;
  name: string;
  type: InterfaceType;

  // HTTP fields
  method: HttpMethod | null;
  url: string | null;
  headers: ApiKeyValuePair[];
  params: ApiKeyValuePair[];
  body: ApiRequestBody;

  // SQL fields
  sql_method: SqlMethod | null;
  sql: string | null;
  sql_params: Record<string, any>;
  sql_size: number;

  // httprunner fields
  setup_hooks: string[];
  teardown_hooks: string[];
  variables: Record<string, any>;
  validators: any[];
  extract: Record<string, string>;

  // Relationships
  project: number;
  module: number | null;
  module_info?: { id: number; name: string } | null;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}

export interface ApiInterfaceResult {
  id: number;
  interface: number;
  environment_id: number | null;
  success: boolean;
  elapsed: number;
  request_data: Record<string, any>;
  response_data: Record<string, any>;
  validation_results: any[];
  extracted_variables: Record<string, any>;
  executed_by: UserBrief | null;
  executed_at: string;
  [key: string]: any;
}
