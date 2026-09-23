from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Version = Literal['before', 'after']
EvidenceStatus = Literal['direct', 'semantic', 'insufficient']

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class EvidenceRef(Model):
    block_id: str
    quote: str

class SourceBlock(Model):
    block_id: str
    document_id: str
    version: Version
    text: str
    page: int | None = None
    section: str | None = None
    paragraph_index: int | None = None
    table: int | None = None
    cell_range: str | None = None
    sheet: str | None = None
    context: str = ''

class DocumentRecord(Model):
    id: str
    original_name: str
    version: Version
    sha256: str
    format: str
    size: int
    status: Literal['pending', 'processed', 'partial', 'failed'] = 'pending'
    warnings: list[str] = Field(default_factory=list)
    blocks: list[SourceBlock] = Field(default_factory=list)

class Department(Model):
    id: str
    name: str
    parent_name: str | None = None
    version: Version
    source_refs: list[EvidenceRef]

class Function(Model):
    id: str
    department_id: str
    original_text: str
    normalized_description: str
    action: str
    object: str
    scope: str
    role: Literal['execution', 'approval', 'control', 'independent_audit', 'support', 'unknown']
    source_refs: list[EvidenceRef]

class DepartmentChange(Model):
    before_department_ids: list[str]
    after_department_ids: list[str]
    change_type: Literal['preserved', 'renamed', 'transformed', 'split', 'merged', 'created', 'abolished', 'uncertain']
    evidence_status: EvidenceStatus
    explanation: str
    source_refs: list[EvidenceRef]

class FunctionMapping(Model):
    before_function_id: str
    after_function_ids: list[str]
    relation: Literal['preserved', 'transferred', 'partially_covered', 'not_found', 'explicitly_discontinued', 'uncertain']
    covered_aspects: list[str]
    uncovered_aspects: list[str]
    explanation: str
    source_refs: list[EvidenceRef]
    searched_document_ids: list[str] = Field(default_factory=list)
    nearest_after_function_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

class Finding(Model):
    id: str
    type: Literal['potential_loss', 'potential_duplication', 'partial_overlap', 'potential_conflict', 'insufficient_data']
    title: str
    affected_department_ids: list[str]
    before_function_ids: list[str]
    after_function_ids: list[str]
    explanation: str
    evidence_refs: list[EvidenceRef]
    recommendation: str
    evidence_status: EvidenceStatus
    limitations: list[str]
    existed_before: Literal['yes', 'no', 'unknown'] = 'unknown'

class ConclusionItem(Model):
    text: str
    finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

class AnalysisResult(Model):
    departments: list[Department] = Field(default_factory=list)
    functions: list[Function] = Field(default_factory=list)
    department_changes: list[DepartmentChange] = Field(default_factory=list)
    function_mappings: list[FunctionMapping] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    conclusion: list[ConclusionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

class Progress(Model):
    stage: str = 'В очереди'
    processed: int = 0
    total: int = 0
    api_calls: int = 0

class Analysis(Model):
    analysis_id: str
    status: Literal['queued', 'running', 'completed', 'partial', 'failed'] = 'queued'
    created_at: str
    updated_at: str
    documents: list[DocumentRecord] = Field(default_factory=list)
    progress: Progress = Field(default_factory=Progress)
    result: AnalysisResult = Field(default_factory=AnalysisResult)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    model: str
    prompt_version: str
    complete: bool = False

