import asyncio
import datetime
import json
import random
import threading
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .config import DayflowConfig
from .constants import DEFAULT_VARIATION_LEVEL, VARIATION_LEVEL_DEFINITIONS
from .generator import (
    build_format_priority_append_prompt,
    build_generation_error_data,
    build_repair_prompt,
    extract_timeline,
    is_schedule_valid,
    normalize_payload,
    render_prompt,
    render_schedule_display,
    safe_json_loads,
    validate_payload,
)
from .store import DayflowStore


STYLE_RESEARCH_SYSTEM_PROMPT = """你是专业服饰风格研究助手。你的任务是基于联网搜索结果，为另一个日程生成模型提供低歧义、可执行的穿搭方案。

## 工作方式
1. 从搜索结果中提炼该风格的核心特征、典型单品和搭配逻辑
2. 不要复述搜索结果原文，要提炼成可直接用于穿搭生成的指令

## 输出格式
输出 JSON 对象，字段如下：
- definition: string，风格定义——这个风格是什么，不是什么，主体风格与辅助元素的关系
- must_keep: string[]，两套穿搭都必须保留的核心识别点，3-6条
- morning_look: string[]，晨间第一套穿搭建议，5-8条，每条须包含：具体单品名称+材质+配色+搭配理由
- afternoon_look: string[]，午后第二套换装建议，5-8条，每条须包含：具体单品名称+材质+配色+搭配理由
- difference: string[]，两套穿搭之间的关键差异点，3-5条（须具体：如"晨间浅粉系轻盈搭配，午后深酒红系沉稳搭配"，而非"风格场景化变化"）
- avoid: string[]，常见误判与禁区，3-6条
- notes: string，补充说明
- weather: string | null，当查询中包含地点信息时，返回该地点今日真实天气，格式为"天气状况，温度范围，风力等细节"（如"多云转晴，18~26℃，东南风3级"）；无地点信息时为 null

## 两套穿搭的要求
1. 必须属于同一风格体系，但必须在视觉上呈现明显差异——不是换件上衣就完事，而是从单品选择、配色深浅、层次搭配、版型轮廓等多维度拉开差距
2. 两套都应完整覆盖从上到下、从里到外的搭配逻辑，让日程生成模型能直接据此写出完整穿搭描述
3. 如果风格是混合风格，两套都须体现主体风格与辅助元素的主辅关系

规则：
1. 必须输出 JSON 对象，不要输出 Markdown 代码块，不要额外解释
2. 结果要去重、压缩、边界清晰
3. 用中文输出"""

STYLE_RESEARCH_QUERY_TEMPLATE = """「{style_name}」穿搭风格 搭配要点 单品推荐 常见误区"""

CUSTOM_SCHEDULE_INTENT_APPEND = """

## 定制日程意图解析（附加任务）

用户定制要求：{extra_requirement}

可用池：
- 穿搭风格池：{outfit_styles_pool}
- 主线类型池：{schedule_main_types_pool}
- 事件驱动池：{core_event_drivers_pool}

请在 JSON 输出中额外包含 intent_overrides 字段：

### intent_overrides 填写规则

1. **outfit_style**（string | null）
   - 用户指定了穿搭风格/单品 → 填写风格大类
   - 优先使用池中相近值（如"洛丽塔"→"甜系洛丽塔风格"，"杏花微雨"→"甜系洛丽塔风格"）
   - 池中确实无匹配 → 用用户原文
   - 用户只是调整当前风格（如"穿暴露点"）→ null，调整写到 outfit_adjustments
   - 与穿搭无关 → null

2. **outfit_item**（string | null）
   - 用户指定了具体单品（如"杏花微雨"）→ 填写单品名
   - 用户只说了风格大类（如"洛丽塔"）→ null
   - 与穿搭无关 → null

3. **schedule_main_type**（string | null）
   - 用户要求影响日程整体走向 → 填写覆盖值
   - 否则 → null

4. **core_event_driver**（string | null）
   - 用户要求涉及事件核心动机 → 填写覆盖值
   - 否则 → null

5. **outfit_adjustments**（string | null）
   - 用户对穿搭有非整体换风格的调整 → 原文转述
   - 例："第二套下身换裤子" → "午后换装下半身改为裤子"
   - 无调整 → null

### 重要提示
- outfit_style 和 outfit_item 可同时存在：style 是风格大类，item 是具体单品
- 如果用户指定了新风格，请基于该风格进行风格研究
- 如果 outfit_adjustments 非 null，请在 morning_look/afternoon_look 中体现调整
"""


