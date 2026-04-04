from typing import Any

from .constants import (
    DEFAULT_CORE_EVENT_DRIVERS,
    DEFAULT_OUTFIT_STYLES,
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_SCHEDULE_MAIN_TYPES,
    DEFAULT_VARIATION_LEVEL,
    DEFAULT_WEATHERS,
)


class DayflowConfig:
    def __init__(self, config: dict | None):
        self.config = config or {}

    def default_prompt_template(self) -> str:
        return str(self.config.get("default_prompt_template") or DEFAULT_PROMPT_TEMPLATE)

    def _normalize_persona_token(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return "".join(text.lower().split())

    def _normalize_variation_level(self, value: Any) -> str:
        text = str(value or DEFAULT_VARIATION_LEVEL).strip()
        if text in {"低", "中", "高", "随机"}:
            return text
        return DEFAULT_VARIATION_LEVEL

    def personas(self) -> list[dict[str, Any]]:
        personas = self.config.get("personas", []) or []
        default_prompt_template = self.default_prompt_template()
        normalized = []
        for item in personas:
            if not isinstance(item, dict):
                continue
            if not item.get("enabled", True):
                continue
            select_persona = str(item.get("select_persona") or item.get("name") or "").strip()
            if not select_persona:
                continue
            pool = item.get("pool", {}) or {}
            if not isinstance(pool, dict):
                pool = {}
            raw_prompt_template = item.get("prompt_template")
            prompt_template_override = str(raw_prompt_template).strip() if raw_prompt_template is not None else ""
            effective_prompt_template = prompt_template_override or default_prompt_template
            aliases = []
            for candidate in [
                item.get("alias"),
                item.get("aliases"),
                item.get("persona_id"),
                item.get("display_name"),
                item.get("name"),
                item.get("select_persona"),
            ]:
                if isinstance(candidate, list):
                    aliases.extend([str(x).strip() for x in candidate if str(x).strip()])
                elif candidate and str(candidate).strip():
                    aliases.append(str(candidate).strip())
            dedup_aliases = []
            seen = set()
            for alias in aliases:
                key = self._normalize_persona_token(alias)
                if key and key not in seen:
                    dedup_aliases.append(alias)
                    seen.add(key)
            raw_main_types = pool.get("schedule_main_types")
            if raw_main_types is None:
                raw_main_types = pool.get("schedule_types")
            normalized.append({
                "select_persona": select_persona,
                "name": select_persona,
                "enabled": True,
                "aliases": dedup_aliases,
                "reference_schedule_days": self._to_int(item.get("reference_schedule_days"), 0),
                "reference_diary_days": self._to_int(item.get("reference_diary_days"), 3),
                "reference_recent_count": self._to_int(item.get("reference_recent_count"), 10),
                "schedule_variation_level": self._normalize_variation_level(item.get("schedule_variation_level")),
                "pool": {
                    "today_weather": self._to_list(pool.get("today_weather"), DEFAULT_WEATHERS),
                    "outfit_styles": self._to_list(pool.get("outfit_styles"), DEFAULT_OUTFIT_STYLES),
                    "schedule_main_types": self._to_list(raw_main_types, DEFAULT_SCHEDULE_MAIN_TYPES),
                    "core_event_drivers": self._to_list(pool.get("core_event_drivers"), DEFAULT_CORE_EVENT_DRIVERS),
                },
                "select_provider": str(item.get("select_provider") or item.get("provider_id") or "").strip(),
                "provider_id": str(item.get("select_provider") or item.get("provider_id") or "").strip(),
                "generate_time": str(item.get("generate_time") or "07:00").strip() or "07:00",
                "retry_count": self._to_int(item.get("retry_count"), 2),
                "prompt_template_override": prompt_template_override,
                "prompt_template": effective_prompt_template,
            })
        return normalized

    def _to_int(self, value, default: int) -> int:
        try:
            return max(0, int(value if value is not None else default))
        except Exception:
            return default

    def _to_list(self, value, default: list[str]) -> list[str]:
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
            return items or list(default)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return list(default)

    def match_persona(self, persona_name: str | None = None) -> dict[str, Any] | None:
        target = self._normalize_persona_token(persona_name)
        if not target:
            return None
        for item in self.personas():
            if self._normalize_persona_token(item["name"]) == target:
                return item
            if self._normalize_persona_token(item.get("select_persona")) == target:
                return item
            for alias in item.get("aliases", []) or []:
                if self._normalize_persona_token(alias) == target:
                    return item
        return None

    def find_persona(self, persona_name: str | None = None) -> dict[str, Any] | None:
        return self.match_persona(persona_name)

    def resolve_store_key(self, *candidates: str | None) -> str:
        for candidate in candidates:
            matched = self.match_persona(candidate)
            if matched:
                return matched["name"]
        for candidate in candidates:
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return ""

    def default_persona_name(self) -> str:
        items = self.personas()
        return items[0]["name"] if items else ""
