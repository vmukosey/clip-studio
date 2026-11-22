from typing import Any

from pydantic import BaseModel, Field, field_validator


class SourceCreate(BaseModel):
    uri: str
    kind: str = 'local'

    @field_validator('kind')
    @classmethod
    def supported_kind(cls, value: str):
        if value not in {'local', 'youtube'}:
            raise ValueError('kind must be local or youtube')
        return value

class Interval(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @field_validator('end')
    @classmethod
    def end_after_start(cls, value: float, info):
        start = info.data.get('start')
        if start is not None and value <= start:
            raise ValueError('end must be greater than start')
        return value

class JobCreate(BaseModel):
    source_id: str
    count: int | None = Field(default=None, ge=1, le=100)
    language: str | None = None
    template: str = 'default'
    intervals: list[Interval] | None = None
    campaign_rules: dict[str, Any] = Field(default_factory=dict)

    @field_validator('template')
    @classmethod
    def supported_template(cls, value: str):
        if value not in {'default', 'interview', 'gaming'}:
            raise ValueError('template must be default, interview, or gaming')
        return value

class CampaignCreate(BaseModel):
    name: str
    rules: dict[str, Any] = Field(default_factory=dict)

class ReviewAction(BaseModel):
    action: str
    reason: str = ''
    start: float | None = None
    end: float | None = None
    camera_side: str | None = None
    crop_points: list[dict[str, float]] | None = None
    text_method: str | None = None


class SelectionUpdate(BaseModel):
    selection: str

    @field_validator('selection')
    @classmethod
    def valid_selection(cls, value: str):
        if value not in {'unselected', 'keep', 'skip'}:
            raise ValueError('selection must be unselected, keep, or skip')
        return value

class ManualInterval(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

class FinalizeRequest(BaseModel):
    path: str


class TitleUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)

class VoiceoverCreate(BaseModel):
    clip_id: str | None = None
    path: str | None = None
    render_id: str | None = None
    mode: str = 'description'
    prompt: str = Field(default='', max_length=2000)
    regions: list[Interval] = Field(default_factory=list, max_length=30)
    manual: bool = False

    @field_validator('mode')
    @classmethod
    def supported_mode(cls, value: str):
        if value not in {'description', 'commentary', 'intro'}:
            raise ValueError('mode must be description, commentary, or intro')
        return value

class VoiceoverLine(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=500)

class VoiceoverEdit(BaseModel):
    lines: list[VoiceoverLine] = Field(max_length=30)
