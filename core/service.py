import asyncio
import datetime
import json
import random
import threading
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image as ImageComponent, Plain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .config import DayflowConfig
from .constants import DEFAULT_VARIATION_LEVEL, STYLE_SUB_VARIANTS, SUB_VARIANT_NAME_TO_STYLE, VARIATION_LEVEL_DEFINITIONS
from .generator import (
    build_draft_skeleton,
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
    validate_sub_events,
)
from .schedule_renderer import ScheduleRenderer
from .store import DayflowStore
from .utils import GenerationContext, parse_hhmm_to_minutes


STYLE_RESEARCH_SYSTEM_PROMPT = """你是专业服饰设计师。你的任务是基于联网搜索结果，为另一个日程生成模型设计两套有审美高度的完整穿搭方案。

你的核心价值是**设计能力**，不是搜索能力。搜索结果提供风格知识基础，你产出的穿搭方案才是最终成品。

## 工作方式
1. 从搜索结果中理解该风格的核心特征、标志性单品和搭配逻辑
2. 在开始设计前，先构思设计语言三要素——这是高质量设计的基础：
   - 色彩哲学：该风格的核心色系与配色逻辑是什么？（如莫兰迪色系、高饱和撞色、同色系层次等）
   - 廓形语言：该风格的典型廓形、层次关系与比例规则是什么？（如A字、收腰蓬裙、落肩oversized等）
   - 材质情绪：该风格的标志性材质及其传达的情绪基调是什么？（如丝绸=优雅流动、皮革=硬朗力量、蕾丝=精致柔美等）
3. 基于上述设计语言，创作两套穿搭方案。优先选择该风格广为人知的经典搭配组合——经典搭配经过验证，不易踩雷；若经典搭配被防重复记录覆盖，则再自行设计有新意的方案。
4. 不要复述搜索结果原文，要转化为可直接用于穿搭生成的方案
5. 材质搭配是设计感的核心：每套穿搭应包含至少一组材质对比（光泽×哑光、柔软×硬挺、厚重×轻薄等），在单品描述中自然体现

## 输出格式
输出 JSON 对象，字段如下：
- definition: string，风格定义——须涵盖核心美学特征与标志性元素、核心搭配逻辑与设计原则。禁止场景适配性描述（如"适合XX场合""营造XX氛围"）
- morning_look: object，晨间第一套穿搭方案，包含：
  - pieces: string[]，5-8个单品。每个以穿着类别开头（内搭/外搭/外套/马甲/下装/连衣裙/袜子/腿饰/腰饰），后接单品名、材质与色名；版型与设计细节如有意义则附注。下装应为裙装。袜子为功能型腿部穿着（丝袜、棉袜等），腿饰为装饰型（腿套、腿环等），两者不宜同时出现。禁止实用性描述（如"透气舒适""方便活动""适合XX天气"）
  - accessories: string[]，2-5个配饰。鞋为必选项（完整造型的必要组成部分），其余从包/首饰/发饰/腰带等中自由选取。每个配饰为独立单品，禁止将多件合并为一条。每个须包含品名、材质和色名，可标注设计细节
  - hair_makeup: string，发型与妆容。分别描述发型和妆容：发型须包含具体名称及特征描述；妆容须包含具体妆面风格与至少一个特征（如唇色、眼妆特点），风格需要时可突破淡妆基调
- afternoon_look: object，午后第二套换装方案，结构同 morning_look
- difference: string[]，两套穿搭之间的审美维度差异，3-5条。每条应体现具体的审美维度差异（配色方向/廓形语言/材质情绪/风格倾向），禁止使用实用性理由
- weather: string | null，当查询中包含地点信息时，返回该地点今日真实天气，格式为"天气状况，温度范围，风力等细节"（如"多云转晴，18~26℃，东南风3级"）；无地点信息时为 null

## 洛丽塔风格特殊规范
当风格名包含"洛丽塔"时，必须额外遵守以下规则：
1. **自然语言命名**：所有单品名称必须使用通用自然语言，严禁出现圈子专属术语或款式代号，例如：
   - ✓"蝴蝶结发箍"     ✗"KC发饰"
   - ✓"印花图案"       ✗"柄图"
   - ✓"连衣裙"         ✗"JSK""OP"
   - ✓"过膝长袜"       ✗"OTK"
   - ✓"高腰连衣裙"     ✗"高腰JSK"
   - ✓"加厚裙撑"       ✗"暴力撑"
   - ✓"一体式连衣裙"   ✗"OP连衣裙"
   - ✓"背心式连衣裙"   ✗"JSK连衣裙"
2. **差异来自经典款本身**：两套穿搭的差异必须来自两款不同经典搭配本身的风格差异（如甜美系洛丽塔 vs 古典系洛丽塔的配色与廓形差异），而非同一搭配的微小变体

## 两套穿搭的要求
1. 同一风格体系，但从配色方向、廓形语言、材质情绪、风格倾向等多维度呈现明显视觉差异——不是换件上衣就完事
2. 两套都须是从头到脚、从里到外、从服装到配饰到发妆的完整造型
3. 混合风格须在每套中体现主体与辅助元素的主辅关系
4. 材质搭配应在单品选择的组合层面体现设计意图（如缎面吊带×针织开衫的光泽×哑光对比），而非事后凑搭

## 设计自查
完成两套穿搭设计后，从以下四个维度逐个审视每套方案，发现不足则调整后再输出：
1. 层次：是否有至少2个穿搭层次（如内搭→外搭→外套）？最外层单品是否形成了清晰的视觉轮廓？
2. 焦点：是否有一个明确的视觉焦点（一条特别的半裙、一件精致的短外套、一组配饰等）？是否有多余单品在争夺注意力？
3. 配色：主色是否控制在2-3个以内？点缀色是否起到点睛而非杂乱的作用？
4. 材质：是否形成了一组有意义的材质对比（光泽×哑光、柔软×硬挺等）？对比是否破坏了风格统一？

规则：
1. 必须输出 JSON 对象，不要输出 Markdown 代码块，不要额外解释
2. 结果要去重、压缩、边界清晰
3. 用中文输出
4. 你是设计师，不是搜索结果的搬运工。如果搜索结果中的搭配不够美，请基于审美判断做出更好的选择
5. 禁止在任何字段中出现体型修正性语言（"显瘦""修饰XX部位""拉长腿部"等）。设计应基于风格美学（配色、廓形、材质、比例），而非体型修正逻辑。风格层面的设计原则（如"该风格强调高腰线"）不在禁止之列"""

STYLE_RESEARCH_QUERY_TEMPLATE = """「{style_name}」穿搭风格 经典搭配范例 单品要点 色彩方案 材质特点 常见误区"""

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
   - 优先使用池中相近值（如"洛丽塔"→"甜系洛丽塔"，"杏花微雨"→"甜系洛丽塔"）
   - 池中确实无匹配 → 用用户原文
   - 用户只是调整当前风格（如"穿暴露点"）→ null，调整写到 outfit_adjustments
   - 与穿搭无关 → null

2. **outfit_item**（string | null）
   - 用户指定了具体单品或经典款式名（如"杏花微雨""小白云"）→ 填写名称
   - 用户只说了风格大类（如"洛丽塔"）→ null
   - 与穿搭无关 → null

3. **outfit_item_period**（string | null）
   - 仅当 outfit_item 非 null 时填写
   - "morning"：用户明确只指定了晨间（如"早上穿小白云"）
   - "afternoon"：用户明确只指定了午后（如"下午穿小白云"）
   - "all_day"：用户指定全天穿同一款（如"一天都穿小白云""整天穿小白云"）
   - "both"：用户分别指定了晨间和午后的不同款式（如"早上穿小白云下午穿小黑云"）
   - 未明确时段 → null（默认按晨间处理）

4. **outfit_item_afternoon**（string | null）
   - 仅当 outfit_item_period 为 "both" 时填写午后款式名
   - 其他情况 → null

5. **schedule_main_type**（string | null）
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
- outfit_item_period 决定了指定款式的穿着时段，影响晨间/午后的穿搭分配
"""

SUBDIVISION_SYSTEM_PROMPT = """你是日程细分编辑。将以下日程的每个时段拆分为更细粒度的活动片段。

## 角色
{persona_name}：{persona_desc}

## 今日条件
{weekday} | {today_weather} | 风格：{outfit_style}

## 风格参考
{style_reference}

## 日程大骨架
{draft_skeleton}

## 核心原则

细分不是对大时段的机械拆解，而是在大框架下的创造性丰富。
大时段的detail是叙事高光，细分要还原出真实的生活节奏——
那些大时段来不及写的、被叙事省略的、角色自然会做的种种小事，
都是细分的素材。细分的价值在于让日程从"故事梗概"变成"真实的一天"。

## 要求

### 拆分规则
1. 每个时段拆为2-4个小事件，每个至少10分钟
2. 小事件总时长必须等于大时段时长，不得溢出或留空
3. 拆分应体现活动的自然阶段——准备、执行、过渡、收尾
4. 换装类时段最多拆3步，避免每次都走相同的固定步骤序列
5. 子事件时长要符合常理——短暂动作和长时活动应有合理的时长区分，不要让单一小活动占据整个大时段的大部分时间

