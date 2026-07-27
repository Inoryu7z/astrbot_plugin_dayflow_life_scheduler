import asyncio
import datetime
import json
import threading
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
        self.auto_generation_failures: dict[str, dict[str, int]] = {}
        self.pending_custom_requests: dict[str, str] = {}
        self._gen_lock = asyncio.Lock()
        self.generating_personas: dict[str, float] = {}
        self._generation_stale_timeout: float = 600.0
        self.retention_days = self._normalize_retention_days(retention_days)
        self._initialized = False
        self._last_prune_time: float = 0.0
        self._prune_interval_seconds: float = 300.0
        self._save_lock = threading.Lock()

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
        self.prune_expired(force=True)
        if self._initialized:
            self._save_state()

    def initialize(self):
        self._load_state()
        self._initialized = True
        self.prune_expired(force=True)
        self._save_state()

    async def enter_generation(self, persona_name: str) -> bool:
        import time
        async with self._gen_lock:
            # 使用墙钟时间而非 monotonic：生成锁需跨进程重启持久化，
            # monotonic 在重启后归零，无法判断持久化锁的过期状态。
            now = time.time()
            stale = [
                k for k, ts in self.generating_personas.items()
                if now - ts > self._generation_stale_timeout
            ]
            for k in stale:
                logger.warning(f"[dayflow] 清除过期生成锁: persona={k}, held={now - self.generating_personas[k]:.0f}s")
                del self.generating_personas[k]
            if persona_name in self.generating_personas:
                return False
            self.generating_personas[persona_name] = now
            await self.async_save_state()
            return True

    async def exit_generation(self, persona_name: str):
        async with self._gen_lock:
            self.generating_personas.pop(persona_name, None)
            await self.async_save_state()

    async def save_schedule(self, store_key: str, data: dict):
        import copy
        data_copy = copy.deepcopy(data)
        self.memory_store[store_key] = data_copy
        date_str = str((data_copy.get("meta") or {}).get("date") or "")
        history = self.history_store.setdefault(store_key, [])
        before_count = len(history)
        history = [item for item in history if str((item.get("meta") or {}).get("date") or item.get("date") or "") != date_str]
        removed_count = before_count - len(history)
        history.append(copy.deepcopy(data_copy))
        self.history_store[store_key] = history
        if removed_count > 0:
            logger.debug(f"[dayflow] save_schedule 覆盖旧记录: persona={store_key}, date={date_str}, removed={removed_count}")
        self.prune_expired()
        await self.async_save_state()

    def get_memory(self, persona_name: str) -> dict | None:
        self.prune_expired()
        return self.memory_store.get(persona_name)

    def has_memory(self, persona_name: str) -> bool:
        return self.get_memory(persona_name) is not None

    def get_history_count(self, persona_name: str) -> int:
        return len(self.history_store.get(persona_name, []))

    def get_memory_date(self, persona_name: str) -> str:
        data = self.memory_store.get(persona_name)
        if not data:
            return ""
        return str((data.get("meta") or {}).get("date") or "")

    def _history_item_to_schedule(self, persona_name: str, item: dict[str, Any]) -> dict | None:
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

    def _normalize_style_key(self, style_name: str) -> str:
        return "".join(str(style_name or "").strip().lower().split())

    def collect_recent_style_outfits(self, persona_name: str, style_name: str, max_occurrences: int = 3) -> list[dict]:
        self.prune_expired()
        normalized_target = self._normalize_style_key(style_name)
        if not normalized_target:
            return []
        history = self.history_store.get(persona_name, [])
        results = []
        for item in reversed(history):
            converted = self._history_item_to_schedule(persona_name, item) or {}
            item_style = str(converted.get("outfit_style") or "").strip()
            if self._normalize_style_key(item_style) != normalized_target:
                continue
            date_str = str((converted.get("meta") or {}).get("date") or item.get("date") or "")
            morning_outfit = str(converted.get("outfit") or "").strip()
            afternoon_outfits = []
            timeline = converted.get("timeline")
            if isinstance(timeline, list):
                for seg in timeline:
                    if isinstance(seg, dict) and str(seg.get("outfit_change") or "").strip():
                        afternoon_outfits.append(str(seg["outfit_change"]).strip())
            results.append({
                "date": date_str,
                "outfit_style": item_style,
                "morning_outfit": morning_outfit,
                "afternoon_outfits": afternoon_outfits,
            })
            if len(results) >= max_occurrences:
                break
        return results

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

    def get_auto_generation_failure_count(self, persona_name: str, trigger_key: str) -> int:
        persona_state = self.auto_generation_failures.get(persona_name, {}) or {}
        try:
            return max(int(persona_state.get(trigger_key, 0) or 0), 0)
        except Exception:
            return 0

    def record_auto_generation_failure(self, persona_name: str, trigger_key: str) -> int:
        persona_state = self.auto_generation_failures.setdefault(persona_name, {})
        current = self.get_auto_generation_failure_count(persona_name, trigger_key)
        next_value = current + 1
        persona_state[trigger_key] = next_value
        self.prune_expired()
        self._save_state()
        return next_value

    def clear_auto_generation_failure(self, persona_name: str, trigger_key: str):
        persona_state = self.auto_generation_failures.get(persona_name)
        if not isinstance(persona_state, dict):
            return
        if trigger_key in persona_state:
            persona_state.pop(trigger_key, None)
            if not persona_state:
                self.auto_generation_failures.pop(persona_name, None)
            self.prune_expired()
            self._save_state()

    def set_pending_custom_request(self, target_date: str, persona_key: str, requirement: str):
        key = f"{target_date}|{persona_key}"
        self.pending_custom_requests[key] = requirement
        self._save_state()

    def get_pending_custom_request(self, target_date: str, persona_key: str) -> str | None:
        key = f"{target_date}|{persona_key}"
        return self.pending_custom_requests.get(key)

    def clear_pending_custom_request(self, target_date: str, persona_key: str) -> bool:
        key = f"{target_date}|{persona_key}"
        if key in self.pending_custom_requests:
            del self.pending_custom_requests[key]
            self._save_state()
            return True
        return False

    def consume_pending_custom_request(self, target_date: str, persona_key: str) -> str | None:
        requirement = self.get_pending_custom_request(target_date, persona_key)
        if requirement:
            self.clear_pending_custom_request(target_date, persona_key)
        return requirement

    def prune_expired(self, force: bool = False):
        if self.retention_days == -1:
            return

        import time
        now = time.monotonic()
        if not force and (now - self._last_prune_time) < self._prune_interval_seconds:
            return
        self._last_prune_time = now

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

        new_auto_failures: dict[str, dict[str, int]] = {}
        for persona_name, state in self.auto_generation_failures.items():
            if not isinstance(state, dict):
                continue
            valid: dict[str, int] = {}
            for key, count in state.items():
                date_part = str(key).split("@", 1)[0]
                if not self._date_kept(date_part, cutoff):
                    continue
                try:
                    normalized_count = max(int(count or 0), 0)
                except Exception:
                    normalized_count = 0
                if normalized_count > 0:
                    valid[str(key)] = normalized_count
            if valid:
                new_auto_failures[persona_name] = valid
        self.auto_generation_failures = new_auto_failures

        new_pending: dict[str, str] = {}
        for key, requirement in self.pending_custom_requests.items():
            if not requirement:
                continue
            parts = str(key).split("|", 1)
            if len(parts) != 2:
                continue
            date_part = parts[0]
            if self._date_kept(date_part, cutoff):
                new_pending[str(key)] = str(requirement)
        self.pending_custom_requests = new_pending

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
            auto_generation_failures = payload.get("auto_generation_failures", {}) or {}
            pending_custom_requests = payload.get("pending_custom_requests", {}) or {}
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
            if isinstance(auto_generation_failures, dict):
                normalized_failures: dict[str, dict[str, int]] = {}
                for persona_name, state in auto_generation_failures.items():
                    if not isinstance(state, dict):
                        continue
                    persona_map: dict[str, int] = {}
                    for key, count in state.items():
                        try:
                            normalized_count = max(int(count or 0), 0)
                        except Exception:
                            normalized_count = 0
                        if str(key).strip() and normalized_count > 0:
                            persona_map[str(key)] = normalized_count
                    if persona_map:
                        normalized_failures[str(persona_name)] = persona_map
                self.auto_generation_failures = normalized_failures
            if isinstance(pending_custom_requests, dict):
                normalized_pending: dict[str, str] = {}
                for k, v in pending_custom_requests.items():
                    if str(v).strip():
                        normalized_pending[str(k)] = str(v)
                self.pending_custom_requests = normalized_pending
            generating_personas = payload.get("generating_personas", {}) or {}
            if isinstance(generating_personas, dict):
                # 恢复持久化的生成锁；重启后若已超过 stale 阈值会在下次 enter_generation 时被清理
                restored_gen: dict[str, float] = {}
                for k, v in generating_personas.items():
                    try:
                        restored_gen[str(k)] = float(v)
                    except Exception:
                        continue
                self.generating_personas = restored_gen
            saved_retention = payload.get("retention_days")
            if saved_retention is not None:
                self.retention_days = self._normalize_retention_days(saved_retention)
            logger.info(f"[dayflow] 存储已加载: personas={list(self.memory_store.keys())}")
        except Exception as e:
            logger.warning(f"[dayflow] 加载存储状态失败: {e}")

    def _save_state(self):
        with self._save_lock:
            try:
                payload = {
                    "saved_at": datetime.datetime.now().isoformat(),
                    "retention_days": self.retention_days,
                    "memory_store": self.memory_store,
                    "history_store": self.history_store,
                    "auto_generation_state": self.auto_generation_state,
                    "auto_generation_failures": self.auto_generation_failures,
                    "pending_custom_requests": self.pending_custom_requests,
                    # 持久化生成锁：重载插件后仍能识别"正在生成"状态，避免调度器重复触发
                    "generating_personas": self.generating_personas,
                }
                self.state_file.parent.mkdir(parents=True, exist_ok=True)

                tmp_path = self.state_file.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp_path.replace(self.state_file)
            except Exception as e:
                logger.warning(f"[dayflow] 保存存储状态失败: {e}")

    async def async_save_state(self):
        await asyncio.to_thread(self._save_state)
