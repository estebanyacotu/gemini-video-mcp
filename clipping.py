"""Validated data contracts for a complete, resumable live review."""
import json
import math
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Segment(StrictModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    quote: str

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('end must exceed start')
        return self

class Candidate(StrictModel):
    title: str
    segments: list[Segment] = Field(min_length=1)
    rationale: str
    visual_evidence: str
    audio_evidence: str
    hook_overlay: str
    caption: str
    cover: str
    edit_notes: str
    editorial_score: int = Field(ge=0, le=100)

class BlockAnalysis(StrictModel):
    audio_observed: bool
    video_observed: bool
    observation_limits: list[str]
    transcript: list[Segment]
    candidates: list[Candidate]

class Finding(StrictModel):
    second: float = Field(ge=0, allow_inf_nan=False)
    channel: str
    observation: str
    action: str
    priority: str

class ClipReview(StrictModel):
    audio_observed: bool
    video_observed: bool
    verdict: str
    editorial_score: int = Field(ge=0, le=100)
    observation_limits: list[str]
    hook: str
    comprehension: str
    pacing: str
    captions_and_crop: str
    audio: str
    ending: str
    findings: list[Finding]
    revised_hook: str
    revised_caption: str


def windows(duration: float, block_seconds: int = 480, overlap: int = 20):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Se necesita una duración verificada positiva.')
    result = []
    start = 0
    while start < duration:
        end = min(start + block_seconds, duration)
        result.append({'block': len(result) + 1, 'start': start, 'end': end})
        if end == duration:
            break
        start = end - overlap
    return result


def parse_srt(raw: str):
    def sec(value):
        h, m, s = value.replace(',', '.').split(':')
        return int(h)*3600 + int(m)*60 + float(s)
    result = []
    for match in re.finditer(r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)', raw, re.S):
        text = re.sub(r'<[^>]+>', '', match[3]).strip().replace('\n', ' ')
        if text and sec(match[2]) > sec(match[1]):
            result.append({'start': sec(match[1]), 'end': sec(match[2]), 'quote': text})
    return result


def parse_json(text, schema):
    value = text.strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:json)?\s*|\s*```$', '', value)
    return schema.model_validate(json.loads(value))


def validate_block(result: BlockAnalysis, start, end, subtitles):
    for segment in result.transcript + [s for c in result.candidates for s in c.segments]:
        if not start <= segment.start < segment.end <= end:
            raise ValueError('Timestamp fuera del bloque; no publicar un corte sin verificar.')
    verified = []
    for candidate in result.candidates:
        statuses = []
        for segment in candidate.segments:
            context = ' '.join(s['quote'] for s in subtitles if s['end'] > segment.start and s['start'] < segment.end)
            normalize = lambda x: ' '.join(re.findall(r'\w+', x.casefold()))
            statuses.append(bool(normalize(segment.quote)) and normalize(segment.quote) in normalize(context))
        item = candidate.model_dump()
        item['quote_evidence'] = 'subtitle_match' if subtitles and all(statuses) else 'model_audio_unverified'
        item['duration_seconds'] = round(sum(s.end-s.start for s in candidate.segments), 3)
        verified.append(item)
    return verified


def coverage(duration, receipts):
    intervals = sorted((r['start'], r['end']) for r in receipts if r.get('status') == 'reviewed')
    cursor = 0
    gaps = []
    for start, end in intervals:
        if not (0 <= start < end <= duration):
            raise ValueError('Invalid coverage receipt')
        if start > cursor:
            gaps.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < duration:
        gaps.append([cursor, duration])
    return {'complete': not gaps, 'gaps_seconds': gaps,
            'reviewed_seconds': duration-sum(b-a for a,b in gaps)}