class DayflowService:
    PLUGIN_NAME = "astrbot_plugin_dayflow_life_scheduler"
    GROK_PLUGIN_NAME = "astrbot_plugin_grok_web_search"
    LLM_RETRY_DELAY_SECONDS = 2.0
    STYLE_RESEARCH_CACHE_DAYS = 1
    STYLE_RESEARCH_MAX_CHARS = 1200

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
        self._debug_payload_lock = threading.Lock()
        self._style_cache_path = self.data_dir / "style_research_cache.json"
        self._style_research_cache: dict[str, Any] = {}
        self._frozen_randoms: dict[str, dict[str, str]] = {}
        self._last_interaction_times: dict[str, str] = {}

    def _update_debug_payload(self, updates: dict[str, Any]):
        with self._debug_payload_lock:
            self._last_debug_payload.update(updates)

    def record_interaction(self, session_id: str):
        session_key = str(session_id or "").strip()
        if session_key:
            self._last_interaction_times[session_key] = datetime.datetime.now().isoformat()

    def get_last_interaction_time(self, session_id: str) -> str | None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return None
        return self._last_interaction_times.get(session_key)

    def _presence_injection_level(self) -> int:
        try:
            return max(0, min(4, int(self.config.get("presence_injection_level", 2) or 0)))
        except Exception:
            return 2

    def _presence_min_interval_minutes(self) -> int:
        try:
            return max(0, int(self.config.get("presence_min_interval_minutes", 0) or 0))
        except Exception:
            return 0

    @staticmethod
    def _format_time_gap(delta: datetime.timedelta) -> str:
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 1:
            return "不到1分钟"
        if total_minutes < 60:
            return f"约{total_minutes}分钟"
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours < 24:
            if minutes == 0:
                return f"约{hours}小时"
            return f"约{hours}小时{minutes}分钟"
        days = hours // 24
        remaining_hours = hours % 24
        if remaining_hours == 0:
            return f"约{days}天"
        return f"约{days}天{remaining_hours}小时"

    def build_presence_injection(self, session_id: str, level: int | None = None, min_interval: int | None = None) -> str | None:
        effective_level = level if level is not None else self._presence_injection_level()
        if effective_level < 2:
            return None

        last_iso = self.get_last_interaction_time(session_id)
        if not last_iso:
            return None

        try:
            last_dt = datetime.datetime.fromisoformat(last_iso)
        except Exception:
            return None

        now = datetime.datetime.now()
        delta = now - last_dt
        effective_min_interval = min_interval if min_interval is not None else self._presence_min_interval_minutes()
        if effective_min_interval > 0 and delta.total_seconds() < effective_min_interval * 60:
            return None

        current_time = now.strftime("%H:%M")
        last_time = last_dt.strftime("%H:%M")
        time_gap = self._format_time_gap(delta)

        lines = [f"现在是 {current_time}，你上一次与用户互动是在 {last_time}（{time_gap}前）。"]

        if effective_level >= 3:
            lines.append("你可以考虑是否要主动与用户分享今天的日程，但不必刻意。")
        if effective_level >= 4:
            lines[-1] = "你可以考虑是否要主动与用户分享今天的日程、现在的心情或最近的思考，但不必刻意。"

        return "\n".join(lines)

    def _schedule_retention_days(self) -> int:
        try:
            value = int(self.config.get("schedule_retention_days", 3))
            if value == -1:
                return -1
            return max(0, value)
        except Exception:
            return 3

    def _style_research_cache_days(self) -> int:
        return self.STYLE_RESEARCH_CACHE_DAYS

    def _style_research_max_chars(self) -> int:
        return self.STYLE_RESEARCH_MAX_CHARS

    def _style_research_system_prompt(self) -> str:
        value = str(self.config.get("style_research_system_prompt") or "").strip()
        return value or STYLE_RESEARCH_SYSTEM_PROMPT

    def _style_research_query_template(self) -> str:
        value = str(self.config.get("style_research_query_template") or "").strip()
        return value or STYLE_RESEARCH_QUERY_TEMPLATE

    def _custom_schedule_intent_append(self) -> str:
        value = str(self.config.get("custom_schedule_intent_append") or "").strip()
        return value or CUSTOM_SCHEDULE_INTENT_APPEND

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

    def set_tomorrow_custom_request(self, persona_key: str, requirement: str):
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        self.store.set_pending_custom_request(tomorrow, persona_key, requirement)

    def get_tomorrow_custom_request(self, persona_key: str) -> str | None:
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        return self.store.get_pending_custom_request(tomorrow, persona_key)

    def clear_tomorrow_custom_request(self, persona_key: str) -> bool:
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        return self.store.clear_pending_custom_request(tomorrow, persona_key)

    def consume_pending_custom_request(self, target_date: str, persona_key: str) -> str | None:
        return self.store.consume_pending_custom_request(target_date, persona_key)

    def _get_auto_retry_limit(self, persona: dict[str, Any] | None) -> int:
        try:
            return max(int((persona or {}).get("retry_count", 2) or 0), 0)
        except Exception:
            return 2

    def _build_persona_not_enabled_data(self, persona_name: str | None, target_date: str | None = None) -> dict:
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
                "date": target_date or datetime.datetime.now().strftime("%Y-%m-%d"),
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

    def _frozen_randoms_key(self, store_key: str, target_date: str) -> str:
        return f"{store_key}|{target_date}"

    def _get_or_freeze_randoms(
        self,
        store_key: str,
        target_date: str,
        today_weather_pool: list[str],
        outfit_styles_pool: list[str],
        schedule_main_types_pool: list[str],
        core_event_drivers_pool: list[str],
        configured_variation: str,
    ) -> dict[str, str]:
        cache_key = self._frozen_randoms_key(store_key, target_date)
        if cache_key in self._frozen_randoms:
            logger.info(f"[dayflow] reusing frozen randoms: key={cache_key}, values={self._frozen_randoms[cache_key]}")
            return self._frozen_randoms[cache_key]
        today_weather = random.choice(today_weather_pool) if today_weather_pool else "晴，微风，18~26℃"
        outfit_style = random.choice(outfit_styles_pool) if outfit_styles_pool else "自然风"
        schedule_main_type = random.choice(schedule_main_types_pool) if schedule_main_types_pool else "日常常规型"
        core_event_driver = random.choice(core_event_drivers_pool) if core_event_drivers_pool else "任务驱动"
        effective_variation = self._effective_variation_level(configured_variation)
        frozen = {
            "today_weather": today_weather,
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "effective_variation": effective_variation,
        }
        self._frozen_randoms[cache_key] = frozen
        logger.info(f"[dayflow] frozen randoms: key={cache_key}, values={frozen}")
        return frozen

    def clear_frozen_randoms(self, store_key: str, target_date: str):
        cache_key = self._frozen_randoms_key(store_key, target_date)
        if cache_key in self._frozen_randoms:
            logger.info(f"[dayflow] cleared frozen randoms: key={cache_key}")
            del self._frozen_randoms[cache_key]

    def _cleanup_stale_caches(self):
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        stale_keys = [k for k in self._frozen_randoms if not k.endswith(today_str)]
        for k in stale_keys:
            del self._frozen_randoms[k]
        if stale_keys:
            logger.info(f"[dayflow] cleaned up {len(stale_keys)} stale frozen_randoms entries")

        cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
        stale_sessions = [
            sid for sid, iso in self._last_interaction_times.items()
            if iso < cutoff.isoformat()
        ]
        for sid in stale_sessions:
            del self._last_interaction_times[sid]
        if stale_sessions:
            logger.info(f"[dayflow] cleaned up {len(stale_sessions)} stale interaction time entries")

    def _load_style_research_cache(self):
        try:
            if self._style_cache_path.exists():
                data = json.loads(self._style_cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._style_research_cache = data
        except Exception as e:
            logger.warning(f"[dayflow] load style research cache failed: {e}")
            self._style_research_cache = {}

    def _save_style_research_cache(self):
        try:
            self._style_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._style_cache_path.write_text(
                json.dumps(self._style_research_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[dayflow] save style research cache failed: {e}")

    def _is_style_cache_entry_valid(self, entry: dict[str, Any] | None) -> bool:
        if not isinstance(entry, dict):
            return False
        if not entry.get("summary"):
            return False
        cache_days = self._style_research_cache_days()
        if cache_days <= 0:
            return False
        updated_at = str(entry.get("updated_at") or "").strip()
        if not updated_at:
            return False
        try:
            updated = datetime.datetime.fromisoformat(updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            age = now - updated.astimezone(datetime.timezone.utc)
            return age <= datetime.timedelta(days=cache_days)
        except Exception:
            return False

    def _normalize_style_key(self, style_name: str) -> str:
        return "".join(str(style_name or "").strip().lower().split())

    def _clip_list(self, value: Any, max_items: int = 6, max_chars: int = 80) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        seen = set()
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            text = text.replace("\r", " ").replace("\n", " ").strip()
            key = "".join(text.lower().split())
            if not key or key in seen:
                continue
            seen.add(key)
            if len(text) > max_chars:
                text = text[:max_chars].rstrip("，,；;。.!?？") + "…"
            result.append(text)
            if len(result) >= max_items:
                break
        return result

    def _preview_text(self, text: Any, limit: int = 1200) -> str:
        content = str(text or "").strip()
        if len(content) <= limit:
            return content
        return content[:limit].rstrip() + "…"

    def _render_sources_preview(self, sources: list[dict[str, str]] | None) -> str:
        lines = []
        for src in sources or []:
            title = str((src or {}).get("title") or "").strip()
            url = str((src or {}).get("url") or "").strip()
            snippet = self._preview_text((src or {}).get("snippet") or "", limit=120)
            if not url:
                continue
            line = f"- {title or '未命名来源'}\n  {url}"
            if snippet:
                line += f"\n  {snippet}"
            lines.append(line)
            if len(lines) >= 3:
                break
        return "\n".join(lines)

    def _render_style_reference(self, style_name: str, payload: dict[str, Any] | None, sources: list[dict[str, str]] | None = None) -> str:
        if not payload:
            return "（本次未获取到联网风格研究结果，请严格按风格名本身与上下文谨慎生成，禁止想当然混入相近风格。）"
        definition = str(payload.get("definition") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        must_keep = self._clip_list(payload.get("must_keep"), max_items=6)
        morning_look = self._clip_list(payload.get("morning_look"), max_items=8)
        afternoon_look = self._clip_list(payload.get("afternoon_look"), max_items=8)
        difference = self._clip_list(payload.get("difference"), max_items=5)
        avoid = self._clip_list(payload.get("avoid"), max_items=6)
        lines = [f"风格名：{style_name}"]
        if definition:
            lines.append(f"风格定义：{definition}")
        if must_keep:
            lines.append("两套穿搭都必须保留的核心识别点：")
            lines.extend(f"- {item}" for item in must_keep)
        if morning_look:
            lines.append("晨间第一套建议：")
            lines.extend(f"- {item}" for item in morning_look)
        if afternoon_look:
            lines.append("午后第二套换装建议：")
            lines.extend(f"- {item}" for item in afternoon_look)
        if difference:
            lines.append("两套穿搭之间的关键差异：")
            lines.extend(f"- {item}" for item in difference)
        if avoid:
            lines.append("常见误判与禁区：")
            lines.extend(f"- {item}" for item in avoid)
        if notes:
            lines.append(f"补充说明：{notes}")
        source_urls = []
        for src in sources or []:
            url = str((src or {}).get("url") or "").strip()
            if url and url not in source_urls:
                source_urls.append(url)
            if len(source_urls) >= 3:
                break
        if source_urls:
            lines.append("参考来源：")
            lines.extend(f"- {url}" for url in source_urls)
        text = "\n".join(lines).strip()
        max_chars = self._style_research_max_chars()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return text

    def _render_style_reference_from_plain_text(self, style_name: str, plain_text: str, sources: list[dict[str, str]] | None = None) -> str:
        lines = [f"风格名：{style_name}"]
        plain_text = self._preview_text(plain_text, limit=self._style_research_max_chars())
        if plain_text:
            lines.append(plain_text)
        source_urls = []
        for src in sources or []:
            url = str((src or {}).get("url") or "").strip()
            if url and url not in source_urls:
                source_urls.append(url)
            if len(source_urls) >= 3:
                break
        if source_urls:
            lines.append("参考来源：")
            lines.extend(f"- {url}" for url in source_urls)
        return "\n".join(lines).strip()

    def _find_grok_plugin(self):
        try:
            stars = self.context.get_all_stars()
        except Exception as e:
            logger.debug(f"[dayflow] get_all_stars failed when finding grok: {e}")
            stars = []
        for meta in stars or []:
            plugin_name = str(getattr(meta, "name", "") or "").strip()
            root_dir_name = str(getattr(meta, "root_dir_name", "") or "").strip()
            module_path = str(getattr(meta, "module_path", "") or "").strip()
            if plugin_name != self.GROK_PLUGIN_NAME and root_dir_name != self.GROK_PLUGIN_NAME and self.GROK_PLUGIN_NAME not in module_path:
                continue
            for attr in ("star", "instance", "plugin", "obj", "star_cls"):
                candidate = getattr(meta, attr, None)
                if candidate is not None and hasattr(candidate, "_do_search"):
                    return candidate
        for star in self._iter_loaded_stars():
            cls_module = getattr(star.__class__, "__module__", "")
            if self.GROK_PLUGIN_NAME in cls_module and hasattr(star, "_do_search"):
                return star
        return None

    def _build_style_research_query(self, style_name: str, location: str | None = None) -> str:
        template = self._style_research_query_template()
        try:
            query = template.format(style_name=style_name)
        except Exception as e:
            logger.warning(f"[dayflow] invalid style research query template, fallback to default: {e}")
            query = STYLE_RESEARCH_QUERY_TEMPLATE.format(style_name=style_name)
        if location and str(location).strip():
            query += f" | 同时查询{str(location).strip()}今日真实天气"
        return query

    async def _research_style_reference(self, style_name: str, extra_requirement: str | None = None, pool_options: dict | None = None, location: str | None = None) -> tuple[str, dict[str, Any], list[dict[str, str]], dict[str, Any] | None, str | None]:
        style_name = str(style_name or "").strip()
        if not style_name:
            return "", {}, [], None, None
        cache_key = self._normalize_style_key(style_name)
        cached = self._style_research_cache.get(cache_key)
        if self._is_style_cache_entry_valid(cached):
            payload = dict(cached.get("payload") or {})
            sources = list(cached.get("sources") or [])
            summary = str(cached.get("summary") or "")
            cached_weather = str(cached.get("weather") or "").strip() or None
            self._update_debug_payload({
                "style_research_cache_hit": True,
                "style_research_query_preview": self._preview_text(self._build_style_research_query(style_name, location=location), limit=1200),
                "style_research_system_prompt_preview": self._preview_text(self._style_research_system_prompt(), limit=1200),
                "style_research_raw_response_preview": self._preview_text(cached.get("raw_response") or "", limit=1600),
                "style_research_payload_preview": self._preview_text(json.dumps(payload, ensure_ascii=False, indent=2), limit=1600) if payload else "",
                "style_research_sources_preview": self._render_sources_preview(sources),
                "style_research_weather": cached_weather,
            })
            logger.info(f"[dayflow-style-research] cache hit | style={style_name} | weather={cached_weather}")
            return summary, payload, sources, None, cached_weather

        grok = self._find_grok_plugin()
        if grok is None:
            logger.warning(f"[dayflow] grok plugin not found, skip style research: style={style_name}")
            return "", {}, [], None, None

        query = self._build_style_research_query(style_name, location=location)
        system_prompt = self._style_research_system_prompt()

        intent_overrides = None
        if extra_requirement and pool_options:
            query += f" | 用户定制要求：{extra_requirement}"
            try:
                system_prompt += self._custom_schedule_intent_append().format(
                    extra_requirement=extra_requirement,
                    outfit_styles_pool=json.dumps(pool_options.get("outfit_styles", []), ensure_ascii=False),
                    schedule_main_types_pool=json.dumps(pool_options.get("schedule_main_types", []), ensure_ascii=False),
                    core_event_drivers_pool=json.dumps(pool_options.get("core_event_drivers", []), ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"[dayflow] custom schedule intent append format failed: {e}")

        logger.info(f"[dayflow-style-research] query | style={style_name} | location={location} | query={query}")
        result = await grok._do_search(query=query, system_prompt=system_prompt, use_retry=True)

        sources = list(result.get("sources") or [])
        raw_text = ""
        parsed_payload = None
        last_reason = ""
        real_weather = None

        if not result.get("ok"):
            last_reason = str(result.get("error") or "grok search failed")
            raw_from_error = str(result.get("raw") or "").strip()
            if raw_from_error:
                raw_text = raw_from_error
                logger.warning(f"[dayflow] style research search failed but raw content available, degrading: style={style_name}, error={last_reason}")
            else:
                logger.warning(f"[dayflow] style research search failed: style={style_name}, reason={last_reason}")
        else:
            raw_text = str(result.get("content") or "").strip()
            logger.info(f"[dayflow-style-research] raw_response | style={style_name} | content={self._preview_text(raw_text, limit=1600)}")
            parsed = safe_json_loads(raw_text)
            if isinstance(parsed, dict) and parsed.get("definition"):
                parsed_payload = parsed
            else:
                last_reason = "研究结果不是有效 JSON"
                logger.warning(f"[dayflow] style research parse failed: style={style_name}, reason={last_reason}")

        if parsed_payload:
            if extra_requirement and isinstance(parsed_payload.get("intent_overrides"), dict):
                intent_overrides = parsed_payload.pop("intent_overrides")
                logger.info(f"[dayflow-style-research] intent_overrides | style={style_name} | overrides={json.dumps(intent_overrides, ensure_ascii=False)}")
            weather_value = parsed_payload.pop("weather", None)
            if isinstance(weather_value, str) and weather_value.strip():
                real_weather = weather_value.strip()
                logger.info(f"[dayflow-style-research] weather | style={style_name} | location={location} | weather={real_weather}")
            summary = self._render_style_reference(style_name, parsed_payload, sources)
            self._style_research_cache[cache_key] = {
                "style_name": style_name,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "payload": parsed_payload,
                "summary": summary,
                "sources": sources,
                "raw_response": raw_text,
                "weather": real_weather or "",
            }
            self._save_style_research_cache()
            payload_preview = json.dumps(parsed_payload, ensure_ascii=False, indent=2)
            logger.info(f"[dayflow-style-research] parsed_payload | style={style_name} | payload={self._preview_text(payload_preview, limit=1600)}")
            logger.info(f"[dayflow-style-research] sources | style={style_name} | sources={self._render_sources_preview(sources)}")
            self._update_debug_payload({
                "style_research_cache_hit": False,
                "style_research_query_preview": self._preview_text(query, limit=1200),
                "style_research_system_prompt_preview": self._preview_text(system_prompt, limit=1200),
                "style_research_raw_response_preview": self._preview_text(raw_text, limit=1600),
                "style_research_payload_preview": self._preview_text(payload_preview, limit=1600),
                "style_research_sources_preview": self._render_sources_preview(sources),
                "style_research_weather": real_weather,
            })
            logger.info(f"[dayflow] style research success(json): style={style_name}, sources={len(sources)}, weather={real_weather}")
            return summary, parsed_payload, sources, intent_overrides, real_weather

        if raw_text:
            summary = self._render_style_reference_from_plain_text(style_name, raw_text, sources)
            self._style_research_cache[cache_key] = {
                "style_name": style_name,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "payload": {},
                "summary": summary,
                "sources": sources,
                "raw_response": raw_text,
                "weather": "",
            }
            self._save_style_research_cache()
            self._update_debug_payload({
                "style_research_cache_hit": False,
                "style_research_query_preview": self._preview_text(query, limit=1200),
                "style_research_system_prompt_preview": self._preview_text(system_prompt, limit=1200),
                "style_research_raw_response_preview": self._preview_text(raw_text, limit=1600),
                "style_research_payload_preview": "（JSON 解析失败，已降级使用纯文本研究结果）",
                "style_research_sources_preview": self._render_sources_preview(sources),
                "style_research_weather": None,
            })
            logger.info(f"[dayflow-style-research] fallback_plain_text | style={style_name} | summary={self._preview_text(summary, limit=1600)}")
            logger.info(f"[dayflow-style-research] sources | style={style_name} | sources={self._render_sources_preview(sources)}")
            logger.warning(f"[dayflow] style research downgraded to plain text: style={style_name}, reason={last_reason or '非 JSON 输出'}")
            return summary, {}, sources, intent_overrides, None

        if last_reason:
            logger.warning(f"[dayflow] style research unavailable: style={style_name}, reason={last_reason}")
        self._update_debug_payload({
            "style_research_cache_hit": False,
            "style_research_query_preview": self._preview_text(query, limit=1200),
            "style_research_system_prompt_preview": self._preview_text(system_prompt, limit=1200),
            "style_research_raw_response_preview": "",
            "style_research_payload_preview": "",
            "style_research_sources_preview": self._render_sources_preview(sources),
            "style_research_weather": None,
        })
        return "", {}, sources, intent_overrides, None

    async def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store.initialize()
        self.store.set_retention_days(self._schedule_retention_days())
        self._load_style_research_cache()
        self._cleanup_stale_caches()
        logger.info("[dayflow] plugin initialized")
        logger.info(f"[dayflow] loaded personas: {[p.get('name') for p in self.cfg.personas()]}")
        await self.start_scheduler()

    async def terminate(self):
        await self.stop_scheduler()
        self.store.prune_expired(force=True)
        await self.store.async_save_state()
        self._save_style_research_cache()
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
        loop_count = 0
        while self._scheduler_running:
            try:
                await self.run_due_generations()
                loop_count += 1
                if loop_count % 120 == 0:
                    self._cleanup_stale_caches()
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
                self.store.clear_auto_generation_failure(store_key, trigger_key)
                self.store.mark_auto_generation_consumed(store_key, trigger_key)
                continue

            auto_retry_limit = self._get_auto_retry_limit(persona)
            failure_count = self.store.get_auto_generation_failure_count(store_key, trigger_key)
            if failure_count >= auto_retry_limit + 1:
                self.store.mark_auto_generation_consumed(store_key, trigger_key)
                logger.warning(
                    f"[dayflow] auto generation retry limit reached, stop retrying today: "
                    f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}, limit={auto_retry_limit}"
                )
                continue

            ok = await self.enter_generation(store_key)
            if not ok:
                continue
            try:
                persona_ctx = await self._resolve_persona_context_internal(persona_name=configured_persona_name)
                auto_session_id = await self._get_recent_session_id_for_persona(configured_persona_name, persona_ctx.get("persona_id"))
                pending_requirement = self.consume_pending_custom_request(today, store_key)
                data = await self.generate_schedule(
                    event=None,
                    persona_name=configured_persona_name,
                    persona_desc=persona_ctx["persona_desc"],
                    session_id=auto_session_id,
                    target_date=today,
                    auto_retry=False,
                    extra_requirement=pending_requirement,
                )
                if not data.get("meta", {}).get("error"):
                    await self.save_generated(store_key, data)
                    await self.push_schedule_to_targets(store_key, data)
                    self.store.clear_auto_generation_failure(store_key, trigger_key)
                    self.store.mark_auto_generation_consumed(store_key, trigger_key)
                    logger.info(
                        f"[dayflow] auto generated schedule for persona={configured_persona_name}, "
                        f"store_key={store_key}, trigger={trigger_key}, session={auto_session_id or 'none'}"
                    )
                else:
                    failure_count = self.store.record_auto_generation_failure(store_key, trigger_key)
                    if failure_count >= auto_retry_limit + 1:
                        self.store.mark_auto_generation_consumed(store_key, trigger_key)
                        logger.warning(
                            f"[dayflow] auto generation failed and reached retry limit, stop retrying today: "
                            f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}, limit={auto_retry_limit}, reason={data.get('memo', '')}"
                        )
                    else:
                        logger.warning(
                            f"[dayflow] auto generation failed, will retry later: "
                            f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}/{auto_retry_limit + 1}, reason={data.get('memo', '')}"
                        )
            except Exception as e:
                failure_count = self.store.record_auto_generation_failure(store_key, trigger_key)
                if failure_count >= auto_retry_limit + 1:
                    self.store.mark_auto_generation_consumed(store_key, trigger_key)
                    logger.warning(
                        f"[dayflow] auto generation exception and reached retry limit, stop retrying today: "
                        f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}, limit={auto_retry_limit}, error={e}"
                    )
                else:
                    logger.warning(
                        f"[dayflow] auto generation failed for persona={configured_persona_name}: {e}, "
                        f"failures={failure_count}/{auto_retry_limit + 1}, trigger={trigger_key}"
                    )
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

    def get_cached_or_fallback(self, persona_name: str, target_date: str | None = None) -> dict:
        store_key = self.normalize_persona_key(persona_name)
        if target_date:
            exact = self.store.get_schedule_for_date(store_key, target_date)
            if exact:
                return exact
        latest = self.store.get_latest_schedule(store_key)
        if latest:
            return latest
        return {
            "outfit": "尚未生成",
            "schedule": "暂无日程记录，请使用 /生成日程 命令。",
            "meta": {"persona_name": store_key, "fallback": True, "fallback_reason": "无历史日程", "date": target_date or ""},
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

    def _build_missing_today_context(self, store_key: str, target_date: str, latest: dict | None = None, fallback_reason: str = "目标日期尚无有效日程") -> dict:
        latest_date = str((latest or {}).get("meta", {}).get("date") or "")
        return {
            "outfit": "尚未生成",
            "schedule": f"{target_date} 的日程尚未生成成功，请先重新生成该日期日程。",
            "meta": {
                "persona_name": store_key,
                "date": target_date,
                "fallback": True,
                "fallback_reason": fallback_reason,
                "latest_available_date": latest_date,
                "error": True,
            },
            "timeline": [],
            "weather": "",
            "memo": fallback_reason,
            "long_term_memory": [],
        }

    async def get_life_context(self, session_id: str | None = None, persona_name: str | None = None, target_date: str | None = None) -> dict:
        effective_date = str(target_date or datetime.datetime.now().strftime("%Y-%m-%d")).strip() or datetime.datetime.now().strftime("%Y-%m-%d")
        resolved_ctx = await self._resolve_persona_context_internal(session_id=session_id, persona_name=persona_name)
        if not self.is_persona_configured(resolved_ctx.get("persona_name"), resolved_ctx.get("persona_id")):
            reason = f"人格未在 Dayflow 中启用：{resolved_ctx.get('persona_name') or persona_name or '（未识别人格）'}"
            logger.warning(
                f"[dayflow] get_life_context rejected unconfigured persona: session={session_id or ''}, "
                f"requested_persona={persona_name or ''}, resolved_persona={resolved_ctx.get('persona_name', '')}, "
                f"resolved_persona_id={resolved_ctx.get('persona_id', '')}, target_date={effective_date}"
            )
            return {
                "outfit": "尚未生成",
                "schedule": reason,
                "meta": {
                    "persona_name": str(resolved_ctx.get('persona_name') or persona_name or '').strip(),
                    "date": effective_date,
                    "fallback": True,
                    "fallback_reason": reason,
                    "persona_not_enabled": True,
                    "error": True,
                },
                "timeline": [],
                "weather": "",
                "memo": reason,
                "long_term_memory": [],
            }

        candidate_keys = self._candidate_store_keys(resolved_ctx=resolved_ctx, persona_name=persona_name)

        for store_key in candidate_keys:
            exact_schedule = self.store.get_schedule_for_date(store_key, effective_date)
            if exact_schedule and not exact_schedule.get("meta", {}).get("error"):
                logger.info(
                    f"[dayflow] get_life_context hit target schedule: session={session_id or ''}, "
                    f"requested_persona={persona_name or ''}, resolved_persona={resolved_ctx.get('persona_name', '')}, "
                    f"resolved_persona_id={resolved_ctx.get('persona_id', '')}, store_key={store_key}, date={effective_date}"
                )
                return exact_schedule

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
            f"[dayflow] get_life_context missing target schedule: session={session_id or ''}, "
            f"requested_persona={persona_name or ''}, resolved_persona={resolved_ctx.get('persona_name', '')}, "
            f"resolved_persona_id={resolved_ctx.get('persona_id', '')}, candidate_keys={candidate_keys}, "
            f"target_date={effective_date}, latest_date={latest_date or 'none'}, returning_fallback=yes"
        )
        return self._build_missing_today_context(
            store_key=latest_store_key,
            target_date=effective_date,
            latest=latest,
            fallback_reason="目标日期尚无有效日程，已拒绝回退到其他日期旧日程",
        )

    async def enter_generation(self, persona_name: str) -> bool:
        return await self.store.enter_generation(self.normalize_persona_key(persona_name))

    async def exit_generation(self, persona_name: str):
        await self.store.exit_generation(self.normalize_persona_key(persona_name))

    async def save_generated(self, persona_name: str, data: dict):
        requested_store_key = self.normalize_persona_key(persona_name)
        if data.get("meta", {}).get("error"):
            logger.warning(f"[dayflow] skip saving error result for persona={requested_store_key}")
            return

        saved_date = str((data.get("meta") or {}).get("date") or datetime.datetime.now().strftime("%Y-%m-%d"))
        self.clear_frozen_randoms(requested_store_key, saved_date)

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
        await self.store.save_schedule(store_key, data)
        logger.info(
            f"[dayflow] schedule saved: store_key={store_key}, date={saved_date}, "
            f"history_count={self.store.get_history_count(store_key)}, "
            f"memory_current_date={self.store.get_memory_date(store_key)}"
        )

    def _render_push_content(self, data: dict) -> str:
        return render_schedule_display(data)

    async def push_schedule_to_targets(self, persona_name: str, data: dict):
        if not is_schedule_valid(data):
            return
        persona = self.get_persona_config(persona_name) or self.cfg.find_persona(persona_name)
        if not persona:
            return
        push_targets = persona.get("push_targets") or []
        if not push_targets:
            return

        content = self._render_push_content(data)
        if not content.strip():
            return

        chain = MessageChain([Plain(content)])
        for target_umo in push_targets:
            target_umo = str(target_umo or "").strip()
            if not target_umo:
                continue
            try:
                await self.context.send_message(target_umo, chain)
                logger.info(f"[dayflow] schedule pushed to {target_umo} for persona={persona_name}")
            except Exception as e:
                logger.warning(f"[dayflow] push to {target_umo} failed for persona={persona_name}: {e}")

    def describe_personas(self) -> list[str]:
        retention = self._schedule_retention_days()
        retention_text = "无限制" if retention == -1 else f"{retention}天"
        return [
            f"- {item['name']} @ {item.get('generate_time', '07:00')} ({item.get('provider_id') or 'current_provider'} -> session_fallback) | 重试:{item.get('retry_count', 2)} | 变化:{item.get('schedule_variation_level', DEFAULT_VARIATION_LEVEL)} | 持久化:{retention_text} | 推送:{len(item.get('push_targets') or [])}个目标"
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
        effective_id = provider_id
        if not effective_id:
            effective_id = await self._get_default_provider_id()
        if not effective_id:
            raise RuntimeError("[dayflow] no provider available for llm_generate")
        llm_resp = await self.context.llm_generate(chat_provider_id=effective_id, prompt=prompt)
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

    def _get_provider_id_from_instance(self, provider) -> str | None:
        if provider is None:
            return None
        try:
            meta = provider.meta()
            if meta and getattr(meta, "id", None):
                return str(meta.id).strip() or None
        except Exception:
            pass
        for attr in ("id", "provider_id"):
            val = getattr(provider, attr, None)
            if val and str(val).strip():
                return str(val).strip()
        return None

    async def _get_default_provider_id(self) -> str | None:
        try:
            if hasattr(self.context, "get_using_provider"):
                provider = self.context.get_using_provider()
                pid = self._get_provider_id_from_instance(provider)
                if pid:
                    return pid
            if hasattr(self.context, "provider_manager"):
                pm = self.context.provider_manager
                if hasattr(pm, "get_using_provider"):
                    provider = pm.get_using_provider()
                    pid = self._get_provider_id_from_instance(provider)
                    if pid:
                        return pid
                if hasattr(pm, "selected_provider_id"):
                    return pm.selected_provider_id or None
        except Exception as e:
            logger.debug(f"[dayflow] get_default_provider_id failed: {e}")
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
        primary_failed = False

        should_try_primary = True
        if primary_provider_id is None and fallback_provider_id is not None:
            should_try_primary = False

        if should_try_primary:
            try:
                text = await self.call_llm_with_retries(prompt, primary_provider_id, retry_count=retry_count)
                if text:
                    return text, primary_provider_id
                last_text = text
                primary_failed = True
                logger.warning(
                    f"[dayflow] primary provider exhausted with empty result, switching to fallback: provider={primary_provider_id or 'session_default'}"
                )
            except Exception as e:
                last_error = e
                primary_failed = True
                logger.warning(
                    f"[dayflow] primary provider exhausted retries, switching to fallback: "
                    f"provider={primary_provider_id or 'session_default'}, error={e}"
                )

        if fallback_provider_id is not None and (primary_failed or not should_try_primary):
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
        return last_text, (fallback_provider_id if last_text and primary_failed and fallback_provider_id is not None else primary_provider_id)

    def get_last_debug_payload(self) -> dict[str, Any]:
        with self._debug_payload_lock:
            return dict(self._last_debug_payload)

    async def build_debug_snapshot(self, event=None, persona_name: str | None = None) -> dict[str, Any]:
        persona_ctx = await self.resolve_persona_context(event=event, persona_name=persona_name)
        resolved_name = self.normalize_persona_key(persona_ctx.get("persona_name"), persona_ctx.get("persona_id"))
        persona = self.get_persona_config(resolved_name) or self.cfg.find_persona(resolved_name)
        if not persona:
            return {
                "persona_name": persona_ctx.get("persona_name", ""),
                "persona_source": persona_ctx.get("source", ""),
                "persona_desc_preview": (persona_ctx.get("persona_desc") or "")[:800],
                "outfit_styles_pool": [],
                "schedule_main_types_pool": [],
                "core_event_drivers_pool": [],
                "today_weather_pool": [],
                "location": "",
                "variation_configured": "",
                "variation_effective": "",
                "variation_definition": "",
                "style_research_cache_hit": "",
                "style_research_query_preview": "",
                "style_research_system_prompt_preview": "",
                "style_research_raw_response_preview": "",
                "style_research_payload_preview": "",
                "style_research_sources_preview": "",
                "style_research_weather": "",
                "rendered_prompt_preview": f"人格未在 Dayflow 中启用：{persona_ctx.get('persona_name', '')}",
                "recent_chats_preview": "",
                "recent_diaries_preview": "",
            }

        pool = persona.get("pool", {}) or {}
        outfit_styles_pool = pool.get("outfit_styles") or []
        schedule_main_types_pool = pool.get("schedule_main_types") or []
        core_event_drivers_pool = pool.get("core_event_drivers") or []
        today_weather_pool = pool.get("today_weather") or []

        last_payload = self.get_last_debug_payload()
        if last_payload and self.normalize_persona_key(last_payload.get("persona_name")) == resolved_name:
            return {
                "persona_name": resolved_name,
                "persona_source": persona_ctx.get("source", ""),
                "persona_desc_preview": last_payload.get("persona_desc_preview", "")[:800],
                "outfit_styles_pool": outfit_styles_pool,
                "schedule_main_types_pool": schedule_main_types_pool,
                "core_event_drivers_pool": core_event_drivers_pool,
                "today_weather_pool": today_weather_pool,
                "location": persona.get("location", ""),
                "variation_configured": last_payload.get("variation_configured", ""),
                "variation_effective": last_payload.get("variation_effective", ""),
                "variation_definition": last_payload.get("variation_definition", ""),
                "style_research_cache_hit": last_payload.get("style_research_cache_hit", ""),
                "style_research_query_preview": last_payload.get("style_research_query_preview", ""),
                "style_research_system_prompt_preview": last_payload.get("style_research_system_prompt_preview", ""),
                "style_research_raw_response_preview": last_payload.get("style_research_raw_response_preview", ""),
                "style_research_payload_preview": last_payload.get("style_research_payload_preview", ""),
                "style_research_sources_preview": last_payload.get("style_research_sources_preview", ""),
                "style_research_weather": last_payload.get("style_research_weather", ""),
                "rendered_prompt_preview": last_payload.get("rendered_prompt_preview", "")[:2200],
                "recent_chats_preview": last_payload.get("recent_chats_preview", "")[:500],
                "recent_diaries_preview": last_payload.get("recent_diaries_preview", "")[:500],
            }

        session_id = getattr(event, "unified_msg_origin", None) if event else None
        if session_id is None:
            session_id = await self._get_recent_session_id_for_persona(resolved_name, persona_ctx.get("persona_id"))
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        today_weather = today_weather_pool[0] if today_weather_pool else ""
        outfit_style = outfit_styles_pool[0] if outfit_styles_pool else ""
        schedule_main_type = schedule_main_types_pool[0] if schedule_main_types_pool else ""
        core_event_driver = core_event_drivers_pool[0] if core_event_drivers_pool else ""
        configured_variation = persona.get("schedule_variation_level", DEFAULT_VARIATION_LEVEL)
        effective_variation = self._effective_variation_level(configured_variation)
        style_reference, _, _, _, _ = await self._research_style_reference(outfit_style)
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
            "history_structure_summary": "",
            "recent_diaries": await self.collect_recent_diaries_text(resolved_name, persona, session_id),
            "recent_chats": await self.collect_recent_chat_text(event=event, persona=persona, session_id=session_id),
            "daily_theme": "",
            "mood_color": "",
            "style_reference": style_reference,
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
            "location": persona.get("location", ""),
            "variation_configured": configured_variation,
            "variation_effective": effective_variation,
            "variation_definition": self._variation_definition(effective_variation),
            "style_research_cache_hit": self.get_last_debug_payload().get("style_research_cache_hit", ""),
            "style_research_query_preview": self.get_last_debug_payload().get("style_research_query_preview", ""),
            "style_research_system_prompt_preview": self.get_last_debug_payload().get("style_research_system_prompt_preview", ""),
            "style_research_raw_response_preview": self.get_last_debug_payload().get("style_research_raw_response_preview", ""),
            "style_research_payload_preview": self.get_last_debug_payload().get("style_research_payload_preview", ""),
            "style_research_sources_preview": self.get_last_debug_payload().get("style_research_sources_preview", ""),
            "style_research_weather": self.get_last_debug_payload().get("style_research_weather", ""),
            "rendered_prompt_preview": prompt[:2200],
            "recent_chats_preview": replacements["recent_chats"][:500],
            "recent_diaries_preview": replacements["recent_diaries"][:500],
        }

    async def generate_schedule(self, event, persona_name: str, persona_desc: str = "", session_id: str | None = None, target_date: str | None = None, auto_retry: bool = False, extra_requirement: str | None = None, force_regenerate: bool = False) -> dict:
        persona_ctx = await self._resolve_persona_context_internal(event=event, persona_name=persona_name, session_id=session_id)
        matched_persona = self.get_persona_config(persona_ctx.get("persona_name"), persona_ctx.get("persona_id"))
        effective_date = str(target_date or datetime.datetime.now().strftime("%Y-%m-%d")).strip() or datetime.datetime.now().strftime("%Y-%m-%d")
        if not matched_persona:
            target_persona = persona_ctx.get("persona_name") or persona_name
            logger.warning(f"[dayflow] reject generation for unconfigured persona={target_persona}")
            return self._build_persona_not_enabled_data(target_persona, target_date=effective_date)

        normalized_persona_name = str(matched_persona.get("name") or self.normalize_persona_key(persona_ctx.get("persona_name"), persona_ctx.get("persona_id"))).strip()
        persona = matched_persona
        pool = persona.get("pool", {}) or {}
        now = datetime.datetime.now()
        date_str = effective_date
        try:
            target_dt = datetime.datetime.strptime(effective_date, "%Y-%m-%d")
        except Exception:
            target_dt = now
            date_str = now.strftime("%Y-%m-%d")
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][target_dt.weekday()]
        holiday = ""
        effective_session_id = session_id or (getattr(event, "unified_msg_origin", None) if event else None)
        if effective_session_id is None:
            effective_session_id = await self._get_recent_session_id_for_persona(normalized_persona_name, persona_ctx.get("persona_id"))
        today_weather_pool = pool.get("today_weather") or []
        outfit_styles_pool = pool.get("outfit_styles") or []
        schedule_main_types_pool = pool.get("schedule_main_types") or []
        core_event_drivers_pool = pool.get("core_event_drivers") or []
        configured_variation = persona.get("schedule_variation_level", DEFAULT_VARIATION_LEVEL)

        if force_regenerate:
            self.clear_frozen_randoms(normalized_persona_name, date_str)

        frozen = self._get_or_freeze_randoms(
            store_key=normalized_persona_name,
            target_date=date_str,
            today_weather_pool=today_weather_pool,
            outfit_styles_pool=outfit_styles_pool,
            schedule_main_types_pool=schedule_main_types_pool,
            core_event_drivers_pool=core_event_drivers_pool,
            configured_variation=configured_variation,
        )
        today_weather = frozen["today_weather"]
        outfit_style = frozen["outfit_style"]
        schedule_main_type = frozen["schedule_main_type"]
        core_event_driver = frozen["core_event_driver"]
        effective_variation = frozen["effective_variation"]

        pool_options = None
        if extra_requirement:
            pool_options = {
                "outfit_styles": outfit_styles_pool,
                "schedule_main_types": schedule_main_types_pool,
                "core_event_drivers": core_event_drivers_pool,
            }

        persona_location = str(persona.get("location") or "").strip()
        style_reference, style_payload, style_sources, intent_overrides, real_weather = await self._research_style_reference(
            outfit_style, extra_requirement=extra_requirement, pool_options=pool_options, location=persona_location or None,
        )

        if real_weather:
            today_weather = real_weather
            logger.info(f"[dayflow] real weather override: persona={normalized_persona_name}, location={persona_location}, weather={today_weather}")

        user_specified_outfit_style = None
        user_specified_outfit_item = None
        outfit_adjustments = None
        if intent_overrides:
            override_style = intent_overrides.get("outfit_style")
            if override_style and override_style != outfit_style:
                style_reference, style_payload, style_sources, _, override_weather = await self._research_style_reference(override_style, location=persona_location or None)
                if override_weather:
                    real_weather = override_weather
                    today_weather = override_weather
                    logger.info(f"[dayflow] real weather override (intent override): persona={normalized_persona_name}, location={persona_location}, weather={today_weather}")
                outfit_style = override_style
                user_specified_outfit_style = override_style
            elif override_style:
                outfit_style = override_style
                user_specified_outfit_style = override_style
            override_item = intent_overrides.get("outfit_item")
            if override_item:
                user_specified_outfit_item = override_item
            override_main_type = intent_overrides.get("schedule_main_type")
            if override_main_type:
                schedule_main_type = override_main_type
            override_driver = intent_overrides.get("core_event_driver")
            if override_driver:
                core_event_driver = override_driver
            override_adjustments = intent_overrides.get("outfit_adjustments")
            if override_adjustments:
                outfit_adjustments = override_adjustments
            logger.info(
                f"[dayflow] intent overrides applied: persona={normalized_persona_name}, "
                f"outfit_style={outfit_style}, outfit_item={user_specified_outfit_item}, "
                f"main_type={schedule_main_type}, driver={core_event_driver}, adjustments={outfit_adjustments}"
            )

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
            "history_structure_summary": "",
            "recent_diaries": await self.collect_recent_diaries_text(normalized_persona_name, persona, effective_session_id),
            "recent_chats": await self.collect_recent_chat_text(event=event, persona=persona, session_id=effective_session_id),
            "daily_theme": "",
            "mood_color": "",
            "style_reference": style_reference,
            "schedule_variation_level": effective_variation,
            "schedule_variation_definition": self._variation_definition(effective_variation),
        }
        prompt_template = persona.get("prompt_template") or self.cfg.find_persona(normalized_persona_name).get("prompt_template")
        prompt = render_prompt(prompt_template, replacements)

        if extra_requirement:
            prompt += f"\n\n---\n【用户定制要求】\n{extra_requirement}\n\n请严格遵循以上要求生成日程。"
            if user_specified_outfit_style:
                prompt += f"\n- 穿搭风格：{outfit_style}"
            if user_specified_outfit_item:
                prompt += f"\n- 具体单品：{user_specified_outfit_item}（属于 {outfit_style} 风格）"
            if outfit_adjustments:
                prompt += f"\n- 穿搭调整：{outfit_adjustments}"

        prompt += build_format_priority_append_prompt(validate_persona := {
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "today_weather": today_weather,
            "user_specified_outfit_style": user_specified_outfit_style,
        })
        self._update_debug_payload({
            "persona_name": normalized_persona_name,
            "persona_desc_preview": (replacements["persona_desc"] or "")[:1200],
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "today_weather": today_weather,
            "variation_configured": configured_variation,
            "variation_effective": effective_variation,
            "variation_definition": self._variation_definition(effective_variation),
            "recent_chats_preview": replacements["recent_chats"][:500],
            "recent_diaries_preview": replacements["recent_diaries"][:500],
            "style_reference_preview": style_reference[:800],
            "style_research_payload": style_payload,
            "style_research_sources": style_sources[:3],
            "rendered_prompt_preview": prompt[:2500],
        })
        logger.info(
            f"[dayflow-debug] persona={normalized_persona_name} style={outfit_style} "
            f"main={schedule_main_type} driver={core_event_driver} variation={configured_variation}->{effective_variation} "
            f"desc_len={len(replacements['persona_desc'] or '')}, session={effective_session_id or 'none'}, target_date={date_str}"
        )

        configured_provider_id = persona.get("provider_id") or None
        session_provider_id = await self._resolve_session_provider_id(event)
        default_provider_id = await self._get_default_provider_id()

        primary_provider_id = configured_provider_id or session_provider_id or default_provider_id
        fallback_candidates = []
        if configured_provider_id:
            if session_provider_id and session_provider_id != configured_provider_id:
                fallback_candidates.append(session_provider_id)
            if default_provider_id and default_provider_id != configured_provider_id and default_provider_id != session_provider_id:
                fallback_candidates.append(default_provider_id)
        elif session_provider_id:
            if default_provider_id and default_provider_id != session_provider_id:
                fallback_candidates.append(default_provider_id)
        fallback_provider_id = fallback_candidates[0] if fallback_candidates else None

        repair_retries = int(persona.get("retry_count", 2) or 2) if auto_retry else 0
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
            summary = str(payload.get("summary") or "").strip()
            timeline_data = payload.get("timeline")
            if not outfit:
                return build_generation_error_data(normalized_persona_name, validate_persona, "JSON 缺少 outfit 字段")
            if not schedule and not timeline_data:
                return build_generation_error_data(normalized_persona_name, validate_persona, "JSON 缺少 schedule 和 timeline 字段")
            used_fallback = actual_provider_id != configured_provider_id and configured_provider_id is not None
            logger.info(
                f"[dayflow] llm json schedule generated for persona={normalized_persona_name}, "
                f"provider_used={actual_provider_id or 'session_default'}, configured_provider={configured_provider_id or 'none'}, "
                f"session_provider={session_provider_id or 'none'}, default_provider={default_provider_id or 'none'}, "
                f"fallback_used={used_fallback}, session={effective_session_id or 'none'}, target_date={date_str}"
            )
            return {
                "outfit": outfit,
                "schedule": schedule,
                "summary": summary,
                "meta": {
                    "style": parsed_style,
                    "schedule_main_type": schedule_main_type,
                    "core_event_driver": core_event_driver,
                    "persona_name": normalized_persona_name,
                    "date": date_str,
                    "provider_id": actual_provider_id or "",
                    "configured_provider_id": configured_provider_id or "",
                    "session_provider_id": session_provider_id or "",
                    "default_provider_id": default_provider_id or "",
                    "source_session_id": effective_session_id or "",
                    "weather": today_weather,
                    "prompt_template_version": "persona_full_template_v9_timeline_struct",
                    "fallback": used_fallback,
                    "variation_configured": configured_variation,
                    "variation_effective": effective_variation,
                    "style_reference": style_reference,
                },
                "timeline": timeline_data if isinstance(timeline_data, list) and timeline_data else extract_timeline(schedule),
                "weather": today_weather,
                "memo": "",
                "long_term_memory": [],
            }
        except Exception as e:
            logger.warning(
                f"[dayflow] llm generation failed for persona={normalized_persona_name}: {e}, "
                f"configured_provider={configured_provider_id or 'none'}, session_provider={session_provider_id or 'none'}, target_date={date_str}"
            )
            return build_generation_error_data(normalized_persona_name, validate_persona, f"LLM 调用失败: {e}")
