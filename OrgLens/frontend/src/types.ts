export type Version = 'before' | 'after';
export type EvidenceStatus = 'direct' | 'semantic' | 'insufficient';
export interface EvidenceRef { block_id: string; quote: string }
export interface SourceBlock {
  block_id: string; document_id: string; version: Version; text: string;
  page: number | null; section: string | null; paragraph_index: number | null;
  table: number | null; cell_range: string | null; sheet: string | null; context: string;
}
export interface DocumentRecord {
  id: string; original_name: string; version: Version; sha256: string; format: string;
  size: number; status: 'pending' | 'processed' | 'partial' | 'failed';
  warnings: string[]; blocks: SourceBlock[];
}
export interface Department {
  id: string; name: string; parent_name: string | null; version: Version; source_refs: EvidenceRef[];
}
export interface OrgFunction {
  id: string; department_id: string; original_text: string; normalized_description: string;
  action: string; object: string; scope: string;
  role: 'execution' | 'approval' | 'control' | 'independent_audit' | 'support' | 'unknown';
  source_refs: EvidenceRef[];
}
export interface DepartmentChange {
  before_department_ids: string[]; after_department_ids: string[];
  change_type: 'preserved' | 'renamed' | 'transformed' | 'split' | 'merged' | 'created' | 'abolished' | 'uncertain';
  evidence_status: EvidenceStatus; explanation: string; source_refs: EvidenceRef[];
}
export interface FunctionMapping {
  before_function_id: string; after_function_ids: string[];
  relation: 'preserved' | 'transferred' | 'partially_covered' | 'not_found' | 'explicitly_discontinued' | 'uncertain';
  covered_aspects: string[]; uncovered_aspects: string[]; explanation: string;
  source_refs: EvidenceRef[]; searched_document_ids: string[];
  nearest_after_function_ids: string[]; limitations: string[];
}
export interface Finding {
  id: string; type: 'potential_loss' | 'potential_duplication' | 'partial_overlap' | 'potential_conflict' | 'insufficient_data';
  title: string; affected_department_ids: string[]; before_function_ids: string[];
  after_function_ids: string[]; explanation: string; evidence_refs: EvidenceRef[];
  recommendation: string; evidence_status: EvidenceStatus; limitations: string[];
  existed_before: 'yes' | 'no' | 'unknown';
}
export interface AnalysisResult {
  departments: Department[]; functions: OrgFunction[]; department_changes: DepartmentChange[];
  function_mappings: FunctionMapping[]; findings: Finding[];
  conclusion: { text: string; finding_ids: string[]; evidence_refs: EvidenceRef[] }[];
  limitations: string[];
}
export interface Analysis {
  analysis_id: string; status: 'queued' | 'running' | 'completed' | 'partial' | 'failed';
  created_at: string; updated_at: string; documents: DocumentRecord[];
  progress: { stage: string; processed: number; total: number; api_calls: number };
  result: AnalysisResult; warnings: string[]; error: string | null;
  model: string; prompt_version: string; complete: boolean;
}
export interface Health {
  status: string; ai_configured: boolean; model: string;
  limits: { max_file_mb: number; max_files_per_version: number; max_analysis_chars: number };
}
export interface SourceResponse { block: SourceBlock; document: DocumentRecord }
