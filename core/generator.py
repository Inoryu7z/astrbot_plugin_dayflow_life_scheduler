import datetime
import json
import re
from typing import Any

from astrbot.api import logger


def render_timeline_block(timeline: list, start_idx: int = 1) -> str:
    """渲染 timeline 切片为文本块。start_idx 控制起始编号（保持原始序号）。"""
    parts = []
    for i, item in enumerate(timeline, start_idx):
        if not isinstance(item, dict):
            continue
        time_start = str(item.get("time_start") or "").strip()
        time_end = str(item.get("time_end") or "").strip()
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        outfit_change = str(item.get("outfit_change") or "").strip()
        time_range = f"{time_start}-{time_end}" if time_start and time_end else ""
        block = f"── 第 {i} 项 ──\n🕐 {time_range}"
        if title:
            block += f"\n📌 {title}"
        if detail:
            block += f"\n📄 {detail}"
        if outfit_change:
            block += f"\n👗 换装：{outfit_change}"
        parts.append(block)
    return "\n\n".join(parts)


def render_schedule_display(data: dict) -> str:
    outfit = str(data.get("outfit") or "").strip()
    summary = str(data.get("summary") or "").strip()
    timeline = data.get("timeline")

    header = f"👕 今日穿搭：{outfit}"
    if summary:
        header += f"\n💬 {summary}"

    if isinstance(timeline, list) and timeline:
        schedule_text = render_timeline_block(timeline)
    else:
        schedule_text = str(data.get("schedule") or "").strip()

    return f"{header}\n📝 日程安排：\n{schedule_text}"


def is_schedule_valid(data: dict) -> bool:
    if not data:
        return False
    meta = data.get("meta") or {}
    if meta.get("error"):
        return False
    outfit = str(data.get("outfit") or "").strip()
    schedule = str(data.get("schedule") or "").strip()
    if not outfit and not schedule:
        return False
    if outfit == "尚未生成" or "尚未生成成功" in schedule:
        return False
    return True


FORMAT_PRIORITY_APPEND_PROMPT = """

---
【格式输出追加要求】
本任务有三层优先级，冲突时上层优先：
1. 格式合规：输出必须是合法 JSON，字段结构正确，否则无法解析
2. 内容质量：事件有趣、细节丰富、逻辑连贯
3. 文采表达：语言美感、氛围表达

请严格遵守以下规则：
1. 只允许输出 JSON 对象本体，不要 Markdown，不要代码块，不要解释，不要额外前后缀。
2. JSON 必须包含字段：outfit_style、outfit、summary、timeline。
3. outfit_style 必须严格等于指定风格，不允许近义改写，不允许变体，不允许替换措辞。
4. outfit 的第一行必须严格写为：风格：{outfit_style}
5. 除了"风格：{outfit_style}"这行以外，不允许写成"【风格】"、"穿搭风格："、"风格为："或任何其他变体。
6. timeline 必须是数组，每个元素包含 time_start、time_end、title、detail、outfit_change 五个字段。
7. title 禁止出现"核心事件"、"XX驱动"等元标签。
"""


STYLE_LINE_RE = re.compile(r"^\s*(?:风格|【风格】|\[风格\]|穿搭风格|风格为)\s*[:：]\s*(.+?)\s*$")


def strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _strip_json_comments(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


def _fix_trailing_commas(text: str) -> str:
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _try_repair_truncated_json(text: str) -> dict | None:
    match = re.search(r"\{", text)
    if not match:
        return None
    fragment = text[match.start():]
    for _ in range(10):
        try:
            obj = json.loads(fragment)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        fragment = re.sub(r'"[^"]*$', "", fragment)
        fragment = re.sub(r",\s*$", "", fragment)
        fragment = re.sub(r"\[[^\]]*$", "", fragment)
        fragment += "}"
    try:
        obj = json.loads(fragment)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def safe_json_loads(text: str) -> dict | None:
    text = strip_code_fence(text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        candidate = match.group(0)
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        repaired = _fix_trailing_commas(candidate)
        try:
            obj = json.loads(repaired)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        repaired = _strip_json_comments(candidate)
        try:
            obj = json.loads(repaired)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        repaired = _fix_trailing_commas(_strip_json_comments(candidate))
        try:
            obj = json.loads(repaired)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    obj = _try_repair_truncated_json(text)
    if obj is not None:
        return obj
    return None


def render_prompt(template: str, values: dict[str, str]) -> str:
    rendered = template or ""
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value or "")
    return rendered


def build_format_priority_append_prompt(persona: dict[str, Any]) -> str:
    required_style = str(persona.get("outfit_style") or "").strip()
    return FORMAT_PRIORITY_APPEND_PROMPT.replace("{outfit_style}", required_style)


def _synthesize_schedule_from_timeline(payload: dict) -> str:
    # 兼容 DayMind：DayMind 只读取 schedule 字符串字段，不读取 timeline 数组。
    # 当模型输出 timeline 数组但未输出 schedule 时，从此处合成 schedule 字符串，
    # 使 DayMind 的 diary/reflection 提示词仍能获取完整日程信息。
    timeline = payload.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return ""
    parts = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        time_start = str(item.get("time_start") or "").strip()
        time_end = str(item.get("time_end") or "").strip()
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        outfit_change = str(item.get("outfit_change") or "").strip()
        time_range = f"{time_start}-{time_end}" if time_start and time_end else ""
        line_parts = []
        if time_range:
            line_parts.append(time_range)
        if title:
            line_parts.append(title)
        header = " ".join(line_parts)
        block = header
        if detail:
            block += f"\n{detail}"
        if outfit_change:
            block += f"\n👗 换装：{outfit_change}"
        parts.append(block)
    return "\n".join(parts)


def normalize_payload(payload: dict | None, persona: dict[str, Any]) -> dict | None:
    if not payload or not isinstance(payload, dict):
        return payload

    required_style = str(persona.get("outfit_style") or "").strip()
    normalized = dict(payload)

    if required_style:
        normalized["outfit_style"] = required_style

    outfit = str(normalized.get("outfit") or "")
    if required_style and outfit.strip():
        lines = outfit.splitlines()
        if lines:
            first = lines[0].strip()
            m = STYLE_LINE_RE.match(first)
            if m or first != f"风格：{required_style}":
                lines[0] = f"风格：{required_style}"
                outfit = "\n".join(lines)
        else:
            outfit = f"风格：{required_style}"
        normalized["outfit"] = outfit

    # 兼容 DayMind：若模型输出了 timeline 但未输出 schedule，
    # 从 timeline 数组合成 schedule 字符串，确保 DayMind 可正常读取日程。
    raw_schedule = normalized.get("schedule")
    if isinstance(raw_schedule, list):
        if "timeline" not in normalized or not isinstance(normalized.get("timeline"), list):
            normalized["timeline"] = raw_schedule
        normalized["schedule"] = _synthesize_schedule_from_timeline(normalized)
    schedule = str(normalized.get("schedule") or "").strip()
    if not schedule:
        synthesized = _synthesize_schedule_from_timeline(normalized)
        if synthesized:
            normalized["schedule"] = synthesized

    return normalized


def validate_payload(payload: dict | None, persona: dict[str, Any]) -> tuple[bool, str]:
    if not payload:
        return False, "未解析出 JSON 对象"

    required_style = str(persona.get("outfit_style") or "").strip()
    user_specified_style = str(persona.get("user_specified_outfit_style") or "").strip()
    outfit_style = str(payload.get("outfit_style") or "").strip()
    outfit = str(payload.get("outfit") or "").strip()

    if not outfit:
        return False, "outfit 不能为空"
    if required_style and outfit_style != required_style:
        if user_specified_style and outfit_style == user_specified_style:
            pass
        else:
            return False, f'outfit_style 必须严格等于 "{required_style}"'
    if required_style:
        first_line = (outfit.splitlines()[0] if outfit.splitlines() else "").strip()
        expected_first_line = f"风格：{required_style}"
        if first_line != expected_first_line:
            if user_specified_style and first_line == f"风格：{user_specified_style}":
                pass
            else:
                return False, f'outfit 第一行必须为 "风格：{required_style}"'

    timeline = payload.get("timeline")

    # timeline 是日程的核心字段（输出格式只要求 timeline，不要求 schedule）
    # schedule 字段是 normalize_payload 从 timeline 合成的 DayMind 兼容字段，不参与校验
    if not isinstance(timeline, list):
        return False, "timeline 为空或不是数组"
    if not timeline:
        return False, "timeline 不能为空数组"
    for i, item in enumerate(timeline):
        if not isinstance(item, dict):
            return False, f"timeline[{i}] 不是有效对象"
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if not title:
            return False, f"timeline[{i}] 的 title 不能为空"
        if not detail:
            return False, f"timeline[{i}] 的 detail 不能为空"
        for meta_label in ["核心事件", "主线事件", "情感驱动", "任务驱动", "人际驱动", "抉择驱动", "突发驱动", "仪式驱动", "回忆驱动"]:
            if meta_label in title:
                return False, f'timeline[{i}] 的 title 包含禁止的元标签："{meta_label}"'

    return True, ""


def build_repair_prompt(
    original_prompt: str,
    bad_text: str,
    reason: str,
    persona: dict[str, Any],
    retry_index: int = 1,
) -> str:
    required_style = str(persona.get("outfit_style") or "").strip()
    required_main_type = str(persona.get("schedule_main_type") or "").strip()
    required_driver = str(persona.get("core_event_driver") or "").strip()

    bad_text_clean = str(bad_text or "").strip()
    # 判断上次输出是否含 timeline 核心字段
    # 仅含 outfit 但缺 timeline 的残缺输出不算"已基本成型"，走补全模式
    has_valid_output = bool(bad_text_clean) and "{" in bad_text_clean and (
        "timeline" in bad_text_clean.lower() or "schedule" in bad_text_clean.lower()
    )

    # 针对性指示：如果失败原因与 timeline 相关，明确指示保留 outfit 并补 timeline
    reason_lower = str(reason or "").lower()
    timeline_issue = "timeline" in reason_lower

    if has_valid_output:
        mode_intro = (
            "【修复模式】\n"
            f"这是你第 {retry_index} 次因校验失败被退回修复。\n"
            "上次输出已基本成型，请基于上次输出做最小修改：只改出错的部分，保留已正确的字段。\n"
            "不要重新生成完整 JSON，不要改动已正确的字段。\n"
        )
    else:
        mode_intro = (
            "【补全模式】\n"
            f"这是你第 {retry_index} 次因校验失败被退回修复。\n"
            "上次输出残缺（缺少 timeline 等核心字段），请保留上次输出中已正确的字段（如 outfit_style/outfit/summary），"
            "仅补全缺失的核心字段。不要重新生成已正确的字段，避免重复消耗篇幅导致后续字段再次丢失。\n"
        )

    if timeline_issue:
        mode_intro += (
            "⚠️ 特别指示：上次输出缺少 timeline 字段或 timeline 不合法。"
            "请直接保留上次输出中的 outfit_style/outfit/summary 内容原样不动，仅补充完整的 timeline 数组（8-10 个连续时段，覆盖 00:00-23:59）。\n"
            "不要重新生成 outfit，不要改动已正确的字段。\n"
        )

    result = (
        f"{original_prompt}\n\n"
        "---\n"
        f"{mode_intro}"
        f"最近一次失败原因：{reason}\n"
        "请根据失败原因和上次输出，自行判断需要小修小补还是补全缺失字段："
        "大多数情况下上次输出只有部分问题，保留已正确字段并补全缺失字段即可；"
        "仅当上次输出完全不可用、严重乱码或严重偏离要求时才从零重写。\n"
    )

    result += (
        "格式正确是最高优先级，优先级高于内容表现、语言美感、角色氛围和细节发挥。\n"
        f"必须使用穿搭风格：{required_style}\n"
        f"必须遵循日程主线类型：{required_main_type}\n"
        f"必须遵循核心事件驱动：{required_driver}\n"
        "只允许输出 JSON 对象本体，不要 Markdown，不要解释，不要代码块。\n"
        f"JSON 的 outfit_style 必须严格等于 \"{required_style}\"。\n"
        f"outfit 第一行必须严格为：风格：{required_style}\n"
        "不要写成“【风格】”、“穿搭风格：”、“风格为：”或任何其他变体。\n"
        "JSON 必须包含 timeline 数组，每个元素含 time_start、time_end、title、detail、outfit_change。\n"
        "title 禁止出现“核心事件”、“XX驱动”等元标签。\n"
        "如果格式与内容表达发生冲突，必须优先满足格式要求。\n"
    )

    # 始终附加上次输出：即使残缺，LLM 也能复用已正确的字段（如 outfit），仅补全缺失字段
    # 避免"从零生成"时 LLM 被原 prompt 的超详细 outfit 引导带偏，再次只输出 outfit 而丢 timeline
    if bad_text_clean:
        result += "上次的不合格输出如下（请保留其中已正确的字段，仅修复/补全出错或缺失的部分；若严重不可用则从零重写）：\n" + bad_text_clean
    return result


def extract_timeline(schedule: str | dict) -> list[dict[str, str]]:
    timeline = []
    if isinstance(schedule, dict):
        tl_array = schedule.get("timeline")
        if isinstance(tl_array, list):
            for item in tl_array:
                if not isinstance(item, dict):
                    continue
                start = str(item.get("time_start") or "").strip()
                end = str(item.get("time_end") or "").strip()
                title = str(item.get("title") or "").strip()
                if start and end:
                    entry = {"time": start, "activity": f"至 {end} {title}", "status": "planned"}
                    timeline.append(entry)
            return timeline[:20]
        schedule = str(schedule.get("schedule") or "")
    for m in re.finditer(r"(\d{2}:\d{2})\s*[-—~～]\s*(\d{2}:\d{2})", schedule or ""):
        start = m.group(1)
        end = m.group(2)
        timeline.append({"time": start, "activity": f"至 {end} 的日程段", "status": "planned"})
    return timeline[:20]


def build_generation_error_data(persona_name: str, persona: dict[str, Any], reason: str = "") -> dict:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    style = persona.get("outfit_style") or "自然"
    schedule_main_type = persona.get("schedule_main_type") or "日常常规型"
    core_event_driver = persona.get("core_event_driver") or "任务驱动"
    weather = persona.get("today_weather") or ""
    if reason:
        logger.warning(f"[dayflow] 生成错误: persona={persona_name}, reason={reason}")
    return {
        "outfit": "",
        "schedule": "",
        "meta": {
            "style": style,
            "schedule_main_type": schedule_main_type,
            "core_event_driver": core_event_driver,
            "persona_name": persona_name,
            "date": today,
            "weather": weather,
            "fallback": True,
            "fallback_reason": reason,
            "error": True,
        },
        "timeline": [],
        "weather": weather,
        "memo": reason or "生成失败",
        "long_term_memory": [],
    }


from .utils import parse_hhmm_to_minutes


def build_draft_skeleton(timeline: list[dict]) -> str:
    if not isinstance(timeline, list) or not timeline:
        return ""
    parts = []
    for i, item in enumerate(timeline):
        if not isinstance(item, dict):
            continue
        time_start = str(item.get("time_start") or "").strip()
        time_end = str(item.get("time_end") or "").strip()
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        outfit_change = str(item.get("outfit_change") or "").strip()
        block = f"── 第 {i} 项 ──\n时间：{time_start}-{time_end}"
        if title:
            block += f"\n标题：{title}"
        if detail:
            block += f"\n内容：{detail}"
        if outfit_change:
            block += f"\n换装：{outfit_change}"
        parts.append(block)
    return "\n\n".join(parts)


def validate_sub_events(sub_events: Any, timeline: list[dict]) -> tuple[bool, str]:
    if not isinstance(sub_events, list):
        return False, "sub_events 不是数组"
    if not sub_events:
        return False, "sub_events 为空数组"
    if not isinstance(timeline, list) or not timeline:
        return False, "timeline 为空，无法校验 sub_events"

    timeline_len = len(timeline)
    covered_indices: set[int] = set()

    for entry_idx, entry in enumerate(sub_events):
        if not isinstance(entry, dict):
            return False, f"sub_events[{entry_idx}] 不是有效对象"

        source_index = entry.get("source_index")
        if not isinstance(source_index, int):
            return False, f"sub_events[{entry_idx}] 的 source_index 不是整数"
        if source_index < 0 or source_index >= timeline_len:
            return False, f"sub_events[{entry_idx}] 的 source_index={source_index} 超出范围(0-{timeline_len - 1})"
        if source_index in covered_indices:
            return False, f"sub_events[{entry_idx}] 的 source_index={source_index} 重复"
        covered_indices.add(source_index)

        items = entry.get("items")
        if not isinstance(items, list):
            return False, f"sub_events[{entry_idx}] 的 items 不是数组"
        if len(items) < 1:
            return False, f"sub_events[{entry_idx}] 没有子事件"

        parent = timeline[source_index]
        parent_start = parse_hhmm_to_minutes(str(parent.get("time_start") or ""))
        parent_end = parse_hhmm_to_minutes(str(parent.get("time_end") or ""))
        if parent_start is None or parent_end is None:
            return False, f"timeline[{source_index}] 的时间格式无效"

        for item_idx, item in enumerate(items):
            if not isinstance(item, dict):
                return False, f"sub_events[{entry_idx}].items[{item_idx}] 不是有效对象"
            sub_start = parse_hhmm_to_minutes(str(item.get("time_start") or ""))
            sub_end = parse_hhmm_to_minutes(str(item.get("time_end") or ""))
            if sub_start is None or sub_end is None:
                return False, f"sub_events[{entry_idx}].items[{item_idx}] 的时间格式无效"

    return True, ""
