"""Bounded learning contracts. No client/model-provided owner or progress fields."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from .store import KnowledgeError, identifier

Text = Annotated[str, Field(strict=True, min_length=1, max_length=1500)]
Title = Annotated[str, Field(strict=True, min_length=1, max_length=200)]
IDs = Annotated[list[Title], Field(max_length=20)]
Points = Annotated[list[Title], Field(min_length=1, max_length=8)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearningInput(Contract):
    topic: Title
    goal: Text
    foundation: Text
    days: Annotated[StrictInt, Field(ge=1, le=365)] | None = None
    target_date: str | None = None
    daily_minutes: Annotated[StrictInt, Field(ge=5, le=480)]
    knowledge_base_ids: Annotated[list[Title], Field(min_length=1, max_length=10)]
    constraints: Annotated[list[Title], Field(max_length=10)] = []
    timezone: Title = "Asia/Shanghai"


class Chapter(Contract):
    title: Title
    objective: Text
    knowledge_points: Points
    minutes: Annotated[StrictInt, Field(ge=1, le=480)]
    citation_ids: IDs
    supplemental: StrictBool


class Proposal(Contract):
    chapters: Annotated[list[Chapter], Field(min_length=1, max_length=40)]
    gaps: Annotated[list[Title], Field(max_length=20)]


class EditedChapter(Chapter):
    chapter_id: Title | None = None


class Edit(Contract):
    chapters: Annotated[list[EditedChapter], Field(min_length=1, max_length=40)]


class TimeBudget(Contract):
    days: Annotated[StrictInt, Field(ge=1, le=365)]
    daily_minutes: Annotated[StrictInt, Field(ge=5, le=480)]


class Section(Contract):
    kind: Literal["goal", "concept", "example", "check"]
    text: Text
    citation_ids: Annotated[list[Title], Field(min_length=1, max_length=20)]


class Exercise(Contract):
    kind: Literal["objective", "short_answer"]
    question: Text
    knowledge_points: Points
    options: Annotated[list[Title], Field(max_length=6)]
    answer: Text
    rubric: Text
    citation_ids: Annotated[list[Title], Field(min_length=1, max_length=20)]


class Lesson(Contract):
    sections: Annotated[list[Section], Field(min_length=4, max_length=8)]
    exercises: Annotated[list[Exercise], Field(min_length=2, max_length=4)]


class FeedbackCheck(Contract):
    learner_quote: Annotated[str, Field(strict=True, min_length=1, max_length=400)]
    relation: Literal["supported", "contradicted", "missing", "uncertain"]
    explanation: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    citation_ids: Annotated[list[Title], Field(max_length=1)]
    evidence_quote: Annotated[str, Field(strict=True, max_length=600)]


class Feedback(Contract):
    checks: Annotated[list[FeedbackCheck], Field(min_length=1, max_length=6)]
    complete: StrictBool


def validate(contract, value):
    try:
        result = contract.model_validate(value).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise KnowledgeError("learning_invalid_structure") from None
    return result


def inline_schema(contract):
    """The public host intentionally disallows references, including local $defs."""
    schema = contract.model_json_schema()
    definitions = schema.get("$defs", {})

    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].removeprefix("#/$defs/")])
            return {k: expand(v) for k, v in value.items() if k != "$defs"}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    return expand(schema)


def evidence_schema(contract, citation_ids, knowledge_points=None):
    """Make exact current IDs/points visible in the generation contract too."""
    schema = inline_schema(contract)

    def bind(value):
        if isinstance(value, dict):
            for key, child in value.get("properties", {}).items():
                allowed = citation_ids if key == "citation_ids" else knowledge_points if key == "knowledge_points" else None
                if allowed is not None:
                    if allowed:
                        child["items"]["enum"] = list(dict.fromkeys(allowed))
                    else:
                        child["maxItems"] = 0
            for child in value.values():
                bind(child)
        elif isinstance(value, list):
            for child in value:
                bind(child)

    bind(schema)
    return schema


def learning_input(value, *, now=None):
    data = validate(LearningInput, value)
    if any(not data[k].strip() for k in ("topic", "goal", "foundation")):
        raise KnowledgeError("learning_required_input_missing")
    try:
        zone = ZoneInfo(data["timezone"])
        today = (now or datetime.now(zone)).astimezone(zone).date()
        if (data["days"] is None) == (data["target_date"] is None):
            raise ValueError
        if data["target_date"] is not None:
            end = date.fromisoformat(data["target_date"])
            data["days"] = (end - today).days + 1
        if not 1 <= data["days"] <= 365:
            raise ValueError
    except (ValueError, ZoneInfoNotFoundError):
        raise KnowledgeError("learning_invalid_deadline_or_timezone") from None
    bases = data["knowledge_base_ids"]
    if len(set(bases)) != len(bases):
        raise KnowledgeError("explicit_knowledge_scope_required")
    for base in bases:
        identifier(base)
    data["start_date"] = today.isoformat()
    return data


def schedule(chapters, inputs):
    day, used = 1, 0
    for chapter in chapters:
        if chapter["minutes"] > inputs["daily_minutes"]:
            raise KnowledgeError("chapter_exceeds_daily_budget_split_required")
        if used + chapter["minutes"] > inputs["daily_minutes"]:
            day, used = day + 1, 0
        chapter["day"] = day
        used += chapter["minutes"]
    total = sum(c["minutes"] for c in chapters)
    return {"estimated_minutes": total, "available_minutes": inputs["days"] * inputs["daily_minutes"], "scheduled_days": day, "over_budget": day > inputs["days"], "mastery_guaranteed": False}
