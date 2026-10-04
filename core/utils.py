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


def _norm_outfit_text(text) -> str:
    """穿搭文本归一化：去全部空白后比对，用于识别晨起复读。"""
    return "".join(str(text or "").split())


def get_first_outfit(data: dict) -> str:
    """取晨间第一套穿搭。兼容两种数据结构：

    - 旧结构：顶层 ``outfit`` 字段承载第一套（历史上晨起时段 outfit_change 还会复读一份）
    - 新结构：顶层字段已废弃，第一套写在晨起时段（timeline 中最早的换装时段）的 outfit_change

    判定规则：顶层 outfit 非空 → 旧结构直接返回；为空 → 返回最早换装时段的 outfit_change。
    """
    data = data or {}
    base = str(data.get("outfit") or "").strip()
    if base:
        return base
    best, best_key = "", None
    for item in data.get("timeline") or []:
        if not isinstance(item, dict):
            continue
        oc = str(item.get("outfit_change") or "").strip()
        if not oc:
            continue
        ts = parse_hhmm_to_minutes(str(item.get("time_start") or ""))
        key = (ts is None, ts if ts is not None else 0)
        if best_key is None or key < best_key:
            best, best_key = oc, key
    return best


def earliest_outfit_change_item(data: dict) -> dict | None:
    """返回 timeline 中最早的换装时段项（outfit_change 非空），无换装时返回 None。"""
    data = data or {}
    best, best_key = None, None
    for item in data.get("timeline") or []:
        if not isinstance(item, dict):
            continue
        oc = str(item.get("outfit_change") or "").strip()
        if not oc:
            continue
        ts = parse_hhmm_to_minutes(str(item.get("time_start") or ""))
        key = (ts is None, ts if ts is not None else 0)
        if best_key is None or key < best_key:
            best, best_key = item, key
    return best


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
    max_repair_retries: int = 2
