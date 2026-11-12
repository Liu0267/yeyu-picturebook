"""A/B contract v0.1.0; all coordinates are normalized, top-left origin."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
State = Literal['queued', 'running', 'needs_review', 'succeeded', 'failed', 'cancelled', 'expired']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Region(Model):
    x1: Unit = 0
    y1: Unit = 0
    x2: Unit = 1
    y2: Unit = 1

    @model_validator(mode='after')
    def ordered(self):
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError('region must have positive area')
        return self


class SourceRef(Model):
    ref_id: str
    page_id: str
    kind: Literal['original_text', 'original_image']
    locator: str
    region: Region
    excerpt: str = ''


class OCRLine(Model):
    line_id: str
    raw_text: str
    confidence: Unit | None = None
    region: Region
    suggested_text: str | None = None
    confirmed_text: str | None = None


class Page(Model):
    page_id: str
    sequence: int = Field(ge=1)
    printed_page: str | None = None
    page_revision: int = Field(default=1, ge=1)
    image_asset_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_locator: str
    ocr_engine: str
    ocr_lines: list[OCRLine] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    review_status: Literal['needs_review', 'reviewed'] = 'needs_review'


class Fact(Model):
    fact_id: str
    page_id: str
    claim: str
    kind: Literal['text_explicit', 'visual_observation', 'inference']
    source_ref_ids: list[str] = Field(min_length=1)
    status: Literal['draft', 'reviewed'] = 'draft'
    reviewed_by: str | None = None

    @model_validator(mode='after')
    def audit(self):
        if self.status == 'reviewed' and (not self.reviewed_by or self.kind == 'inference'):
            raise ValueError('reviewed facts require a reviewer; inference stays draft')
        return self


class Entity(Model):
    entity_id: str
    name: str
    description: str
    source_ref_ids: list[str] = Field(min_length=1)


class Event(Model):
    event_id: str
    sequence: int = Field(ge=1)
    description: str
    fact_ids: list[str] = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)


class BookManifest(Model):
    schema_version: str = '0.1.0'
    book_id: str
    title: str
    revision: int = Field(default=1, ge=1)
    is_mock: bool = False
    rights_status: str = 'user_supplied_local_research'
    external_upload_allowed: bool = False
    source_filename: str
    source_sha256: str
    pages: list[Page]
    source_refs: list[SourceRef] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode='after')
    def references(self):
        groups = [(self.pages, 'page_id'), (self.source_refs, 'ref_id'),
                  (self.facts, 'fact_id'), (self.entities, 'entity_id'), (self.events, 'event_id')]
        for items, attr in groups:
            if len({getattr(x, attr) for x in items}) != len(items):
                raise ValueError(f'duplicate {attr}')
        pages = {x.page_id for x in self.pages}
        refs = {x.ref_id: x for x in self.source_refs}
        facts = {x.fact_id for x in self.facts}
        entities = {x.entity_id for x in self.entities}
        if len({x.sequence for x in self.pages}) != len(self.pages):
            raise ValueError('duplicate page sequence')
        for ref in self.source_refs:
            if ref.page_id not in pages:
                raise ValueError('source refers to unknown page')
        for fact in self.facts:
            if fact.page_id not in pages or any(r not in refs or refs[r].page_id != fact.page_id for r in fact.source_ref_ids):
                raise ValueError('fact evidence must belong to its page')
        for entity in self.entities:
            if any(r not in refs for r in entity.source_ref_ids):
                raise ValueError('entity source missing')
        for event in self.events:
            if not set(event.fact_ids) <= facts or not set(event.entity_ids) <= entities:
                raise ValueError('event reference missing')
        return self


class ShotSpec(Model):
    shot_id: str
    book_id: str
    manifest_revision: int = Field(ge=1)
    page_id: str
    page_revision: int = Field(ge=1)
    fact_ids: list[str] = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)
    reference_asset_id: str
    end_keyframe_asset_id: str | None = None
    reference_region: Region
    narration: str
    creative_additions: list[str] = Field(default_factory=list)
    prompt_version: str = 'source-bound-v1'
    prompt: str
    negative_prompt: str
    target_duration_ms: int = Field(ge=1000, le=20000)
    status: Literal['needs_review', 'approved'] = 'needs_review'


class ClipAsset(Model):
    clip_id: str
    book_id: str
    page_id: str
    shot_id: str
    manifest_revision: int
    page_revision: int
    asset_version: int = Field(ge=1)
    duration_ms: int = Field(gt=0)
    status: Literal['needs_review', 'approved', 'failed', 'expired']
    asset_id: str
    is_mock: bool
    provenance: Literal['synthetic_mock', 'model_generated']
    motion_status: Literal['not_assessed','passed','failed','not_required'] = 'not_assessed'
    run_id: str
    model_call_id: str | None = None
    reviewed_by: str | None = None
    audio_asset_id: str | None = None
    subtitle_asset_id: str | None = None
    source_video_asset_id: str | None = None

    @model_validator(mode='after')
    def provenance_check(self):
        if self.is_mock != (self.provenance == 'synthetic_mock'):
            raise ValueError('mock label contradicts provenance')
        if not self.is_mock and not self.model_call_id:
            raise ValueError('real generation needs model call id')
        if self.status == 'approved' and not self.reviewed_by:
            raise ValueError('publication requires review')
        return self


class VisualClaim(Model):
    claim: str
    region: Region
    observed_by: str
    method: Literal['human_frame_review', 'vlm_frame_review']


class FrameEvidence(Model):
    frame_id: str
    clip_id: str
    asset_version: int = Field(ge=1)
    t_ms: int = Field(ge=0)
    image_asset_id: str
    video_sha256: str
    visual_claims: list[VisualClaim] = Field(default_factory=list)
    status: Literal['needs_review', 'reviewed', 'expired'] = 'needs_review'
    is_mock: bool


class ErrorInfo(Model):
    code: str
    message: str
    retryable: bool = False


class RunRecord(Model):
    run_id: str
    step: str
    model_version: str | None = None
    input_hash: str
    status: State
    error: ErrorInfo | None = None
    attempts: int = 0
    max_attempts: int = 2
    created_at: str
    updated_at: str
    payload: dict
    outputs: list[str] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    is_mock: bool = False


class Envelope(Model):
    request_id: str
    status: Literal['ok', 'accepted', 'error']
    data: dict | list | None = None
    error: ErrorInfo | None = None
    warnings: list[str] = Field(default_factory=list)


class BookCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=250)


class PageReview(Model):
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1)
    corrections: dict[str, str] = Field(default_factory=dict)
    reading_order: list[str] | None = None
    reviewed_fact_ids: list[str] = Field(default_factory=list)


class ClipCreate(Model):
    shot_id: str
    mode: Literal['mock', 'real'] = 'mock'
    idempotency_key: str = Field(min_length=1, max_length=200)


class ClipReview(Model):
    expected_asset_version: int = Field(ge=1)
    reviewer: str = Field(min_length=1)
    claims_by_frame: dict[str, list[VisualClaim]]
    audit_resolution_notes: dict[str,str] = Field(default_factory=dict)
    motion_review: Literal['not_assessed','passed','failed'] = 'not_assessed'
    motion_notes: str = ''


class ShotReview(Model):
    expected_page_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1)


class NarrationCreate(Model):
    expected_asset_version: int = Field(ge=1)


class VisibleObject(Model):
    name: str
    description: str
    bbox: tuple[Unit,Unit,Unit,Unit]

    @model_validator(mode='after')
    def ordered_box(self):
        Region(x1=self.bbox[0],y1=self.bbox[1],x2=self.bbox[2],y2=self.bbox[3])
        return self


class PageObservation(Model):
    visible_objects: list[VisibleObject]
    explicit_text: str
    visual_summary: str
    uncertainties: list[str]


class NarrationAsset(Model):
    narration_id: str
    book_id: str
    page_id: str
    page_revision: int
    shot_id: str
    run_id: str
    model_id: str
    audio_asset_id: str
    text: str
    speaker: str
    duration_ms: int = Field(gt=0)
    sample_rate: int = Field(gt=0)
    status: Literal['needs_review','expired'] = 'needs_review'
    is_mock: bool = False



class PreprocessCreate(Model):
    expected_page_revision: int = Field(ge=1)
    rotation_clockwise: Literal[0,90,180,270] = 0
    chinese_text_region: Region | None = None
    corners_after_rotation: list[tuple[Unit,Unit]] | None = Field(default=None,min_length=4,max_length=4)

    @model_validator(mode='after')
    def convex(self):
        points=self.corners_after_rotation
        if points:
            crosses=[]
            for i in range(4):
                a,b,c=points[i],points[(i+1)%4],points[(i+2)%4]
                crosses.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
            if min(crosses)<=.0001:raise ValueError('corners must be TL,TR,BR,BL and convex')
        return self


class BookContentReview(Model):
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1)
    source_refs: list[SourceRef]
    facts: list[Fact]
    entities: list[Entity]
    events: list[Event]
    reviewed_page_ids: list[str] = Field(default_factory=list)


class ObservationReview(Model):
    expected_page_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1)
    decision: Literal['corrected','rejected']
    corrected_observation: PageObservation | None = None
    notes: str = Field(min_length=1)

    @model_validator(mode='after')
    def corrected_required(self):
        if self.decision=='corrected' and self.corrected_observation is None:raise ValueError('correction required')
        return self


class CompilationItem(Model):
    clip_id: str
    asset_version: int = Field(ge=1)


class CompilationCreate(Model):
    items: list[CompilationItem] = Field(min_length=2,max_length=30)
    idempotency_key: str = Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def unique_clips(self):
        if len({i.clip_id for i in self.items})!=len(self.items):raise ValueError('duplicate clip')
        return self


class CompilationAsset(Model):
    compilation_id: str
    book_id: str
    asset_version: int = Field(ge=1)
    asset_id: str
    subtitle_asset_id: str
    duration_ms: int = Field(gt=0)
    chapters: list[dict]
    status: Literal['needs_review','approved','expired','failed']
    run_id: str
    is_mock: bool
    reviewed_by: str | None = None


class CompilationReview(Model):
    expected_asset_version: int = Field(ge=1)
    reviewer: str = Field(min_length=1)
    claims_by_frame: dict[str,list[VisualClaim]]


class PipelineCreate(Model):
    shot_id: str
    idempotency_key: str = Field(min_length=1,max_length=160)
    resume_run_ids: dict[str,str] = Field(default_factory=dict)
