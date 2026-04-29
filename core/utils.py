import copy
import datetime
from dataclasses import dataclass, field
from typing import Any


def parse_hhmm_to_minutes(time_str: str) -> int | None:
    try:
        parts = str(time_str or "").strip().split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return None


def deep_copy_schedule(data: dict) -> dict:
    return copy.deepcopy(data)


@dataclass
class GenerationContext:
    normalized_persona_name: str = ""
    outfit_style: str = ""
    schedule_main_type: str = ""
    core_event_driver: str = ""
    date_str: str = ""
    actual_provider_id: str | None = None
    configured_provider_id: str | None = None
    effective_session_id: str | None = None
    today_weather: str = ""
    configured_variation: str = ""
    effective_variation: str = ""
    style_reference: str = ""
    validate_persona: dict[str, Any] = field(default_factory=dict)
    racing_provider_ids: list[str] | None = None
    best_partial: dict | None = None
    max_repair_retries: int = 1
