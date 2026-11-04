import type { GeneralSkillAssessmentResult } from '../../knowledge-evolution/types'
export interface AssessmentCase { id?:string;prompt:string;expected:string;output?:string;evidence_ref?:string }
export interface AssessmentItem {
  id:string;label:string;sha256:string;state:string;parent_id?:string;diff?:string;rationale?:string;
  general?:GeneralSkillAssessmentResult;output?:Record<string,unknown>;
  comparison?:{eligible:boolean;clear_gain?:boolean;reason:string;votes?:Array<{judge:number;reason?:string;evidence?:string[];error?:string}>};
  selection_reason?:string;hub_version_id?:string;
}
export interface HubAssessment {
  id:string;state:string;error:string;skill_name:string;source_version_id:string;source_version:string;source_sha256:string;
  mode:'structure'|'text'|'external';cases:AssessmentCase[];policy:{max_rounds:number};budget:{calls?:number;estimated_tokens?:number;elapsed_seconds?:number};
  baseline:AssessmentItem;candidates:AssessmentItem[];winner_id:string;created_at:string;
}
export interface AssessmentHistory { id:string;source_version:string;state:string;mode:string;created_at:string }
