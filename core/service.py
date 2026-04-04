import asyncio
import datetime
import random
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .config import DayflowConfig
from .constants import DEFAULT_VARIATION_LEVEL, VARIATION_LEVEL_DEFINITIONS
from .generator import (
    build_format_priority_append_prompt,
    build_generation_error_data,
    build_repair_prompt,
    extract_timeline,
    normalize_payload,
    render_prompt,
    safe_json_loads,
    validate_payload,
)
from .store import DayflowStore


class DayflowService:
    PLUGIN_NAME = "astrbot_plugin_dayflow_life_scheduler"
    LLM_RETRY_DELAY_SECONDS = 2.0

    def __init__(self, context, config=None):
        self.context = context
        self.cfg = DayflowConfig(config or {})
        self.config = config or {}
        base_data_dir = Path(get_astrbot_data_path())
        self.data_dir = base_data_dir / "plugin_data" / self.PLUGIN_NAME
        retention_days = self._schedule_retention_days()
        self.store = DayflowStore(data_dir=self.data_dir, retention_days=retention_days)
        self.scheduler_task: asyncio.Task | None = None
        self._scheduler_running = False
        self._last_debug_payload: dict[str, Any] = {}

    def _schedule_retention_days(self) -> int:
        try:
            value = int(self.config.get("schedule_retention_days", 3))
            if value == -1:
                return -1
            return max(0, value)
        except Exception:
            return 3

    def normalize_persona_key(self, persona_name: str | None = None, persona_id: str | None = None) -> str:
        return self.cfg.resolve_store_key(persona_name, persona_id)

    def get_persona_config(self, persona_name: str | None = None, persona_id: str | None = None) -> dict[str, Any] | None:
        for candidate in (persona_name, persona_id):
            matched = self.cfg.match_persona(candidate)
            if matched:
                return matched
        return None

    def is_persona_configured(self, persona_name: str | None = None, persona_id: str | None = None) -> bool:
        return self.get_persona_config(persona_name, persona_id) is not None

    def _build_persona_not_enabled_data(self, persona_name: str | None) -> dict:
        target = str(persona_name or "").strip() or "（未识别人格）"
        reason = f"人格未在 Dayflow 中启用：{target}"
        return {
            "outfit": "尚未生成",
            "schedule": reason,
            "meta": {
                "persona_name": target,
                "fallback": True,
                "fallback_reason": reason,
                "persona_not_enabled": True,
                "error": True,
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            },
            "timeline": [],
            "weather": "",
            "memo": reason,
            "long_term_memory": [],
        }

    def _effective_variation_level(self, configured_level: str | None) -> str:
        level = str(configured_level or DEFAULT_VARIATION_LEVEL).strip()
        if level == "随机":
            roll = random.random()
            if roll < 0.3:
                return "低"
            if roll < 0.9:
                return "中"
            return "高"
        return level if level in VARIATION_LEVEL_DEFINITIONS else "中"

    def _variation_definition(self, level: str) -> str:
        return VARIATION_LEVEL_DEFINITIONS.get(level, VARIATION_LEVEL_DEFINITIONS["中"])

    def _build_history_structure_summary(self, persona_name: str, days: int = 3) -> str:
        history = self.store.history_store.get(persona_name, []) or []
        if not history:
            return "（暂无近三日结构摘要）"
        selected = history[-max(0, days):]
        if not selected:
            return "（暂无近三日结构摘要）"
        lines = []
        for item in selected:
            converted = self.store._history_item_to_schedule(persona_name, item) or {}
            date_str = str((converted.get("meta") or {}).get("date") or "")
            schedule = str(converted.get("schedule") or "")
            first_lines = []
            for line in schedule.splitlines():
                text = line.strip()
                if not text:
                    continue
                first_lines.append(text[:42])
                if len(first_lines) >= 4:
                    break
            summary = "；".join(first_lines) if first_lines else "（无可提取结构）"
            lines.append(f"- {date_str}: {summary}")
        return "\n".join(lines)

    async def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store.initialize()
        self.store.set_retention_days(self._schedule_retention_days())
        logger.info("[dayflow] plugin initialized")
        logger.info(f"[dayflow] loaded personas: {[p.get('name') for p in self.cfg.personas()]}")
        await self.start_scheduler()

    async def terminate(self):
        await self.stop_scheduler()
        self.store.prune_expired()
        self.store._save_state()
        logger.info("[dayflow] terminated")

    async def start_scheduler(self):
        if self._scheduler_running:
            return
        self._scheduler_running = True
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("[dayflow] auto scheduler started")

    async def stop_scheduler(self):
        self._scheduler_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
            self.scheduler_task = None
        logger.info("[dayflow] auto scheduler stopped")

    async def _scheduler_loop(self):
        while self._scheduler_running:
            try:
                await self.run_due_generations()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[dayflow] scheduler loop error: {e}")
                await asyncio.sleep(60)

    def _parse_hhmm(self, value: str) -> tuple[int, int]:
        try:
            hour, minute = map(int, str(value or "07:00").split(":"))
            hour = min(max(hour, 0), 23)
            minute = min(max(minute, 0), 59)
            return hour, minute
        except Exception:
            return 7, 0

    async def run_due_generations(self):
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        now_minutes = now.hour * 60 + now.minute
        for persona in self.cfg.personas():
            configured_persona_name = persona["name"]
            store_key = self.normalize_persona_key(configured_persona_name)
            hour, minute = self._parse_hhmm(persona.get("generate_time", "07:00"))
            trigger_minutes = hour * 60 + minute
            trigger_key = f"{today}@{hour:02d}:{minute:02d}"
            if now_minutes < trigger_minutes:
                continue
            if self.store.has_consumed_auto_generation(store_key, trigger_key):
                continue
            if self.store.has_generated_for_date(store_key, today):
                self.store.mark_auto_generation_consumed(store_key, trigger_key)
                continue
            ok = await self.enter_generation(store_key)
            if not ok:
                continue
            try:
                persona_ctx = await self._resolve_persona_context_internal(persona_name=configured_persona_name)
                auto_session_id = await self._get_recent_session_id_for_persona(configured_persona_name, persona_ctx.get("persona_id"))
                data = await self.generate_schedule(
                    event=None,
                    persona_name=configured_persona_name,
                    persona_desc=persona_ctx["persona_desc"],
                    session_id=auto_session_id,
                )
                if not data.get("meta", {}).get("error"):
                    self.save_generated(store_key, data)
                    self.store.mark_auto_generation_consumed(store_key, trigger_key)
                    logger.info(
                        f"[dayflow] auto generated schedule for persona={configured_persona_name}, "
                        f"store_key={store_key}, trigger={trigger_key}, session={auto_session_id or 'none'}"
                    )
                else:
                    logger.warning(f"[dayflow] auto generation failed but will retry later for persona={configured_persona_name}, trigger={trigger_key}, reason={data.get('memo', '')}")
            except Exception as e:
                logger.warning(f"[dayflow] auto generation failed for persona={configured_persona_name}: {e}")
            finally:
                await self.exit_generation(store_key)

    def _extract_persona_fields(self, persona_obj) -> dict[str, str]:
        result = {"persona_name": "", "persona_desc": "", "persona_id": ""}
        if not persona_obj:
            return result
        if isinstance(persona_obj, dict):
            result["persona_name"] = str(persona_obj.get("name") or persona_obj.get("persona_id") or persona_obj.get("id") or "").strip()
            result["persona_id"] = str(persona_obj.get("persona_id") or persona_obj.get("id") or result["persona_name"] or "").strip()
            result["persona_desc"] = str(
                persona_obj.get("system_prompt")
                or persona_obj.get("prompt")
                or persona_obj.get("description")
                or persona_obj.get("persona_desc")
                or persona_obj.get("content")
                or ""
            ).strip()
            return result
        for attr in ("name", "persona_id", "id"):
            value = getattr(persona_obj, attr, None)
            if value and not result["persona_name"]:
                result["persona_name"] = str(value).strip()
            if value and not result["persona_id"]:
                result["persona_id"] = str(value).strip()
        for attr in ("system_prompt", "prompt", "description", "persona_desc", "content"):
            value = getattr(persona_obj, attr, None)
            if isinstance(value, str) and value.strip() and not result["persona_desc"]:
                result["persona_desc"] = value.strip()
        return result

    async def _resolve_persona_context_internal(self, event=None, persona_name: str | None = None, session_id: str | None = None) -> dict[str, str]:
        result = {"persona_name": "", "persona_desc": "", "persona_id": "", "source": ""}
        target_persona_name = (persona_name or "").strip()
        effective_session_id = session_id or (getattr(event, "unified_msg_origin", None) if event else None)

        conv_mgr = getattr(self.context, "conversation_manager", None)
        persona_mgr = getattr(self.context, "persona_manager", None)
        bound_persona_id = None

        if effective_session_id and conv_mgr:
            try:
                curr_cid = await conv_mgr.get_curr_conversation_id(effective_session_id)
                if curr_cid:
                    conversation = await conv_mgr.get_conversation(effective_session_id, curr_cid)
                    bound_persona_id = getattr(conversation, "persona_id", None) if conversation else None
            except Exception as e:
                logger.warning(f"[dayflow] read conversation persona failed: {e}")

        if persona_mgr and bound_persona_id:
            try:
                persona_obj = persona_mgr.get_persona(bound_persona_id)
                if hasattr(persona_obj, "__await__"):
                    persona_obj = await persona_obj
                extracted = self._extract_persona_fields(persona_obj)
                if extracted["persona_desc"]:
                    result.update(extracted)
                    result["source"] = "conversation_persona"
            except Exception as e:
                logger.warning(f"[dayflow] get_persona by bound persona_id failed: {e}")

        if persona_mgr and target_persona_name and not result["persona_desc"]:
            try:
                persona_obj = persona_mgr.get_persona(target_persona_name)
                if hasattr(persona_obj, "__await__"):
                    persona_obj = await persona_obj
                extracted = self._extract_persona_fields(persona_obj)
                if extracted["persona_desc"]:
                    result.update(extracted)
                    result["source"] = "target_persona"
            except Exception:
                pass

        if persona_mgr and effective_session_id and not result["persona_desc"]:
            try:
                persona_obj = await persona_mgr.get_default_persona_v3(effective_session_id)
                extracted = self._extract_persona_fields(persona_obj)
                if extracted["persona_desc"]:
                    result.update(extracted)
                    result["source"] = "default_persona_v3"
            except Exception as e:
                logger.warning(f"[dayflow] get_default_persona_v3 failed: {e}")

        if not result["persona_name"] and target_persona_name:
            result["persona_name"] = target_persona_name
        if not result["persona_name"]:
            event_persona = getattr(event, "persona_name", None) if event else None
            if event_persona and str(event_persona).strip():
                result["persona_name"] = str(event_persona).strip()
        if not result["persona_name"] and bound_persona_id:
            result["persona_name"] = str(bound_persona_id).strip()
        if not result["persona_name"]:
            result["persona_name"] = self.cfg.default_persona_name()

        if not result["persona_desc"]:
            result["persona_desc"] = f"人格：{result['persona_name']}。"
            result["source"] = result["source"] or "fallback_name_only"
            logger.warning(
                f"[dayflow] persona context fallback to name only: persona={result['persona_name']}, "
                f"session={effective_session_id or ''}, target={target_persona_name or ''}, bound_persona_id={bound_persona_id or ''}"
            )
        return result

    async def resolve_persona_context(self, event=None, persona_name: str | None = None) -> dict[str, str]:
        return await self._resolve_persona_context_internal(event=event, persona_name=persona_name)

    def get_cached_or_fallback(self, persona_name: str) -> dict:
        store_key = self.normalize_persona_key(persona_name)
        latest = self.store.get_latest_schedule(store_key)
        if latest:
            return latest
        return {
            "outfit": "尚未生成",
            "schedule": "暂无日程记录，请使用 /生成日程 命令。",
            "meta": {"persona_name": store_key, "fallback": True, "fallback_reason": "无历史日程"},
            "timeline": [],
            "weather": "",
            "memo": "",
            "long_term_memory": [],
        }

    def _candidate_store_keys(self, resolved_ctx: dict[str, str] | None = None, persona_name: str | None = None) -> list[str]:
        candidates: list[str] = []
        for candidate in [
            (resolved_ctx or {}).get("persona_name"),
            (resolved_ctx or {}).get("persona_id"),
            persona_name,
        ]:
            store_key = self.normalize_persona_key(candidate)
            if store_key and store_key not in candidates:
                candidates.append(store_key)
        return candidates

    def _build_missing_today_context(self, store_key: str, today: str, latest: dict | None = None, fallback_reason: str = "今日尚无有效日程") -> dict:
        latest_date = str((latest or {}).get("meta", {}).get("date") or "")
        return {
            "outfit": "尚未生成",
            "schedule": "今日日程尚未生成成功，请先重新生成今日日程。",
            "meta": {
                "persona_name": store_key,
                "date": today,
                "fallback": True,
                "fallback_reason": fallback_reason,
                "latest_available_date": latest_date,
            },
            "timeline": [],
            "weather": "",
            "memo": fallback_reason,
            "long_term_memory": [],
        }

    async def get_life_context(self, session_id: str | None = None, persona_name: str | None = None) -> dict:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        resolved_ctx = await self._resolve_persona_context_internal(session_id=session_id, persona_name=persona_name)
        if not self.is_persona_configured(resolved_ctx.get("persona_name"), resolved_ctx.get("persona_id")):
            reason = f"人格未在 Dayflow 中启用：{resolved_ctx.get('persona_name') or persona_name or '（未识别人格）'}"
            logger.warning(
                f"[dayflow] get_life_context rejected unconfigured persona: session={session_id or ''}, "
                f"requested_persona={persona_name or ''}, resolved_persona={resolved_ctx.get('persona_name', '')}, "
                f"resolved_persona_id={resolved_ctx.get('persona_id', '')}"
            )
            return {
                "outfit": "尚未生成",
                "schedule": reason,
                "meta": {
                    "persona_name": str(resolved_ctx.get('persona_name') or persona_name or '').strip(),
                    "date": today,
                    "fallback": True,
                    "fallback_reason": reason,
                    "persona_not_enabled": True,
                },
                "timeline": [],
                "weather": "",
                "memo": reason,
                "long_term_memory": [],
            }

        candidate_keys = self._candidate_store_keys(resolved_ctx=resolved_ctx, persona_name=persona_name)

        for store_key in candidate_keys:
            today_schedule = self.store.get_schedule_for_date(store_key, today)
            if today_schedule and not today_schedule.get("meta", {}).get("error"):
                logger.info(
                    f"[dayflow] get_life_context hit today schedule: session={session_id or ''}, "
                    f"requested_persona={persona_name or ''}, resolved_persona={resolved_ctx.get('persona_name', '')}, "
                    f"resolved_persona_id={resolved_ctx.get('persona_id', '')}, store_key={store_key}, date={today}"
                )
                return today_schedule

        latest = None
        latest_store_key = candidate_keys[0] if candidate_keys else self.cfg.default_persona_name()
        for store_key in candidate_keys:
            current_latest = self.store.get_latest_schedule(store_key)
            if current_latest:
                latest = current_latest
                latest_store_key = store_key
                break

        latest_date = str((latest or {}).get("meta", {}).get("date") or "")
        logger.warning(
            f"[dayflow] get_life_context missing today schedule: session={session_id or ''}, "
            f"requested_persona={persona_name or ''}, resolved_persona={resolved_ctx.get('persona_name', '')}, "
            f"resolved_persona_id={resolved_ctx.get('persona_id', '')}, candidate_keys={candidate_keys}, "
            f"today={today}, latest_date={latest_date or 'none'}, returning_fallback=yes"
        )
        return self._build_missing_today_context(
            store_key=latest_store_key,
            today=today,
            latest=latest,
            fallback_reason="今日尚无有效日程，已拒绝回退到旧日程",
        )

    async def enter_generation(self, persona_name: str) -> bool:
        return await self.store.enter_generation(self.normalize_persona_key(persona_name))

    async def exit_generation(self, persona_name: str):
        await self.store.exit_generation(self.normalize_persona_key(persona_name))

    def save_generated(self, persona_name: str, data: dict):
        requested_store_key = self.normalize_persona_key(persona_name)
        if data.get("meta", {}).get("error"):
            logger.warning(f"[dayflow] skip saving error result for persona={requested_store_key}")
            return

        data = dict(data)
        meta = dict(data.get("meta") or {})
        meta_persona_name = str(meta.get("persona_name") or "").strip()
        if meta_persona_name:
            meta_store_key = self.normalize_persona_key(meta_persona_name)
            if meta_store_key != requested_store_key:
                logger.warning(
                    f"[dayflow] generated persona key mismatch, use caller key: "
                    f"requested={requested_store_key}, meta={meta_persona_name}, normalized_meta={meta_store_key}"
                )
        store_key = requested_store_key
        meta["persona_name"] = store_key
        data["meta"] = meta
        self.store.memory_store[store_key] = data
        history = self.store.history_store.setdefault(store_key, [])
        today = str(data.get("meta", {}).get("date") or datetime.datetime.now().strftime("%Y-%m-%d"))
        history = [item for item in history if str((item.get('meta') or {}).get('date') or item.get('date') or '') != today]
        history.append(dict(data))
        self.store.history_store[store_key] = history
        self.store.prune_expired()
        self.store._save_state()
        logger.info(
            f"[dayflow] schedule saved: store_key={store_key}, date={today}, "
            f"history_count={len(self.store.history_store.get(store_key, []))}, "
            f"memory_current_date={str((self.store.memory_store.get(store_key) or {}).get('meta', {}).get('date') or '')}"
        )

    def describe_personas(self) -> list[str]:
        retention = self._schedule_retention_days()
        retention_text = "无限制" if retention == -1 else f"{retention}天"
        return [
            f"- {item['name']} @ {item.get('generate_time', '07:00')} ({item.get('provider_id') or 'current_provider'} -> session_fallback) | 重试:{item.get('retry_count', 2)} | 变化:{item.get('schedule_variation_level', DEFAULT_VARIATION_LEVEL)} | 持久化:{retention_text}"
            for item in self.cfg.personas()
        ]

    def _iter_loaded_stars(self):
        try:
            stars = self.context.get_all_stars()
        except Exception as e:
            logger.debug(f"[dayflow] get_all_stars failed: {e}")
            return []
        result = []
        for item in stars or []:
            star_obj = getattr(item, "star", None)
            if star_obj is None and hasattr(item, "instance"):
                star_obj = getattr(item, "instance", None)
            if star_obj is None and hasattr(item, "plugin"):
                star_obj = getattr(item, "plugin", None)
            if star_obj is None and hasattr(item, "obj"):
                star_obj = getattr(item, "obj", None)
            if star_obj is None and not isinstance(item, (str, int, float, bool, list, tuple, set, dict)):
                star_obj = item
            if star_obj is not None:
                result.append(star_obj)
        return result

    def _is_valid_daymind_instance(self, obj) -> bool:
        if obj is None:
            return False
        has_cache = hasattr(obj, "message_cache")
        scheduler = getattr(obj, "scheduler", None)
        has_scheduler_api = scheduler is not None and hasattr(scheduler, "list_diaries")
        return has_cache or has_scheduler_api

    def _find_daymind_plugin(self):
        try:
            stars = self.context.get_all_stars()
        except Exception as e:
            logger.debug(f"[dayflow] get_all_stars failed when finding daymind: {e}")
            stars = []
        for meta in stars or []:
            plugin_name = str(getattr(meta, "name", "") or "").strip()
            root_dir_name = str(getattr(meta, "root_dir_name", "") or "").strip()
            module_path = str(getattr(meta, "module_path", "") or "").strip()
            for attr in ("star", "instance", "plugin", "obj", "star_cls"):
                candidate = getattr(meta, attr, None)
                if plugin_name == "astrbot_plugin_daymind" or root_dir_name == "astrbot_plugin_daymind" or "astrbot_plugin_daymind" in module_path:
                    if self._is_valid_daymind_instance(candidate):
                        return candidate
            star_obj = getattr(meta, "star_cls", None)
            if star_obj is not None:
                cls_name = star_obj.__class__.__name__
                module_name = getattr(star_obj.__class__, "__module__", "")
                if (cls_name == "DayMindPlugin" or "astrbot_plugin_daymind" in module_name) and self._is_valid_daymind_instance(star_obj):
                    return star_obj
        for star in self._iter_loaded_stars():
            cls_name = star.__class__.__name__
            module_name = getattr(star.__class__, "__module__", "")
            if (cls_name == "DayMindPlugin" or "astrbot_plugin_daymind" in module_name) and self._is_valid_daymind_instance(star):
                return star
        return None

    async def _get_recent_session_id_for_persona(self, persona_name: str | None = None, persona_id: str | None = None) -> str | None:
        daymind = self._find_daymind_plugin()
        if daymind is None:
            return None
        message_cache = getattr(daymind, "message_cache", None)
        session_persona_map = getattr(daymind, "session_persona_map", {}) or {}
        if message_cache is None or not isinstance(session_persona_map, dict):
            return None
        target_key = self.normalize_persona_key(persona_name, persona_id)
        if not target_key:
            return None
        try:
            recent_session_ids = await message_cache.get_recent_session_ids()
            for session_id in recent_session_ids:
                mapped_key = self.normalize_persona_key(session_persona_map.get(session_id))
                if mapped_key == target_key:
                    return session_id
            all_session_ids = await message_cache.get_all_session_ids()
            for session_id in all_session_ids:
                mapped_key = self.normalize_persona_key(session_persona_map.get(session_id))
                if mapped_key == target_key:
                    return session_id
        except Exception as e:
            logger.warning(f"[dayflow] resolve recent session for persona failed: {e}")
        return None

    async def collect_recent_chat_text(self, event=None, persona: dict | None = None, session_id: str | None = None) -> str:
        persona = persona or {}
        limit = int(persona.get("reference_recent_count", 10) or 10)
        if limit <= 0:
            return "（未启用近期对话参考）"
        effective_session_id = session_id or (getattr(event, "unified_msg_origin", None) if event else None)
        daymind = self._find_daymind_plugin()
        if daymind is not None:
            message_cache = getattr(daymind, "message_cache", None)
            if message_cache is not None and effective_session_id:
                try:
                    rounds = max(1, (min(limit, 30) + 1) // 2) if limit > 1 else 1
                    recent_messages = await message_cache.get_recent_messages(effective_session_id, rounds=rounds)
                    if recent_messages:
                        clipped = recent_messages[-limit:]
                        return "\n".join(f"- {line}" for line in clipped)
                except Exception as e:
                    logger.warning(f"[dayflow] read daymind recent messages failed: {e}")
        msg = (getattr(event, "message_str", None) or "").strip() if event else ""
        if not msg:
            return "（暂无近期对话参考）"
        return f"- 当前触发消息：{msg}\n- 近期对话摘要：未读取到 DayMind 消息缓存，已回退到当前触发消息。"

    async def collect_recent_diaries_text(self, persona_name: str, persona: dict, session_id: str | None = None) -> str:
        days = int(persona.get("reference_diary_days", 3) or 3)
        if days <= 0:
            return "（未启用近日日记参考）"
        daymind = self._find_daymind_plugin()
        if daymind is None:
            return "（未检测到 DayMind，无法读取近日日记）"
        scheduler = getattr(daymind, "scheduler", None)
        if scheduler is None or not hasattr(scheduler, "list_diaries"):
            return "（DayMind 已加载，但未找到可用的日记读取接口）"
        try:
            items = scheduler.list_diaries(days=days, starred_only=False) or []
            persona_items = [x for x in items if self.normalize_persona_key(x.get("persona_name")) == self.normalize_persona_key(persona_name)]
            persona_items.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
            selected = list(reversed(persona_items[:days]))
            if not selected:
                return "（DayMind 中暂无该人格近日日记）"
            lines = []
            for item in selected:
                date_str = str(item.get("date") or "")
                title = str(item.get("title") or date_str or "未命名")
                preview = str(item.get("preview") or "").strip()
                body = f"{date_str}｜{title}"
                if preview:
                    body += f"\n{preview}"
                lines.append(body)
            return "\n\n".join(lines)
        except Exception as e:
            logger.warning(f"[dayflow] read daymind diaries failed: {e}")
            return f"（读取 DayMind 近日日记失败：{e}）"

    async def call_llm_once(self, prompt: str, provider_id: str | None) -> str:
        if provider_id:
            llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
        else:
            llm_resp = await self.context.llm_generate(prompt=prompt)
        return (getattr(llm_resp, "completion_text", "") or "").strip()

    async def call_llm_with_retries(self, prompt: str, provider_id: str | None, retry_count: int = 0) -> str:
        attempts = max(int(retry_count), 0) + 1
        last_text = ""
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                text = await self.call_llm_once(prompt, provider_id)
                last_text = text
                if text:
                    if attempt > 1:
                        logger.info(f"[dayflow] llm call retry success: provider={provider_id or 'session_default'}, attempt={attempt}/{attempts}")
                    return text
                logger.warning(f"[dayflow] llm completion empty: provider={provider_id or 'session_default'}, attempt={attempt}/{attempts}")
            except Exception as e:
                last_error = e
                logger.warning(f"[dayflow] llm call failed: provider={provider_id or 'session_default'}, attempt={attempt}/{attempts}, error={e}")
            if attempt < attempts and self.LLM_RETRY_DELAY_SECONDS > 0:
                await asyncio.sleep(self.LLM_RETRY_DELAY_SECONDS)
        if last_error is not None:
            raise last_error
        return last_text

    async def _resolve_session_provider_id(self, event=None) -> str | None:
        if event is None or not hasattr(self.context, "get_current_chat_provider_id"):
            return None
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
            return provider_id or None
        except Exception as e:
            logger.warning(f"[dayflow] get_current_chat_provider_id failed: {e}")
            return None

    async def call_llm_with_provider_fallback(
        self,
        prompt: str,
        primary_provider_id: str | None,
        fallback_provider_id: str | None,
        retry_count: int = 0,
    ) -> tuple[str, str | None]:
        primary_provider_id = primary_provider_id or None
        fallback_provider_id = fallback_provider_id or None
        if primary_provider_id and fallback_provider_id and primary_provider_id == fallback_provider_id:
            fallback_provider_id = None

        logger.info(
            f"[dayflow] llm provider strategy: primary={primary_provider_id or 'session_default'}, "
            f"fallback={fallback_provider_id or 'none'}, retry_count={max(int(retry_count), 0)}"
        )

        last_error: Exception | None = None
        last_text = ""

        if primary_provider_id is not None or fallback_provider_id is None:
            try:
                text = await self.call_llm_with_retries(prompt, primary_provider_id, retry_count=retry_count)
                if text:
                    return text, primary_provider_id
                last_text = text
                logger.warning(
                    f"[dayflow] primary provider exhausted with empty result: provider={primary_provider_id or 'session_default'}"
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[dayflow] primary provider exhausted retries, switching to fallback: "
                    f"provider={primary_provider_id or 'session_default'}, error={e}"
                )

        if fallback_provider_id is not None:
            try:
                text = await self.call_llm_with_retries(prompt, fallback_provider_id, retry_count=retry_count)
                if text:
                    logger.info(f"[dayflow] fallback provider succeeded: provider={fallback_provider_id}")
                    return text, fallback_provider_id
                last_text = text
                logger.warning(f"[dayflow] fallback provider exhausted with empty result: provider={fallback_provider_id}")
            except Exception as e:
                last_error = e
                logger.warning(f"[dayflow] fallback provider failed: provider={fallback_provider_id}, error={e}")

        if last_error is not None:
            raise last_error
        return last_text, (fallback_provider_id if last_text and fallback_provider_id is not None else primary_provider_id)

    def get_last_debug_payload(self) -> dict[str, Any]:
        return self._last_debug_payload or {}

    async def build_debug_snapshot(self, event=None, persona_name: str | None = None) -> dict[str, Any]:
        persona_ctx = await self.resolve_persona_context(event=event, persona_name=persona_name)
        if not self.is_persona_configured(persona_ctx.get("persona_name"), persona_ctx.get("persona_id")):
            return {
                "persona_name": persona_ctx.get("persona_name", ""),
                "persona_source": persona_ctx.get("source", ""),
                "persona_desc_preview": (persona_ctx.get("persona_desc") or "")[:800],
                "outfit_styles_pool": [],
                "schedule_main_types_pool": [],
                "core_event_drivers_pool": [],
                "today_weather_pool": [],
                "variation_configured": "",
                "variation_effective": "",
                "variation_definition": "",
                "history_structure_summary": "",
                "rendered_prompt_preview": f"人格未在 Dayflow 中启用：{persona_ctx.get('persona_name', '')}",
                "recent_chats_preview": "",
                "recent_diaries_preview": "",
            }
        resolved_name = self.normalize_persona_key(persona_ctx["persona_name"], persona_ctx.get("persona_id"))
        persona = self.get_persona_config(resolved_name) or self.cfg.find_persona(resolved_name)
        pool = persona.get("pool", {}) or {}
        session_id = getattr(event, "unified_msg_origin", None) if event else None
        if session_id is None:
            session_id = await self._get_recent_session_id_for_persona(resolved_name, persona_ctx.get("persona_id"))
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        today_weather_pool = pool.get("today_weather") or []
        outfit_styles_pool = pool.get("outfit_styles") or []
        schedule_main_types_pool = pool.get("schedule_main_types") or []
        core_event_drivers_pool = pool.get("core_event_drivers") or []
        today_weather = today_weather_pool[0] if today_weather_pool else ""
        outfit_style = outfit_styles_pool[0] if outfit_styles_pool else ""
        schedule_main_type = schedule_main_types_pool[0] if schedule_main_types_pool else ""
        core_event_driver = core_event_drivers_pool[0] if core_event_drivers_pool else ""
        configured_variation = persona.get("schedule_variation_level", DEFAULT_VARIATION_LEVEL)
        effective_variation = self._effective_variation_level(configured_variation)
        history_structure_summary = self._build_history_structure_summary(resolved_name, 3)
        replacements = {
            "date": date_str,
            "date_str": date_str,
            "weekday": weekday,
            "holiday": "",
            "persona_name": resolved_name,
            "persona_desc": persona_ctx["persona_desc"] or f"当前人格：{resolved_name}",
            "today_weather": today_weather,
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "history_schedules": self.store.recent_history_text(resolved_name, int(persona.get("reference_schedule_days", 0) or 0)),
            "history_structure_summary": history_structure_summary,
            "recent_diaries": await self.collect_recent_diaries_text(resolved_name, persona, session_id),
            "recent_chats": await self.collect_recent_chat_text(event=event, persona=persona, session_id=session_id),
            "daily_theme": "",
            "mood_color": "",
            "schedule_variation_level": effective_variation,
            "schedule_variation_definition": self._variation_definition(effective_variation),
        }
        prompt_template = persona.get("prompt_template") or self.cfg.find_persona(resolved_name).get("prompt_template")
        prompt = render_prompt(prompt_template, replacements)
        prompt += build_format_priority_append_prompt({"outfit_style": outfit_style})
        return {
            "persona_name": resolved_name,
            "persona_source": persona_ctx.get("source", ""),
            "persona_desc_preview": (persona_ctx.get("persona_desc") or "")[:800],
            "outfit_styles_pool": outfit_styles_pool,
            "schedule_main_types_pool": schedule_main_types_pool,
            "core_event_drivers_pool": core_event_drivers_pool,
            "today_weather_pool": today_weather_pool,
            "variation_configured": configured_variation,
            "variation_effective": effective_variation,
            "variation_definition": self._variation_definition(effective_variation),
            "history_structure_summary": history_structure_summary[:800],
            "rendered_prompt_preview": prompt[:2200],
            "recent_chats_preview": replacements["recent_chats"][:500],
            "recent_diaries_preview": replacements["recent_diaries"][:500],
        }

    async def generate_schedule(self, event, persona_name: str, persona_desc: str = "", session_id: str | None = None) -> dict:
        persona_ctx = await self._resolve_persona_context_internal(event=event, persona_name=persona_name, session_id=session_id)
        matched_persona = self.get_persona_config(persona_ctx.get("persona_name"), persona_ctx.get("persona_id"))
        if not matched_persona:
            target_persona = persona_ctx.get("persona_name") or persona_name
            logger.warning(f"[dayflow] reject generation for unconfigured persona={target_persona}")
            return self._build_persona_not_enabled_data(target_persona)

        normalized_persona_name = str(matched_persona.get("name") or self.normalize_persona_key(persona_ctx.get("persona_name"), persona_ctx.get("persona_id"))).strip()
        persona = matched_persona
        pool = persona.get("pool", {}) or {}
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        holiday = ""
        effective_session_id = session_id or (getattr(event, "unified_msg_origin", None) if event else None)
        if effective_session_id is None:
            effective_session_id = await self._get_recent_session_id_for_persona(normalized_persona_name, persona_ctx.get("persona_id"))
        today_weather_pool = pool.get("today_weather") or []
        outfit_styles_pool = pool.get("outfit_styles") or []
        schedule_main_types_pool = pool.get("schedule_main_types") or []
        core_event_drivers_pool = pool.get("core_event_drivers") or []
        today_weather = random.choice(today_weather_pool) if today_weather_pool else "晴，微风，18~26℃"
        outfit_style = random.choice(outfit_styles_pool) if outfit_styles_pool else "自然风"
        schedule_main_type = random.choice(schedule_main_types_pool) if schedule_main_types_pool else "日常常规型"
        core_event_driver = random.choice(core_event_drivers_pool) if core_event_drivers_pool else "任务驱动"
        configured_variation = persona.get("schedule_variation_level", DEFAULT_VARIATION_LEVEL)
        effective_variation = self._effective_variation_level(configured_variation)
        history_structure_summary = self._build_history_structure_summary(normalized_persona_name, 3)
        replacements = {
            "date": date_str,
            "date_str": date_str,
            "weekday": weekday,
            "holiday": holiday,
            "persona_name": normalized_persona_name,
            "persona_desc": persona_desc or persona_ctx.get("persona_desc") or f"当前人格：{normalized_persona_name}",
            "today_weather": today_weather,
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "history_schedules": self.store.recent_history_text(normalized_persona_name, int(persona.get("reference_schedule_days", 0) or 0)),
            "history_structure_summary": history_structure_summary,
            "recent_diaries": await self.collect_recent_diaries_text(normalized_persona_name, persona, effective_session_id),
            "recent_chats": await self.collect_recent_chat_text(event=event, persona=persona, session_id=effective_session_id),
            "daily_theme": "",
            "mood_color": "",
            "schedule_variation_level": effective_variation,
            "schedule_variation_definition": self._variation_definition(effective_variation),
        }
        prompt_template = persona.get("prompt_template") or self.cfg.find_persona(normalized_persona_name).get("prompt_template")
        prompt = render_prompt(prompt_template, replacements)
        prompt += build_format_priority_append_prompt(validate_persona := {
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "today_weather": today_weather,
        })
        self._last_debug_payload = {
            "persona_name": normalized_persona_name,
            "persona_desc_preview": (replacements["persona_desc"] or "")[:1200],
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "today_weather": today_weather,
            "variation_configured": configured_variation,
            "variation_effective": effective_variation,
            "variation_definition": self._variation_definition(effective_variation),
            "history_structure_summary": history_structure_summary[:800],
            "recent_chats_preview": replacements["recent_chats"][:500],
            "recent_diaries_preview": replacements["recent_diaries"][:500],
            "rendered_prompt_preview": prompt[:2500],
        }
        logger.info(
            f"[dayflow-debug] persona={normalized_persona_name} style={outfit_style} "
            f"main={schedule_main_type} driver={core_event_driver} variation={configured_variation}->{effective_variation} "
            f"desc_len={len(replacements['persona_desc'] or '')}, session={effective_session_id or 'none'}"
        )

        configured_provider_id = persona.get("provider_id") or None
        session_provider_id = await self._resolve_session_provider_id(event)
        primary_provider_id = configured_provider_id or session_provider_id
        fallback_provider_id = session_provider_id if configured_provider_id else None
        if primary_provider_id and fallback_provider_id and primary_provider_id == fallback_provider_id:
            fallback_provider_id = None

        repair_retries = int(persona.get("retry_count", 2) or 2)
        actual_provider_id = primary_provider_id
        try:
            raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                prompt,
                primary_provider_id,
                fallback_provider_id,
                retry_count=repair_retries,
            )
            payload = normalize_payload(safe_json_loads(raw_text), validate_persona)
            ok, reason = validate_payload(payload, validate_persona)
            for attempt in range(1, repair_retries + 1):
                if ok and payload:
                    break
                logger.warning(f"[dayflow] payload validation failed for persona={normalized_persona_name}, attempt={attempt}, reason={reason}")
                repair_prompt = build_repair_prompt(prompt, raw_text, reason, validate_persona, retry_index=attempt)
                raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                    repair_prompt,
                    primary_provider_id,
                    fallback_provider_id,
                    retry_count=repair_retries,
                )
                payload = normalize_payload(safe_json_loads(raw_text), validate_persona)
                ok, reason = validate_payload(payload, validate_persona)
            if not ok or not payload:
                return build_generation_error_data(normalized_persona_name, validate_persona, reason or "模型输出未通过校验")
            parsed_style = str(payload.get("outfit_style") or outfit_style).strip()
            outfit = str(payload.get("outfit") or "").strip()
            schedule = str(payload.get("schedule") or "").strip()
            if not outfit or not schedule:
                return build_generation_error_data(normalized_persona_name, validate_persona, "JSON 缺少必要字段")
            today = now.strftime("%Y-%m-%d")
            logger.info(
                f"[dayflow] llm json schedule generated for persona={normalized_persona_name}, "
                f"provider_used={actual_provider_id or 'session_default'}, configured_provider={configured_provider_id or 'none'}, "
                f"session_provider={session_provider_id or 'none'}, session={effective_session_id or 'none'}"
            )
            return {
                "outfit": outfit,
                "schedule": schedule,
                "meta": {
                    "style": parsed_style,
                    "schedule_main_type": schedule_main_type,
                    "core_event_driver": core_event_driver,
                    "persona_name": normalized_persona_name,
                    "date": today,
                    "provider_id": actual_provider_id or "",
                    "configured_provider_id": configured_provider_id or "",
                    "session_provider_id": session_provider_id or "",
                    "source_session_id": effective_session_id or "",
                    "weather": today_weather,
                    "prompt_template_version": "persona_full_template_v7_main_type_driver",
                    "fallback": False,
                    "variation_configured": configured_variation,
                    "variation_effective": effective_variation,
                },
                "timeline": extract_timeline(schedule),
                "weather": today_weather,
                "memo": "",
                "long_term_memory": [],
            }
        except Exception as e:
            logger.warning(
                f"[dayflow] llm generation failed for persona={normalized_persona_name}: {e}, "
                f"configured_provider={configured_provider_id or 'none'}, session_provider={session_provider_id or 'none'}"
            )
            return build_generation_error_data(normalized_persona_name, validate_persona, f"LLM 调用失败: {e}")
