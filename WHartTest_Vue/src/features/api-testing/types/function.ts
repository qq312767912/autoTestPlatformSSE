import type { UserBrief } from './common';

export interface ApiCustomFunction {
  id: number;
  name: string;
  code: string;
  description: string;
  project: number;
  is_active: boolean;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}