### 内容规则
4. 每个小事件只需简短标题描述活动类型（10字内）
5. detail可选且不超过20字，只补充活动内容，禁止写感受、情绪、感官体验
6. 大时段detail中明确提到的活动必须在细分中体现；detail未提及但角色在此场景下自然会做的事，也应补充
7. 细分应比大时段更贴近真实生活节奏——真人不会从护肤直接跳到入睡，中间总有过渡、停顿、小习惯、小岔路

### 输出
输出JSON对象，包含一个sub_events数组。数组中每个元素对应一个大时段的细分结果：

{{"sub_events": [{{"source_index": 0, "items": [{{"time_start": "08:30", "time_end": "08:42", "title": "活动标题", "detail": "简短补充"}}]}}]}}

字段说明：
- source_index：整数，从0开始，对应大骨架timeline数组的下标，每个大时段都必须有对应的细分
- items：该大时段的细分活动数组
- time_start/time_end：字符串，格式"HH:MM"
- title：字符串，10字以内
- detail：字符串，20字以内，可为空字符串

只输出JSON，不要其他内容。"""


class DayflowService:
    PLUGIN_NAME = "astrbot_plugin_dayflow_life_scheduler"
    GROK_PLUGIN_NAME = "astrbot_plugin_grok_web_search_Inoryu7z"
    LLM_RETRY_DELAY_SECONDS = 2.0
    STYLE_RESEARCH_CACHE_DAYS = 1
    STYLE_RESEARCH_MAX_CHARS = 2500

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
        self._schedule_renderer = ScheduleRenderer(self.data_dir)
        self._render_lock = asyncio.Lock()
        self._llm_concurrency_sem = asyncio.Semaphore(4)

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

    def _llm_timeout_seconds(self) -> int:
        try:
            value = int(self.config.get("llm_timeout_seconds", 900) or 0)
            return max(0, value)
        except Exception:
            return 900

    def _final_fallback_provider_id(self) -> str | None:
        value = str(self.config.get("final_fallback_provider") or "").strip()
        return value or None

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
            logger.info(f"[dayflow] 复用冻结随机值: key={cache_key}, values={self._frozen_randoms[cache_key]}")
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
        logger.info(f"[dayflow] 冻结随机值: key={cache_key}, values={frozen}")
        return frozen

    def clear_frozen_randoms(self, store_key: str, target_date: str):
        cache_key = self._frozen_randoms_key(store_key, target_date)
        if cache_key in self._frozen_randoms:
            logger.info(f"[dayflow] 已清除冻结随机值: key={cache_key}")
            del self._frozen_randoms[cache_key]

    def _cleanup_stale_caches(self):
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        stale_keys = [k for k in self._frozen_randoms if not k.endswith(today_str)]
        for k in stale_keys:
            del self._frozen_randoms[k]
        if stale_keys:
            logger.info(f"[dayflow] 已清理 {len(stale_keys)} 条过期 frozen_randoms 条目")

        cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
        stale_sessions = []
        for sid, iso in self._last_interaction_times.items():
            try:
                last_dt = datetime.datetime.fromisoformat(iso)
                if last_dt < cutoff:
                    stale_sessions.append(sid)
            except Exception:
                stale_sessions.append(sid)
        for sid in stale_sessions:
            del self._last_interaction_times[sid]
        if stale_sessions:
            logger.info(f"[dayflow] 已清理 {len(stale_sessions)} 条过期交互时间条目")

    def _load_style_research_cache(self):
        try:
            if self._style_cache_path.exists():
                data = json.loads(self._style_cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._style_research_cache = data
        except Exception as e:
            logger.warning(f"[dayflow] 加载风格研究缓存失败: {e}")
            self._style_research_cache = {}

    def _save_style_research_cache(self):
        try:
            self._style_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._style_cache_path.write_text(
                json.dumps(self._style_research_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[dayflow] 保存风格研究缓存失败: {e}")

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

    def _clip_list(self, value: Any, max_items: int = 8, max_chars: int = 150) -> list[str]:
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
        difference = self._clip_list(payload.get("difference"), max_items=5)
        lines = [f"风格名：{style_name}"]
        if definition:
            lines.append(f"风格定义：{definition}")

        for label, key in [("晨间第一套", "morning_look"), ("午后第二套", "afternoon_look")]:
            look = payload.get(key)
            if isinstance(look, dict):
                pieces = self._clip_list(look.get("pieces"), max_items=8, max_chars=150)
                accessories = self._clip_list(look.get("accessories"), max_items=5, max_chars=150)
                hair_makeup = str(look.get("hair_makeup") or "").strip()
                if pieces:
                    lines.append(f"{label}单品：")
                    lines.extend(f"- {item}" for item in pieces)
                if accessories:
                    lines.append(f"{label}配饰：")
                    lines.extend(f"- {item}" for item in accessories)
                if hair_makeup:
                    lines.append(f"{label}发妆：{hair_makeup}")
            elif isinstance(look, list):
                clipped = self._clip_list(look, max_items=8, max_chars=150)
                if clipped:
                    lines.append(f"{label}建议：")
                    lines.extend(f"- {item}" for item in clipped)
        if difference:
            lines.append("两套穿搭之间的关键差异：")
            lines.extend(f"- {item}" for item in difference)

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

    def _find_plugin_by_name(self, plugin_name: str, validator=None):
        try:
            stars = self.context.get_all_stars()
        except Exception as e:
            logger.debug(f"[dayflow] get_all_stars 失败（查找 {plugin_name}）: {e}")
            stars = []
        for meta in stars or []:
            meta_name = str(getattr(meta, "name", "") or "").strip()
            root_dir_name = str(getattr(meta, "root_dir_name", "") or "").strip()
            module_path = str(getattr(meta, "module_path", "") or "").strip()
            if meta_name != plugin_name and root_dir_name != plugin_name and plugin_name not in module_path:
                continue
            for attr in ("star", "instance", "plugin", "obj", "star_cls"):
                candidate = getattr(meta, attr, None)
                if candidate is not None:
                    if validator is None or validator(candidate):
                        return candidate
        for star in self._iter_loaded_stars():
            cls_module = getattr(star.__class__, "__module__", "")
            if plugin_name in cls_module:
                if validator is None or validator(star):
                    return star
        return None

    def _find_grok_plugin(self):
        return self._find_plugin_by_name(
            self.GROK_PLUGIN_NAME,
            validator=lambda obj: hasattr(obj, "_do_search"),
        )

    def _select_sub_variants(self, style_name: str, specified_names: list[str] | None = None) -> list[dict] | None:
        variants = STYLE_SUB_VARIANTS.get(style_name)
        if not variants:
            return None
        specified_names = [n for n in (specified_names or []) if n]
        specified_variants = [v for v in variants if v.get("name") in specified_names]
        if len(specified_variants) >= 2:
            logger.info(f"[dayflow-子款式] 用户指定: style={style_name}, variants={[v['name'] for v in specified_variants[:2]]}")
            return specified_variants[:2]
        used_names = set()
        cache_key = self._normalize_style_key(style_name)
        usage_history = self._style_research_cache.get("_sub_variants_usage", {})
        if not isinstance(usage_history, dict):
            usage_history = {}
        style_history = usage_history.get(cache_key, [])
        if isinstance(style_history, list):
            for entry in style_history[-3:]:
                if isinstance(entry, dict):
                    names = entry.get("names", [])
                    if isinstance(names, list):
                        used_names.update(str(n) for n in names)
        for sv in specified_variants:
            used_names.discard(sv.get("name", ""))
        available = [v for v in variants if v.get("name") not in used_names and v not in specified_variants]
        if len(specified_variants) == 1:
            need = 1
            if len(available) < need:
                earliest_used = []
                for entry in style_history:
                    if isinstance(entry, dict):
                        for n in entry.get("names", []):
                            for v in variants:
                                if v.get("name") == str(n) and v not in available and v not in specified_variants and v not in earliest_used:
                                    earliest_used.append(v)
                                    if len(available) + len(earliest_used) >= need:
                                        break
                            if len(available) + len(earliest_used) >= need:
                                break
                        if len(available) + len(earliest_used) >= need:
                            break
                available.extend(earliest_used)
            if not available:
                return None
            selected = specified_variants + random.sample(available, 1)
        else:
            if len(available) < 2:
                earliest_used = []
                for entry in style_history:
                    if isinstance(entry, dict):
                        for n in entry.get("names", []):
                            for v in variants:
                                if v.get("name") == str(n) and v not in available and v not in earliest_used:
                                    earliest_used.append(v)
                                    if len(available) + len(earliest_used) >= 2:
                                        break
                            if len(available) + len(earliest_used) >= 2:
                                break
                        if len(available) + len(earliest_used) >= 2:
                            break
                available.extend(earliest_used)
            if len(available) < 2:
                return None
            selected = random.sample(available, 2)
        logger.info(f"[dayflow-子款式] 选中: style={style_name}, variants={[v['name'] for v in selected]}, specified={[v['name'] for v in specified_variants]}, excluded={used_names}")
        return selected

    def _record_sub_variants_usage(self, style_name: str, variant_names: list[str]):
        cache_key = self._normalize_style_key(style_name)
        usage_history = self._style_research_cache.get("_sub_variants_usage", {})
        if not isinstance(usage_history, dict):
            usage_history = {}
        style_history = usage_history.setdefault(cache_key, [])
        style_history.append({"names": list(variant_names), "date": datetime.datetime.now().isoformat()})
        style_history = style_history[-10:]
        usage_history[cache_key] = style_history
        self._style_research_cache["_sub_variants_usage"] = usage_history
        self._save_style_research_cache()

    def _build_sub_variants_append(self, style_name: str, variants: list[dict], all_day: bool = False) -> str:
        if not variants or len(variants) < 1:
            return ""
        lines = [
            "",
            "## 推荐子款式（真实经典款）",
        ]
        if all_day and len(variants) == 1:
            lines.append("以下款式为该风格下真实存在的经典搭配，经过验证。用户要求全天穿着此款，晨间和午后穿搭方案均基于此款设计，通过配饰和细节变化区分早晚。")
        else:
            lines.append("以下两款均为该风格下真实存在的经典搭配，经过验证。请分别作为晨间和午后穿搭方案的设计基础，搜索其详细信息并据此设计。")
        lines.append("切忌望文生义——款式名仅为标识，具体设计须基于搜索结果与以下描述。子款式池并不全面，你可以在保持核心特征的基础上做合理调整。")
        lines.append("")
        if all_day and len(variants) == 1:
            v = variants[0]
            name = v.get("name", "")
            desc = v.get("description", "")
            lines.append(f"### {name}（全天）")
            lines.append(desc)
            lines.append("")
        else:
            labels = ["（晨间）", "（午后）"]
            for i, variant in enumerate(variants[:2]):
                name = variant.get("name", "")
                desc = variant.get("description", "")
                lines.append(f"### {name} {labels[i]}")
                lines.append(desc)
                lines.append("")
        return "\n".join(lines)

    def _build_style_anti_repetition_append(self, persona_name: str | None, style_name: str) -> str:
        if not persona_name:
            return ""
        recent_outfits = self.store.collect_recent_style_outfits(persona_name, style_name, max_occurrences=3)
        if not recent_outfits:
            return ""
        lines = [
            "",
            "## 近期同风格穿搭记录（防重复）",
            f"以下是该人格近期穿过「{style_name}」风格的穿搭记录，你本次产出的穿搭方案必须与这些记录做出明显差异——",
            "在配色方向、廓形语言、材质情绪、风格倾向等审美维度上均需不同（如配色从暖调转冷调、廓形从宽松转修身），确保每次同风格穿搭各有辨识度。",
            "",
        ]
        for record in recent_outfits:
            date_str = record.get("date", "")
            lines.append(f"### {date_str} 的穿搭")
            morning = record.get("morning_outfit", "")
            if morning:
                lines.append(f"晨间穿搭：{morning}")
            for i, afternoon in enumerate(record.get("afternoon_outfits", []), 1):
                lines.append(f"午后换装{i}：{afternoon}")
            lines.append("")
        return "\n".join(lines)

    def _build_style_research_query(self, style_name: str, location: str | None = None) -> str:
        template = self._style_research_query_template()
        try:
            query = template.format(style_name=style_name)
        except Exception as e:
            logger.warning(f"[dayflow] 风格研究查询模板无效，回退到默认: {e}")
            query = STYLE_RESEARCH_QUERY_TEMPLATE.format(style_name=style_name)
        if location and str(location).strip():
            query += f" | 同时查询{str(location).strip()}今日真实天气"
        return query

    async def _research_style_reference(self, style_name: str, extra_requirement: str | None = None, pool_options: dict | None = None, location: str | None = None, persona_name: str | None = None, specified_sub_variant_names: list[str] | None = None, sub_variant_all_day: bool = False) -> tuple[str, dict[str, Any], list[dict[str, str]], dict[str, Any] | None, str | None]:
        style_name = str(style_name or "").strip()
        if not style_name:
            return "", {}, [], None, None
        cache_key = self._normalize_style_key(style_name)
        anti_repetition_append = self._build_style_anti_repetition_append(persona_name, style_name)
        has_anti_repetition = bool(anti_repetition_append.strip())
        cached = self._style_research_cache.get(cache_key)
        if self._is_style_cache_entry_valid(cached) and not has_anti_repetition:
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
            logger.info(f"[dayflow-风格研究] 缓存命中 | style={style_name} | weather={cached_weather}")
            return summary, payload, sources, None, cached_weather

        grok = self._find_grok_plugin()
        if grok is None:
            logger.warning(f"[dayflow] 未找到 grok 插件，跳过风格研究: style={style_name}")
            return "", {}, [], None, None

        query = self._build_style_research_query(style_name, location=location)
        system_prompt = self._style_research_system_prompt()

        if has_anti_repetition:
            system_prompt += anti_repetition_append
            logger.info(f"[dayflow-风格研究] 防重复追加 | persona={persona_name} | style={style_name} | history_count={len(self.store.collect_recent_style_outfits(persona_name, style_name, max_occurrences=3))}")

        selected_sub_variants = self._select_sub_variants(style_name, specified_names=specified_sub_variant_names)
        if selected_sub_variants:
            sub_variants_append = self._build_sub_variants_append(style_name, selected_sub_variants, all_day=sub_variant_all_day)
            if sub_variants_append:
                system_prompt += sub_variants_append
                logger.info(f"[dayflow-风格研究] 子款式追加 | style={style_name} | variants={[v['name'] for v in selected_sub_variants]}")

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
                logger.warning(f"[dayflow] 自定义日程意图追加格式化失败: {e}")

        logger.info(f"[dayflow-风格研究] 查询 | style={style_name} | location={location} | query={query}")
        result = await grok._do_search(query=query, system_prompt=system_prompt, use_retry=True, prefer_quality=True)

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
                logger.warning(f"[dayflow] 风格研究搜索失败但原始内容可用，降级处理: style={style_name}, error={last_reason}")
            else:
                logger.warning(f"[dayflow] 风格研究搜索失败: style={style_name}, reason={last_reason}")
        else:
            raw_text = str(result.get("content") or "").strip()
            logger.info(f"[dayflow-风格研究] 原始响应 | style={style_name} | content={self._preview_text(raw_text, limit=1600)}")
            parsed = safe_json_loads(raw_text)
            if isinstance(parsed, dict) and parsed.get("definition"):
                parsed_payload = parsed
            else:
                last_reason = "研究结果不是有效 JSON"
                logger.warning(f"[dayflow] 风格研究解析失败: style={style_name}, reason={last_reason}")

        if parsed_payload:
            if extra_requirement and isinstance(parsed_payload.get("intent_overrides"), dict):
                intent_overrides = parsed_payload.pop("intent_overrides")
                logger.info(f"[dayflow-风格研究] intent_overrides | style={style_name} | overrides={json.dumps(intent_overrides, ensure_ascii=False)}")
            weather_value = parsed_payload.pop("weather", None)
            if isinstance(weather_value, str) and weather_value.strip():
                real_weather = weather_value.strip()
                logger.info(f"[dayflow-风格研究] 天气 | style={style_name} | location={location} | weather={real_weather}")
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
            if selected_sub_variants:
                self._record_sub_variants_usage(style_name, [v.get("name", "") for v in selected_sub_variants])
            else:
                self._save_style_research_cache()
            payload_preview = json.dumps(parsed_payload, ensure_ascii=False, indent=2)
            logger.info(f"[dayflow-风格研究] 解析结果 | style={style_name} | payload={self._preview_text(payload_preview, limit=1600)}")
            logger.info(f"[dayflow-风格研究] 来源 | style={style_name} | sources={self._render_sources_preview(sources)}")
            self._update_debug_payload({
                "style_research_cache_hit": False,
                "style_research_query_preview": self._preview_text(query, limit=1200),
                "style_research_system_prompt_preview": self._preview_text(system_prompt, limit=1200),
                "style_research_raw_response_preview": self._preview_text(raw_text, limit=1600),
                "style_research_payload_preview": self._preview_text(payload_preview, limit=1600),
                "style_research_sources_preview": self._render_sources_preview(sources),
                "style_research_weather": real_weather,
            })
            logger.info(f"[dayflow] 风格研究成功(json): style={style_name}, sources={len(sources)}, weather={real_weather}")
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
            logger.info(f"[dayflow-风格研究] 回退纯文本 | style={style_name} | summary={self._preview_text(summary, limit=1600)}")
            logger.info(f"[dayflow-风格研究] 来源 | style={style_name} | sources={self._render_sources_preview(sources)}")
            logger.warning(f"[dayflow] 风格研究降级为纯文本: style={style_name}, reason={last_reason or '非 JSON 输出'}")
            return summary, {}, sources, intent_overrides, None

        if last_reason:
            logger.warning(f"[dayflow] 风格研究不可用: style={style_name}, reason={last_reason}")
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
        logger.info("[dayflow] 插件已初始化")
        logger.info(f"[dayflow] 已加载人格: {[p.get('name') for p in self.cfg.personas()]}")
        await self.start_scheduler()

    async def terminate(self):
        await self.stop_scheduler()
        self.store.prune_expired(force=True)
        await self.store.async_save_state()
        self._save_style_research_cache()
        logger.info("[dayflow] 已终止")

    async def start_scheduler(self):
        if self._scheduler_running:
            return
        self._scheduler_running = True
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("[dayflow] 自动调度器已启动")

    async def stop_scheduler(self):
        self._scheduler_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
            self.scheduler_task = None
        logger.info("[dayflow] 自动调度器已停止")

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
                logger.warning(f"[dayflow] 调度循环错误: {e}")
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
                    f"[dayflow] 自动生成重试已达上限，今日停止重试: "
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
                    enable_subdivision = bool(persona.get("enable_subdivision", False))
                    if enable_subdivision:
                        sub_events = await self._generate_subdivision(
                            result=data,
                            persona_name=configured_persona_name,
                            persona_desc=persona_ctx.get("persona_desc") or "",
                            persona=persona,
                        )
                        if sub_events:
                            data["sub_events"] = sub_events
                            await self.save_generated(store_key, data)
                    await self.push_schedule_to_targets(store_key, data)
                    self.store.clear_auto_generation_failure(store_key, trigger_key)
                    self.store.mark_auto_generation_consumed(store_key, trigger_key)
                    logger.info(
                        f"[dayflow] 已自动生成日程: persona={configured_persona_name}, "
                        f"store_key={store_key}, trigger={trigger_key}, session={auto_session_id or 'none'}"
                    )
                else:
                    failure_count = self.store.record_auto_generation_failure(store_key, trigger_key)
                    if failure_count >= auto_retry_limit + 1:
                        self.store.mark_auto_generation_consumed(store_key, trigger_key)
                        logger.warning(
                            f"[dayflow] 自动生成失败且已达重试上限，今日停止重试: "
                            f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}, limit={auto_retry_limit}, reason={data.get('memo', '')}"
                        )
                    else:
                        logger.warning(
                            f"[dayflow] 自动生成失败，稍后重试: "
                            f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}/{auto_retry_limit + 1}, reason={data.get('memo', '')}"
                        )
            except Exception as e:
                failure_count = self.store.record_auto_generation_failure(store_key, trigger_key)
                if failure_count >= auto_retry_limit + 1:
                    self.store.mark_auto_generation_consumed(store_key, trigger_key)
                    logger.warning(
                        f"[dayflow] 自动生成异常且已达重试上限，今日停止重试: "
                        f"persona={configured_persona_name}, trigger={trigger_key}, failures={failure_count}, limit={auto_retry_limit}, error={e}"
                    )
                else:
                    logger.warning(
                        f"[dayflow] 自动生成失败: persona={configured_persona_name}: {e}, "
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
                logger.warning(f"[dayflow] 读取会话人格失败: {e}")

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
                logger.warning(f"[dayflow] 通过绑定 persona_id 获取人格失败: {e}")

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
                logger.warning(f"[dayflow] 获取默认人格 v3 失败: {e}")

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
                f"[dayflow] 人格上下文回退到仅名称: persona={result['persona_name']}, "
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
                f"[dayflow] get_life_context 拒绝未配置人格: session={session_id or ''}, "
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
        if len(candidate_keys) > 1:
            key_status = []
            for ck in candidate_keys:
                s = self.store.get_schedule_for_date(ck, effective_date)
                key_status.append(f"{ck}={'hit' if s and not s.get('meta', {}).get('error') else 'miss'}")
            logger.info(
                f"[dayflow] get_life_context 多键查找: session={session_id or ''}, "
                f"requested_persona={persona_name or ''}, candidate_keys={candidate_keys}, "
                f"key_status=[{', '.join(key_status)}], date={effective_date}"
            )

        for store_key in candidate_keys:
            exact_schedule = self.store.get_schedule_for_date(store_key, effective_date)
            if exact_schedule and not exact_schedule.get("meta", {}).get("error"):
                logger.info(
                    f"[dayflow] get_life_context 命中目标日程: session={session_id or ''}, "
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
        logger.info(
            f"[dayflow] get_life_context 缺少目标日程: session={session_id or ''}, "
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
            logger.warning(f"[dayflow] 跳过保存错误结果: persona={requested_store_key}")
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
                    f"[dayflow] 生成的人格键不匹配，使用调用方键: "
                    f"requested={requested_store_key}, meta={meta_persona_name}, normalized_meta={meta_store_key}"
                )
        store_key = requested_store_key
        meta["persona_name"] = store_key
        data["meta"] = meta
        await self.store.save_schedule(store_key, data)
        logger.info(
            f"[dayflow] 日程已保存: store_key={store_key}, date={saved_date}, "
            f"history_count={self.store.get_history_count(store_key)}, "
            f"memory_current_date={self.store.get_memory_date(store_key)}"
        )

    def _render_push_content(self, data: dict) -> str:
        return render_schedule_display(data)

    async def _render_push_image(self, data: dict, persona_name: str) -> bytes | None:
        date_str = str((data.get("meta") or {}).get("date") or "").strip()
        async with self._render_lock:
            return await asyncio.to_thread(
                self._schedule_renderer.render, data, date_str, persona_name
            )

    async def push_schedule_to_targets(self, persona_name: str, data: dict, exclude_umo: str | None = None):
        if not is_schedule_valid(data):
            return
        persona = self.get_persona_config(persona_name) or self.cfg.find_persona(persona_name)
        if not persona:
            return
        push_targets = persona.get("push_targets") or []
        if not push_targets:
            return

        push_image_enabled = bool(persona.get("push_image_enabled", False))
        image_bytes: bytes | None = None
        text_content: str | None = None

        if push_image_enabled:
            image_bytes = await self._render_push_image(data, persona_name)
            if image_bytes is None:
                logger.warning(f"[dayflow] 图片渲染失败: persona={persona_name}，回退到纯文本")
                text_content = self._render_push_content(data)
        else:
            text_content = self._render_push_content(data)

        for target_umo in push_targets:
            target_umo = str(target_umo or "").strip()
            if not target_umo:
                continue
            if exclude_umo and target_umo == exclude_umo:
                continue
            try:
                if image_bytes is not None:
                    chain = MessageChain([ImageComponent.fromBytes(image_bytes)])
                elif text_content is not None:
                    chain = MessageChain([Plain(text_content)])
                else:
                    continue
                await self.context.send_message(target_umo, chain)
                logger.info(f"[dayflow] 日程已推送到 {target_umo}: persona={persona_name} (image={image_bytes is not None})")
            except Exception as e:
                logger.warning(f"[dayflow] 推送到 {target_umo} 失败: persona={persona_name}: {e}")

    def describe_personas(self) -> list[str]:
        retention = self._schedule_retention_days()
        retention_text = "无限制" if retention == -1 else f"{retention}天"
        lines = []
        for item in self.cfg.personas():
            racing = item.get("select_providers") or []
            if len(racing) > 1:
                provider_text = f"竞速:{'+'.join(racing)}"
            elif len(racing) == 1:
                provider_text = racing[0]
            else:
                provider_text = "current_provider"
            lines.append(
                f"- {item['name']} @ {item.get('generate_time', '07:00')} ({provider_text} -> 最终兜底) | 重试:{item.get('retry_count', 2)} | 变化:{item.get('schedule_variation_level', DEFAULT_VARIATION_LEVEL)} | 持久化:{retention_text} | 推送:{len(item.get('push_targets') or [])}个目标"
            )
        return lines

    def _iter_loaded_stars(self):
        try:
            stars = self.context.get_all_stars()
        except Exception as e:
            logger.debug(f"[dayflow] get_all_stars 失败: {e}")
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
        return self._find_plugin_by_name(
            "astrbot_plugin_daymind",
            validator=self._is_valid_daymind_instance,
        )

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
            logger.warning(f"[dayflow] 解析人格最近会话失败: {e}")
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
                    logger.warning(f"[dayflow] 读取 DayMind 近期消息失败: {e}")
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
            logger.warning(f"[dayflow] 读取 DayMind 日记失败: {e}")
            return f"（读取 DayMind 近日日记失败：{e}）"

    async def call_llm_once(self, prompt: str, provider_id: str | None) -> str:
        effective_id = provider_id
        if not effective_id:
            effective_id = await self._get_default_provider_id()
        if not effective_id:
            raise RuntimeError("[dayflow] 无可用提供商")
        prov = self.context.get_provider_by_id(effective_id)
        original_client_timeout = None
        timeout_seconds = self._llm_timeout_seconds()
        if prov and timeout_seconds > 0:
            client = getattr(prov, "client", None)
            if client and hasattr(client, "timeout"):
                original_client_timeout = client.timeout
                client.timeout = timeout_seconds
        try:
            async with self._llm_concurrency_sem:
                llm_resp = await self.context.llm_generate(chat_provider_id=effective_id, prompt=prompt)
        finally:
            if original_client_timeout is not None:
                client = getattr(prov, "client", None) if prov else None
                if client and hasattr(client, "timeout"):
                    client.timeout = original_client_timeout
        text = (getattr(llm_resp, "completion_text", "") or "").strip()
        if not text and llm_resp is not None:
            chain = getattr(llm_resp, "result_chain", None)
            if chain:
                for comp in getattr(chain, "chain", []):
                    comp_text = getattr(comp, "text", None) or getattr(comp, "data", None)
                    if comp_text and isinstance(comp_text, str) and comp_text.strip():
                        text = comp_text.strip()
                        break
        return text

    @staticmethod
    def _summarize_error(error: Any) -> str:
        s = str(error)
        if "Error code: 524" in s:
            return "Error 524: origin_response_timeout (Cloudflare 120s 超时)"
        if "Error code: 529" in s:
            return "Error 529: site_overloaded (Cloudflare 站点过载)"
        if "Error code: 502" in s:
            return "Error 502: bad_gateway"
        if "Error code: 503" in s:
            return "Error 503: service_unavailable"
        if "Error code: 429" in s:
            return "Error 429: rate_limit_exceeded"
        if len(s) > 300:
            return s[:300].rstrip() + "…"
        return s

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
                        logger.info(f"[dayflow] LLM 调用重试成功: provider={provider_id or '会话默认'}, attempt={attempt}/{attempts}")
                    return text
                logger.warning(f"[dayflow] LLM 补全为空: provider={provider_id or '会话默认'}, attempt={attempt}/{attempts}")
            except Exception as e:
                last_error = e
                logger.warning(f"[dayflow] LLM 调用失败: provider={provider_id or '会话默认'}, attempt={attempt}/{attempts}, error={self._summarize_error(e)}")
            if attempt < attempts and self.LLM_RETRY_DELAY_SECONDS > 0:
                await asyncio.sleep(self.LLM_RETRY_DELAY_SECONDS)
        if last_error is not None:
            raise last_error
        return last_text

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
            logger.debug(f"[dayflow] 获取默认提供商 ID 失败: {e}")
        return None

    async def _try_final_fallback(
        self,
        prompt: str,
        ctx: GenerationContext,
    ) -> dict | None:
        final_provider_id = self._final_fallback_provider_id()
        if not final_provider_id:
            return None
        logger.info(
            f"[dayflow] 尝试最终回退: persona={ctx.normalized_persona_name}, "
            f"最终提供商={final_provider_id}, 最大修复重试={ctx.max_repair_retries}"
        )
        try:
            final_prompt = prompt
            if ctx.best_partial and ctx.best_partial.get("raw_text") and ctx.best_partial.get("reason"):
                final_prompt = build_repair_prompt(
                    prompt, ctx.best_partial["raw_text"], ctx.best_partial["reason"],
                    ctx.validate_persona, retry_index=1,
                )
                logger.info(
                    f"[dayflow] 最终回退使用修复提示词: "
                    f"来源提供商={ctx.best_partial.get('provider_id')}, 原因={ctx.best_partial.get('reason')}"
                )
            raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                final_prompt, final_provider_id, None, retry_count=0,
            )
            payload = normalize_payload(safe_json_loads(raw_text), ctx.validate_persona)
            ok, reason = validate_payload(payload, ctx.validate_persona)
            for attempt in range(1, ctx.max_repair_retries + 1):
                if ok and payload:
                    break
                logger.warning(
                    f"[dayflow] 最终回退校验失败: persona={ctx.normalized_persona_name}, "
                    f"提供商={final_provider_id}, 第{attempt}/{ctx.max_repair_retries}次, 原因={reason}"
                )
                repair_prompt = build_repair_prompt(final_prompt, raw_text, reason, ctx.validate_persona, retry_index=attempt)
                raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                    repair_prompt, final_provider_id, None, retry_count=0,
                )
                payload = normalize_payload(safe_json_loads(raw_text), ctx.validate_persona)
                ok, reason = validate_payload(payload, ctx.validate_persona)
            if ok and payload:
                logger.info(
                    f"[dayflow] 最终回退成功: persona={ctx.normalized_persona_name}, "
                    f"提供商={actual_provider_id or final_provider_id}"
                )
                new_ctx = GenerationContext(
                    normalized_persona_name=ctx.normalized_persona_name,
                    outfit_style=ctx.outfit_style,
                    schedule_main_type=ctx.schedule_main_type,
                    core_event_driver=ctx.core_event_driver,
                    date_str=ctx.date_str,
                    actual_provider_id=actual_provider_id or final_provider_id,
                    configured_provider_id=ctx.configured_provider_id,
                    effective_session_id=ctx.effective_session_id,
                    today_weather=ctx.today_weather,
                    configured_variation=ctx.configured_variation,
                    effective_variation=ctx.effective_variation,
                    style_reference=ctx.style_reference,
                    validate_persona=ctx.validate_persona,
                )
                return self._build_schedule_result(payload=payload, ctx=new_ctx)
            logger.warning(
                f"[dayflow] 最终回退重试耗尽: persona={ctx.normalized_persona_name}, "
                f"提供商={final_provider_id}, 原因={reason}"
            )
        except Exception as e:
            logger.warning(
                f"[dayflow] 最终回退异常: persona={ctx.normalized_persona_name}, "
                f"提供商={final_provider_id}: {e}"
            )
        return None

    def _build_schedule_result(
        self,
        payload: dict,
        ctx: GenerationContext,
    ) -> dict:
        parsed_style = str(payload.get("outfit_style") or ctx.outfit_style).strip()
        outfit = str(payload.get("outfit") or "").strip()
        schedule = str(payload.get("schedule") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        timeline_data = payload.get("timeline")
        if not outfit:
            return build_generation_error_data(ctx.normalized_persona_name, ctx.validate_persona, "JSON 缺少 outfit 字段")
        if not schedule and not timeline_data:
            return build_generation_error_data(ctx.normalized_persona_name, ctx.validate_persona, "JSON 缺少 schedule 和 timeline 字段")
        used_fallback = (
            ctx.actual_provider_id != ctx.configured_provider_id
            and ctx.configured_provider_id is not None
            and ctx.actual_provider_id not in (ctx.racing_provider_ids or [])
        )
        logger.info(
            f"[dayflow] LLM JSON 日程已生成: persona={ctx.normalized_persona_name}, "
            f"使用提供商={ctx.actual_provider_id or '无'}, 配置提供商={ctx.configured_provider_id or '无'}, "
            f"是否回退={used_fallback}, 会话={ctx.effective_session_id or '无'}, 目标日期={ctx.date_str}"
        )
        return {
            "outfit": outfit,
            "schedule": schedule,
            "summary": summary,
            "meta": {
                "style": parsed_style,
                "schedule_main_type": ctx.schedule_main_type,
                "core_event_driver": ctx.core_event_driver,
                "persona_name": ctx.normalized_persona_name,
                "date": ctx.date_str,
                "provider_id": ctx.actual_provider_id or "",
                "configured_provider_id": ctx.configured_provider_id or "",
                "source_session_id": ctx.effective_session_id or "",
                "weather": ctx.today_weather,
                "prompt_template_version": "persona_full_template_v9_timeline_struct",
                "fallback": used_fallback,
                "variation_configured": ctx.configured_variation,
                "variation_effective": ctx.effective_variation,
                "style_reference": ctx.style_reference,
            },
            "timeline": timeline_data if isinstance(timeline_data, list) and timeline_data else extract_timeline(schedule),
            "weather": ctx.today_weather,
            "memo": "",
            "long_term_memory": [],
        }

    async def _generate_with_single_provider(
        self,
        prompt: str,
        provider_id: str | None,
        normalized_persona_name: str,
        validate_persona: dict,
        repair_retries: int,
    ) -> dict | None:
        last_raw_text = ""
        last_reason = ""
        try:
            raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                prompt, provider_id, None, retry_count=0,
            )
            last_raw_text = raw_text or ""
            payload = normalize_payload(safe_json_loads(raw_text), validate_persona)
            ok, reason = validate_payload(payload, validate_persona)
            last_reason = reason
            for attempt in range(1, repair_retries + 1):
                if ok and payload:
                    break
                logger.warning(
                    f"[dayflow] 竞速: 载荷校验失败: persona={normalized_persona_name}, "
                    f"provider={provider_id}, attempt={attempt}, reason={reason}"
                )
                repair_prompt = build_repair_prompt(prompt, raw_text, reason, validate_persona, retry_index=attempt)
                raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                    repair_prompt, provider_id, None, retry_count=0,
                )
                last_raw_text = raw_text or ""
                payload = normalize_payload(safe_json_loads(raw_text), validate_persona)
                ok, reason = validate_payload(payload, validate_persona)
                last_reason = reason
            if ok and payload:
                outfit = str(payload.get("outfit") or "").strip()
                schedule = str(payload.get("schedule") or "").strip()
                timeline_data = payload.get("timeline")
                if outfit and (schedule or timeline_data):
                    logger.info(
                        f"[dayflow] 竞速: provider={actual_provider_id or provider_id} 胜出: persona={normalized_persona_name}"
                    )
                    return {"payload": payload, "provider_id": actual_provider_id or provider_id}
            logger.warning(
                f"[dayflow] 竞速: provider={provider_id} 校验失败: persona={normalized_persona_name}, reason={reason}"
            )
            return {"payload": None, "provider_id": provider_id, "raw_text": last_raw_text, "reason": last_reason}
        except Exception as e:
            logger.warning(f"[dayflow] 竞速: provider={provider_id} 异常: persona={normalized_persona_name}: {self._summarize_error(e)}")
            return {"payload": None, "provider_id": provider_id, "raw_text": last_raw_text, "reason": str(e)}

    async def _race_providers(
        self,
        prompt: str,
        providers: list[str],
        normalized_persona_name: str,
        validate_persona: dict,
        repair_retries: int,
    ) -> tuple[dict | None, dict | None]:
        if len(providers) == 1:
            result = await self._generate_with_single_provider(
                prompt, providers[0], normalized_persona_name, validate_persona, repair_retries,
            )
            if result and result.get("payload") is not None:
                return result, None
            return None, result

        tasks: dict[asyncio.Task, str] = {}
        for pid in providers:
            coro = self._generate_with_single_provider(
                prompt, pid, normalized_persona_name, validate_persona, repair_retries,
            )
            task = asyncio.create_task(coro)
            tasks[task] = pid

        pending: set[asyncio.Task] = set(tasks.keys())
        winner: dict | None = None
        best_partial: dict | None = None
        provider_results: dict[str, str] = {}
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    pid = tasks[task]
                    try:
                        result = task.result()
                    except Exception as exc:
                        result = None
                        provider_results[pid] = f"exception: {exc}"
                    if result is not None and result.get("payload") is not None:
                        winner = result
                        provider_results[pid] = "success"
                        break
                    if result is not None:
                        provider_results[pid] = f"failed: {result.get('reason', 'unknown')}"
                        if result.get("raw_text") and best_partial is None:
                            best_partial = result
                    elif pid not in provider_results:
                        provider_results[pid] = "no_result"
                if winner is not None:
                    break
        finally:
            for task in list(tasks.keys()):
                pid = tasks[task]
                if not task.done():
                    task.cancel()
                elif pid not in provider_results:
                    provider_results[pid] = "cancelled"
            for task in list(tasks.keys()):
                if not task.done():
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

        if winner is not None:
            winner_pid = winner["provider_id"]
            logger.info(
                f"[dayflow] 竞速: 胜出者={winner_pid}: persona={normalized_persona_name}"
            )
        else:
            summary_parts = []
            for pid, status in provider_results.items():
                short = status if len(status) <= 80 else status[:80].rstrip() + "…"
                summary_parts.append(f"{pid}={short}")
            logger.warning(
                f"[dayflow] 竞速: 所有提供商均失败: persona={normalized_persona_name}, "
                f"results=[{', '.join(summary_parts)}]"
            )

        self._update_debug_payload({
            "racing_providers": providers,
            "racing_results": provider_results,
            "racing_winner": winner["provider_id"] if winner else None,
            "racing_best_partial_provider": best_partial.get("provider_id") if best_partial else None,
            "racing_best_partial_reason": best_partial.get("reason") if best_partial else None,
        })

        return winner, best_partial

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
            f"[dayflow] LLM 提供商策略: primary={primary_provider_id or '会话默认'}, "
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
                    f"[dayflow] 主提供商耗尽重试后仍为空，切换到回退: provider={primary_provider_id or '会话默认'}"
                )
            except Exception as e:
                last_error = e
                primary_failed = True
                logger.warning(
                    f"[dayflow] 主提供商重试耗尽，切换到回退: "
                    f"provider={primary_provider_id or '会话默认'}, error={self._summarize_error(e)}"
                )

        if fallback_provider_id is not None and (primary_failed or not should_try_primary):
            try:
                text = await self.call_llm_with_retries(prompt, fallback_provider_id, retry_count=retry_count)
                if text:
                    logger.info(f"[dayflow] 回退提供商成功: provider={fallback_provider_id}")
                    return text, fallback_provider_id
                last_text = text
                logger.warning(f"[dayflow] 回退提供商耗尽重试后仍为空: provider={fallback_provider_id}")
            except Exception as e:
                last_error = e
                logger.warning(f"[dayflow] 回退提供商失败: provider={fallback_provider_id}, error={self._summarize_error(e)}")

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
                "enable_subdivision": False,
                "sub_events_preview": "",
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
                "enable_subdivision": bool(persona.get("enable_subdivision", False)),
                "sub_events_preview": last_payload.get("sub_events_preview", ""),
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
        style_reference, _, _, _, _ = await self._research_style_reference(outfit_style, persona_name=resolved_name)
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
            "enable_subdivision": bool(persona.get("enable_subdivision", False)),
            "sub_events_preview": self._build_sub_events_debug_preview(resolved_name),
        }

    def _build_sub_events_debug_preview(self, persona_name: str) -> str:
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            store_key = self.normalize_persona_key(persona_name)
            data = self.store.get_schedule_for_date(store_key, today)
            if not data or data.get("meta", {}).get("error"):
                return "(无今日日程)"
            sub_events = data.get("sub_events")
            if not isinstance(sub_events, list) or not sub_events:
                return "(未启用细分或细分未生成)"
            lines = []
            for entry in sub_events:
                if not isinstance(entry, dict):
                    continue
                si = entry.get("source_index")
                items = entry.get("items") or []
                timeline = data.get("timeline") or []
                parent_title = ""
                if isinstance(si, int) and 0 <= si < len(timeline):
                    parent_title = str(timeline[si].get("title") or "")
                lines.append(f"[{si}] {parent_title}")
                for item in items:
                    ts = str(item.get("time_start") or "")
                    te = str(item.get("time_end") or "")
                    t = str(item.get("title") or "")
                    d = str(item.get("detail") or "")
                    line = f"  {ts}-{te} {t}"
                    if d:
                        line += f" ({d})"
                    lines.append(line)
            return "\n".join(lines)
        except Exception as e:
            return f"(读取失败: {e})"

    async def _generate_subdivision(
        self,
        result: dict,
        persona_name: str,
        persona_desc: str,
        persona: dict,
        event=None,
    ) -> list | None:
        logger.info(f"[dayflow-细分] 开始生成细分: persona={persona_name}")
        timeline = result.get("timeline")
        if not isinstance(timeline, list) or not timeline:
            logger.warning(f"[dayflow-细分] 日程无 timeline，跳过细分: persona={persona_name}")
            return None

        draft_skeleton = build_draft_skeleton(timeline)
        if not draft_skeleton:
            logger.warning(f"[dayflow-细分] 骨架为空，跳过细分: persona={persona_name}")
            return None

        meta = result.get("meta") or {}
        outfit_style = str(meta.get("style") or "").strip()
        today_weather = str(meta.get("weather") or result.get("weather") or "").strip()
        style_reference = str(meta.get("style_reference") or "").strip()
        date_str = str(meta.get("date") or "").strip()
        weekday = ""
        if date_str:
            try:
                target_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][target_dt.weekday()]
            except Exception:
                weekday = ""

        prompt = SUBDIVISION_SYSTEM_PROMPT.format(
            persona_name=persona_name,
            persona_desc=persona_desc[:800] if persona_desc else f"人格：{persona_name}",
            weekday=weekday,
            today_weather=today_weather,
            outfit_style=outfit_style,
            style_reference=style_reference[:600] if style_reference else "",
            draft_skeleton=draft_skeleton,
        )

        try:
            select_providers = persona.get("select_providers") or []
            configured_provider_id = select_providers[0] if select_providers else None
            final_fallback_id = self._final_fallback_provider_id()
            logger.info(f"[dayflow-细分] 细分提供商配置: persona={persona_name}, 提供商数={len(select_providers)}, 最终回退={final_fallback_id or '无'}")

            if len(select_providers) <= 1:
                if not configured_provider_id:
                    logger.warning(f"[dayflow-细分] 未配置提供商: persona={persona_name}")
                    return None

                raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                    prompt, configured_provider_id, None, retry_count=0,
                )
                if raw_text:
                    logger.info(f"[dayflow-细分] 单提供商返回成功: provider={actual_provider_id}, persona={persona_name}, 长度={len(raw_text)}")
                else:
                    logger.warning(f"[dayflow-细分] 单提供商返回为空: provider={configured_provider_id}, persona={persona_name}")
            else:
                seen = set()
                deduped_providers = []
                for pid in select_providers:
                    if pid and pid not in seen:
                        seen.add(pid)
                        deduped_providers.append(pid)
                logger.info(f"[dayflow-细分] 多提供商竞速模式: persona={persona_name}, 提供商数={len(deduped_providers)}")

                async def _try_single_provider(pid: str) -> tuple[str, str | None] | None:
                    try:
                        text = await self.call_llm_once(prompt, pid)
                        if text:
                            parsed = safe_json_loads(text)
                            if isinstance(parsed, dict):
                                sub_events = parsed.get("sub_events")
                                if isinstance(sub_events, list):
                                    ok, reason = validate_sub_events(sub_events, timeline)
                                    if ok:
                                        return pid, sub_events
                        return None
                    except Exception as e:
                        logger.debug(f"[dayflow-细分] 竞速提供商 {pid} 失败: {e}")
                        return None

                tasks = {asyncio.create_task(_try_single_provider(pid)): pid for pid in deduped_providers}
                winner_sub_events = None
                best_raw_text = ""
                best_provider_id = None
                pending = set(tasks.keys())
                try:
                    while pending:
                        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            pid = tasks[task]
                            try:
                                task_result = task.result()
                            except Exception:
                                task_result = None
                            if task_result is not None:
                                winner_pid, winner_events = task_result
                                logger.info(f"[dayflow-细分] 竞速胜出: provider={winner_pid}, persona={persona_name}")
                                winner_sub_events = winner_events
                                break
                            else:
                                try:
                                    text = await self.call_llm_once(prompt, pid)
                                    if text and not best_raw_text:
                                        best_raw_text = text
                                        best_provider_id = pid
                                except Exception:
                                    pass
                        if winner_sub_events is not None:
                            break
                finally:
                    for task in list(tasks.keys()):
                        if not task.done():
                            task.cancel()
                    for task in list(tasks.keys()):
                        if not task.done():
                            try:
                                await task
                            except (asyncio.CancelledError, Exception):
                                pass

                if winner_sub_events is not None:
                    logger.info(f"[dayflow-细分] 细分生成成功: persona={persona_name}, 子事件数={len(winner_sub_events)}")
                    return winner_sub_events

                logger.info(f"[dayflow-细分] 竞速无胜出: persona={persona_name}, 兜底文本={'有' if best_raw_text else '无'}")
                raw_text = best_raw_text
                actual_provider_id = best_provider_id

            if not raw_text and final_fallback_id:
                logger.info(f"[dayflow-细分] 尝试最终回退提供商: provider={final_fallback_id}, persona={persona_name}")
                try:
                    raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                        prompt, final_fallback_id, None, retry_count=0,
                    )
                    if raw_text:
                        logger.info(f"[dayflow-细分] 最终回退成功: provider={actual_provider_id}, persona={persona_name}, 长度={len(raw_text)}")
                except Exception as e:
                    logger.warning(f"[dayflow-细分] 最终回退失败: persona={persona_name}, 错误={e}")

            if not raw_text:
                logger.warning(f"[dayflow-细分] 无响应: persona={persona_name}")
                return None

            logger.info(f"[dayflow-细分] 开始解析细分响应: persona={persona_name}, 使用提供商={actual_provider_id}")
            parsed = safe_json_loads(raw_text)
            if not isinstance(parsed, dict):
                logger.warning(f"[dayflow-细分] 响应非 JSON 对象: persona={persona_name}")
                return None

            sub_events = parsed.get("sub_events")
            if not isinstance(sub_events, list):
                logger.warning(f"[dayflow-细分] sub_events 字段缺失或非数组: persona={persona_name}")
                return None

            ok, reason = validate_sub_events(sub_events, timeline)
            if not ok:
                logger.warning(f"[dayflow-细分] 校验失败: persona={persona_name}, 原因={reason}")
                return None

            logger.info(f"[dayflow-细分] 细分生成成功: persona={persona_name}, 子事件数={len(sub_events)}")
            return sub_events

        except Exception as e:
            logger.warning(f"[dayflow-细分] 细分生成异常: persona={persona_name}, 错误={e}")
            return None

    def build_current_sub_activity_injection(self, persona_name: str) -> str | None:
        if not persona_name:
            return None
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            store_key = self.normalize_persona_key(persona_name)
            data = self.store.get_schedule_for_date(store_key, today)
            if not data or data.get("meta", {}).get("error"):
                return None

            sub_events = data.get("sub_events")
            if not isinstance(sub_events, list) or not sub_events:
                return None

            timeline = data.get("timeline")
            if not isinstance(timeline, list) or not timeline:
                return None

            now = datetime.datetime.now()
            now_minutes = now.hour * 60 + now.minute

            index_map: dict[int, list[dict]] = {}
            for entry in sub_events:
                if not isinstance(entry, dict):
                    continue
                si = entry.get("source_index")
                if isinstance(si, int):
                    index_map[si] = entry.get("items") or []

            all_sub_items: list[tuple[int, dict]] = []
            for si in sorted(index_map.keys()):
                for item in index_map[si]:
                    all_sub_items.append((si, item))

            current_idx = -1
            for i, (si, item) in enumerate(all_sub_items):
                start_min = parse_hhmm_to_minutes(str(item.get("time_start") or ""))
                end_min = parse_hhmm_to_minutes(str(item.get("time_end") or ""))
                if start_min is not None and end_min is not None:
                    duration = end_min - start_min
                    if duration <= 0:
                        duration += 24 * 60
                    if start_min <= now_minutes < start_min + duration:
                        current_idx = i
                        break

            if current_idx < 0:
                return None

            lines = []
            _, current_item = all_sub_items[current_idx]
            cs = str(current_item.get("time_start") or "")
            ce = str(current_item.get("time_end") or "")
            ct = str(current_item.get("title") or "")
            lines.append(f"此刻：{cs}-{ce} {ct}")

            if current_idx > 0:
                _, prev_item = all_sub_items[current_idx - 1]
                ps = str(prev_item.get("time_start") or "")
                pe = str(prev_item.get("time_end") or "")
                pt = str(prev_item.get("title") or "")
                lines.append(f"刚做完：{ps}-{pe} {pt}")

            if current_idx < len(all_sub_items) - 1:
                _, next_item = all_sub_items[current_idx + 1]
                ns = str(next_item.get("time_start") or "")
                ne = str(next_item.get("time_end") or "")
                nt = str(next_item.get("title") or "")
                lines.append(f"接下来：{ns}-{ne} {nt}")

            return "\n".join(lines)

        except Exception as e:
            logger.debug(f"[dayflow] 构建当前子活动注入失败: {e}")
            return None

    def get_sub_events(self, persona_name: str, target_date: str | None = None) -> list | None:
        effective_date = target_date or datetime.datetime.now().strftime("%Y-%m-%d")
        store_key = self.normalize_persona_key(persona_name)
        data = self.store.get_schedule_for_date(store_key, effective_date)
        if not data or data.get("meta", {}).get("error"):
            return None
        sub_events = data.get("sub_events")
        if isinstance(sub_events, list) and sub_events:
            return sub_events
        return None

    async def generate_schedule(self, event, persona_name: str, persona_desc: str = "", session_id: str | None = None, target_date: str | None = None, auto_retry: bool = False, extra_requirement: str | None = None, force_regenerate: bool = False) -> dict:
        persona_ctx = await self._resolve_persona_context_internal(event=event, persona_name=persona_name, session_id=session_id)
        matched_persona = self.get_persona_config(persona_ctx.get("persona_name"), persona_ctx.get("persona_id"))
        effective_date = str(target_date or datetime.datetime.now().strftime("%Y-%m-%d")).strip() or datetime.datetime.now().strftime("%Y-%m-%d")
        if not matched_persona:
            target_persona = persona_ctx.get("persona_name") or persona_name
            logger.warning(f"[dayflow] 拒绝为未配置人格生成: persona={target_persona}")
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
        specified_sub_variant_names = None
        sub_variant_all_day = False
        style_reference, style_payload, style_sources, intent_overrides, real_weather = await self._research_style_reference(
            outfit_style, extra_requirement=extra_requirement, pool_options=pool_options, location=persona_location or None, persona_name=normalized_persona_name,
        )

        if real_weather:
            today_weather = real_weather
            logger.info(f"[dayflow] 真实天气覆盖: persona={normalized_persona_name}, location={persona_location}, weather={today_weather}")

        user_specified_outfit_style = None
        user_specified_outfit_item = None
        outfit_adjustments = None
        need_reresearch = False
        if intent_overrides:
            override_style = intent_overrides.get("outfit_style")
            override_item = intent_overrides.get("outfit_item")
            override_period = intent_overrides.get("outfit_item_period")
            override_afternoon_item = intent_overrides.get("outfit_item_afternoon")
            if override_item and override_item in SUB_VARIANT_NAME_TO_STYLE:
                mapped_style = SUB_VARIANT_NAME_TO_STYLE[override_item]
                if not override_style or override_style != mapped_style:
                    override_style = mapped_style
                    logger.info(f"[dayflow] 子款式反向索引: item={override_item} -> style={mapped_style}")
            if override_item and override_item in SUB_VARIANT_NAME_TO_STYLE:
                user_specified_outfit_item = override_item
                if override_period == "all_day":
                    specified_sub_variant_names = [override_item]
                    sub_variant_all_day = True
                elif override_period == "both" and override_afternoon_item:
                    specified_sub_variant_names = [override_item, override_afternoon_item]
                elif override_period == "afternoon":
                    specified_sub_variant_names = [None, override_item]
                else:
                    specified_sub_variant_names = [override_item]
            elif override_item:
                user_specified_outfit_item = override_item
            if override_style and override_style != outfit_style:
                style_reference, style_payload, style_sources, _, override_weather = await self._research_style_reference(override_style, location=persona_location or None, persona_name=normalized_persona_name, specified_sub_variant_names=specified_sub_variant_names, sub_variant_all_day=sub_variant_all_day)
                if override_weather:
                    real_weather = override_weather
                    today_weather = override_weather
                    logger.info(f"[dayflow] 真实天气覆盖（意图覆盖）: persona={normalized_persona_name}, location={persona_location}, weather={today_weather}")
                outfit_style = override_style
                user_specified_outfit_style = override_style
            elif override_style:
                outfit_style = override_style
                user_specified_outfit_style = override_style
                if specified_sub_variant_names:
                    need_reresearch = True
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
                f"[dayflow] 意图覆盖已应用: persona={normalized_persona_name}, "
                f"outfit_style={outfit_style}, outfit_item={user_specified_outfit_item}, "
                f"main_type={schedule_main_type}, driver={core_event_driver}, adjustments={outfit_adjustments}, "
                f"sub_variants={specified_sub_variant_names}, all_day={sub_variant_all_day}"
            )

        if need_reresearch:
            style_reference, style_payload, style_sources, _, _ = await self._research_style_reference(
                outfit_style, location=persona_location or None, persona_name=normalized_persona_name,
                specified_sub_variant_names=specified_sub_variant_names, sub_variant_all_day=sub_variant_all_day,
            )
            logger.info(f"[dayflow] 子款式指定重做风格研究: style={outfit_style}, variants={specified_sub_variant_names}, all_day={sub_variant_all_day}")

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
                if sub_variant_all_day:
                    prompt += f"\n- 全天穿着此款，不换装"
            if outfit_adjustments:
                prompt += f"\n- 穿搭调整：{outfit_adjustments}"

        validate_persona = {
            "outfit_style": outfit_style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "today_weather": today_weather,
            "user_specified_outfit_style": user_specified_outfit_style,
        }
        prompt += build_format_priority_append_prompt(validate_persona)
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
            f"[dayflow-调试] persona={normalized_persona_name} style={outfit_style} "
            f"main={schedule_main_type} driver={core_event_driver} variation={configured_variation}->{effective_variation} "
            f"desc_len={len(replacements['persona_desc'] or '')}, session={effective_session_id or 'none'}, target_date={date_str}"
        )

        configured_provider_id = (persona.get("select_providers") or [None])[0]

        racing_provider_ids = persona.get("select_providers") or []
        if len(racing_provider_ids) <= 1:
            if len(racing_provider_ids) == 1:
                configured_provider_id = racing_provider_ids[0]

            if not configured_provider_id:
                return build_generation_error_data(normalized_persona_name, validate_persona, "未配置日程生成提供商")

            repair_retries = int(persona.get("retry_count", 2) or 2) if auto_retry else 0
            actual_provider_id = configured_provider_id
            try:
                raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                    prompt,
                    configured_provider_id,
                    None,
                    retry_count=0,
                )
                payload = normalize_payload(safe_json_loads(raw_text), validate_persona)
                ok, reason = validate_payload(payload, validate_persona)
                for attempt in range(1, repair_retries + 1):
                    if ok and payload:
                        break
                    logger.warning(f"[dayflow] 载荷校验失败: persona={normalized_persona_name}, attempt={attempt}, reason={reason}")
                    repair_prompt = build_repair_prompt(prompt, raw_text, reason, validate_persona, retry_index=attempt)
                    raw_text, actual_provider_id = await self.call_llm_with_provider_fallback(
                        repair_prompt,
                        configured_provider_id,
                        None,
                        retry_count=0,
                    )
                    payload = normalize_payload(safe_json_loads(raw_text), validate_persona)
                    ok, reason = validate_payload(payload, validate_persona)
                if not ok or not payload:
                    serial_best_partial = {"payload": None, "provider_id": actual_provider_id, "raw_text": raw_text, "reason": reason}
                    final_result = await self._try_final_fallback(
                        prompt=prompt,
                        ctx=GenerationContext(
                            normalized_persona_name=normalized_persona_name,
                            outfit_style=outfit_style,
                            schedule_main_type=schedule_main_type,
                            core_event_driver=core_event_driver,
                            date_str=date_str,
                            configured_provider_id=configured_provider_id,
                            effective_session_id=effective_session_id,
                            today_weather=today_weather,
                            configured_variation=configured_variation,
                            effective_variation=effective_variation,
                            style_reference=style_reference,
                            validate_persona=validate_persona,
                            best_partial=serial_best_partial,
                        ),
                    )
                    if final_result:
                        return final_result
                    return build_generation_error_data(normalized_persona_name, validate_persona, reason or "模型输出未通过校验")
                return self._build_schedule_result(
                    payload=payload,
                    ctx=GenerationContext(
                        normalized_persona_name=normalized_persona_name,
                        outfit_style=outfit_style,
                        schedule_main_type=schedule_main_type,
                        core_event_driver=core_event_driver,
                        date_str=date_str,
                        actual_provider_id=actual_provider_id,
                        configured_provider_id=configured_provider_id,
                        effective_session_id=effective_session_id,
                        today_weather=today_weather,
                        configured_variation=configured_variation,
                        effective_variation=effective_variation,
                        style_reference=style_reference,
                        validate_persona=validate_persona,
                    ),
                )
            except Exception as e:
                logger.warning(
                    f"[dayflow] LLM 生成失败: persona={normalized_persona_name}: {self._summarize_error(e)}, "
                    f"configured_provider={configured_provider_id}, target_date={date_str}"
                )
                exception_best_partial = {"payload": None, "provider_id": actual_provider_id, "raw_text": "", "reason": str(e)}
                final_result = await self._try_final_fallback(
                    prompt=prompt,
                    ctx=GenerationContext(
                        normalized_persona_name=normalized_persona_name,
                        outfit_style=outfit_style,
                        schedule_main_type=schedule_main_type,
                        core_event_driver=core_event_driver,
                        date_str=date_str,
                        configured_provider_id=configured_provider_id,
                        effective_session_id=effective_session_id,
                        today_weather=today_weather,
                        configured_variation=configured_variation,
                        effective_variation=effective_variation,
                        style_reference=style_reference,
                        validate_persona=validate_persona,
                        best_partial=exception_best_partial,
                    ),
                )
                if final_result:
                    return final_result
                return build_generation_error_data(normalized_persona_name, validate_persona, f"LLM 调用失败: {self._summarize_error(e)}")

        seen = set()
        deduped_providers = []
        for pid in racing_provider_ids:
            if pid and pid not in seen:
                seen.add(pid)
                deduped_providers.append(pid)
        if not deduped_providers:
            return build_generation_error_data(normalized_persona_name, validate_persona, "未配置任何日程生成提供商")

        repair_retries = int(persona.get("retry_count", 2) or 2) if auto_retry else 0
        logger.info(
            f"[dayflow] 竞速生成: persona={normalized_persona_name}, "
            f"providers={deduped_providers}, target_date={date_str}"
        )

        result, best_partial = await self._race_providers(
            prompt=prompt,
            providers=deduped_providers,
            normalized_persona_name=normalized_persona_name,
            validate_persona=validate_persona,
            repair_retries=repair_retries,
        )

        if result is None:
            logger.warning(f"[dayflow] 竞速所有提供商均失败: persona={normalized_persona_name}，尝试最终兜底")
            final_result = await self._try_final_fallback(
                prompt=prompt,
                ctx=GenerationContext(
                    normalized_persona_name=normalized_persona_name,
                    outfit_style=outfit_style,
                    schedule_main_type=schedule_main_type,
                    core_event_driver=core_event_driver,
                    date_str=date_str,
                    configured_provider_id=configured_provider_id,
                    effective_session_id=effective_session_id,
                    today_weather=today_weather,
                    configured_variation=configured_variation,
                    effective_variation=effective_variation,
                    style_reference=style_reference,
                    validate_persona=validate_persona,
                    best_partial=best_partial,
                ),
            )
            if final_result:
                return final_result
            return build_generation_error_data(normalized_persona_name, validate_persona, "竞速提供商与兜底提供商均失败")

        return self._build_schedule_result(
            payload=result["payload"],
            ctx=GenerationContext(
                normalized_persona_name=normalized_persona_name,
                outfit_style=outfit_style,
                schedule_main_type=schedule_main_type,
                core_event_driver=core_event_driver,
                date_str=date_str,
                actual_provider_id=result["provider_id"],
                configured_provider_id=configured_provider_id,
                effective_session_id=effective_session_id,
                today_weather=today_weather,
                configured_variation=configured_variation,
                effective_variation=effective_variation,
                style_reference=style_reference,
                validate_persona=validate_persona,
                racing_provider_ids=deduped_providers,
            ),
        )
