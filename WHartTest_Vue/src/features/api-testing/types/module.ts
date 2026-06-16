import type { UserBrief } from './common';

export interface ApiModule {
  id: number;
  name: string;
  project: number;
  parent: number | null;
  description: string;
  created_by: UserBrief | null;
  created_at: string;
  updated_at: string;
  create_time?: string;
  update_time?: string;
  children?: ApiModule[];
  [key: string]: any;
}
