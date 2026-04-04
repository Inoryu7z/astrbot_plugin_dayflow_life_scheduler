import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

from astrbot.api import logger


class DayflowStore:
    def __init__(self, data_dir: str | Path | None = None, retention_days: int = 3):
        self.data_dir = Path(data_dir) if data_dir else Path(".")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "dayflow_state.json"

        self.memory_store: dict[str, dict] = {}
        self.history_store: dict[str, list[dict[str, Any]]] = {}
        self.auto_generation_state: dict[str, list[str]] = {}
        self._gen_lock = asyncio.Lock()
        self.generating_personas: set[str] = set()
        self.retention_days = self._normalize_retention_days(retention_days)
        self._initialized = False

    def _normalize_retention_days(self, value) -> int:
        try:
            parsed = int(value)
            if parsed == -1:
                return -1
            return max(parsed, 0)
        except Exception:
            return 3

    def _retention_cutoff_date(self) -> datetime.date:
        today = datetime.date.today()
        if self.retention_days == -1:
            return today
        if self.retention_days <= 0:
            return today
        return today - datetime.timedelta(days=self.retention_days - 1)

    def set_retention_days(self, days: int):
        self.retention_days = self._normalize_retention_days(days)
        self.prune_expired()
        if self._initialized:
            self._save_state()

    def initialize(self):
        self._load_state()
        self._initialized = True
        self.prune_expired()
        self._save_state()

    async def enter_generation(self, persona_name: str) -> bool:
        async with self._gen_lock:
            if persona_name in self.generating_personas:
                return False
            self.generating_personas.add(persona_name)
            return True

    async def exit_generation(self, persona_name: str):
        async with self._gen_lock:
            self.generating_personas.discard(persona_name)

    def get_memory(self, persona_name: str) -> dict | None:
        self.prune_expired()
        return self.memory_store.get(persona_name)

    def has_memory(self, persona_name: str) -> bool:
        return self.get_memory(persona_name) is not None

    def _history_item_to_schedule(self, persona_name: str, item: dict[str, Any]) -> dict:
        if not isinstance(item, dict):
            return None
        if item.get("meta") or item.get("timeline") or item.get("weather") or item.get("long_term_memory"):
            schedule = dict(item)
            meta = dict(schedule.get("meta") or {})
            meta["persona_name"] = persona_name
            meta["from_history"] = True
            meta["fallback"] = False if "fallback" not in meta else bool(meta.get("fallback"))
            schedule["meta"] = meta
            schedule.setdefault("timeline", [])
            schedule.setdefault("weather", "")
            schedule.setdefault("memo", "")
            schedule.setdefault("long_term_memory", [])
            return schedule

        date_str = str(item.get("date") or "")
        return {
            "outfit": item.get("outfit", ""),
            "schedule": item.get("schedule", ""),
            "meta": {
                "persona_name": persona_name,
                "date": date_str,
                "fallback": False,
                "from_history": True,
            },
            "timeline": [],
            "weather": "",
            "memo": "",
            "long_term_memory": [],
        }

    def get_schedule_for_date(self, persona_name: str, date_str: str) -> dict | None:
        current = self.get_memory(persona_name)
        current_date = str((current or {}).get("meta", {}).get("date") or "")
        if current and current_date == date_str:
            return current

        history = self.history_store.get(persona_name, [])
        for item in reversed(history):
            if str((item.get("meta") or {}).get("date") or item.get("date") or "") == date_str:
                return self._history_item_to_schedule(persona_name, item)
        return None

    def get_latest_schedule(self, persona_name: str) -> dict | None:
        current = self.get_memory(persona_name)
        if current:
            return current
        history = self.history_store.get(persona_name, [])
        if history:
            return self._history_item_to_schedule(persona_name, history[-1])
        return None

    def recent_history_text(self, persona_name: str, days: int) -> str:
        self.prune_expired()
        history = self.history_store.get(persona_name, [])
        if days == -1:
            selected = history
        else:
            selected = history[-max(0, days):]
        if not selected:
            return "（暂无近日日程）"
        parts = []
        for item in selected:
            converted = self._history_item_to_schedule(persona_name, item) or {}
            date_str = str((converted.get("meta") or {}).get("date") or item.get("date") or "")
            parts.append(
                f"[{date_str}]\n穿搭：{converted.get('outfit', '')}\n日程：{str(converted.get('schedule', ''))[:800]}"
            )
        return "\n\n".join(parts)

    def has_generated_for_date(self, persona_name: str, date_str: str) -> bool:
        return self.get_schedule_for_date(persona_name, date_str) is not None

    def has_consumed_auto_generation(self, persona_name: str, trigger_key: str) -> bool:
        items = self.auto_generation_state.get(persona_name, []) or []
        return trigger_key in items

    def mark_auto_generation_consumed(self, persona_name: str, trigger_key: str):
        items = self.auto_generation_state.setdefault(persona_name, [])
        if trigger_key not in items:
            items.append(trigger_key)
            self.prune_expired()
            self._save_state()

    def prune_expired(self):
        if self.retention_days == -1:
            return

        cutoff = self._retention_cutoff_date()

        new_history_store: dict[str, list[dict[str, Any]]] = {}
        for persona_name, items in self.history_store.items():
            kept = []
            for item in items:
                date_str = str((item.get("meta") or {}).get("date") or item.get("date") or "")
                if self._date_kept(date_str, cutoff):
                    kept.append(item)
            if kept:
                kept.sort(key=lambda x: str((x.get("meta") or {}).get("date") or x.get("date") or ""))
                new_history_store[persona_name] = kept
        self.history_store = new_history_store

        new_memory_store: dict[str, dict] = {}
        for persona_name, data in self.memory_store.items():
            date_str = str((data or {}).get("meta", {}).get("date") or "")
            if self._date_kept(date_str, cutoff):
                new_memory_store[persona_name] = data
        self.memory_store = new_memory_store

        new_auto_state: dict[str, list[str]] = {}
        for persona_name, keys in self.auto_generation_state.items():
            valid = []
            for key in keys or []:
                date_part = str(key).split("@", 1)[0]
                if self._date_kept(date_part, cutoff):
                    valid.append(str(key))
            if valid:
                new_auto_state[persona_name] = sorted(set(valid))
        self.auto_generation_state = new_auto_state

    def _date_kept(self, date_str: str | None, cutoff: datetime.date) -> bool:
        if not date_str:
            return False
        try:
            parsed = datetime.datetime.strptime(str(date_str), "%Y-%m-%d").date()
            return parsed >= cutoff
        except Exception:
            return False

    def _load_state(self):
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            memory_store = payload.get("memory_store", {}) or {}
            history_store = payload.get("history_store", {}) or {}
            auto_generation_state = payload.get("auto_generation_state", {}) or {}
            if isinstance(memory_store, dict):
                self.memory_store = memory_store
            if isinstance(history_store, dict):
                self.history_store = history_store
            if isinstance(auto_generation_state, dict):
                normalized_auto = {}
                for k, v in auto_generation_state.items():
                    if isinstance(v, list):
                        normalized_auto[str(k)] = [str(x) for x in v if str(x).strip()]
                self.auto_generation_state = normalized_auto
            saved_retention = payload.get("retention_days")
            if saved_retention is not None:
                self.retention_days = self._normalize_retention_days(saved_retention)
            logger.info(f"[dayflow] store loaded: personas={list(self.memory_store.keys())}")
        except Exception as e:
            logger.warning(f"[dayflow] load state failed: {e}")

    def _save_state(self):
        try:
            payload = {
                "saved_at": datetime.datetime.now().isoformat(),
                "retention_days": self.retention_days,
                "memory_store": self.memory_store,
                "history_store": self.history_store,
                "auto_generation_state": self.auto_generation_state,
            }
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            tmp_path = self.state_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self.state_file)
        except Exception as e:
            logger.warning(f"[dayflow] save state failed: {e}")
