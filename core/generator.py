import datetime
import json
import re
from typing import Any

from astrbot.api import logger


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


def safe_json_loads(text: str) -> dict | None:
    text = strip_code_fence(text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
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
    schedule = str(payload.get("schedule") or "").strip()

    if isinstance(timeline, list):
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
    elif not schedule:
        return False, "timeline 和 schedule 均为空，至少需要提供其一"

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
    return (
        f"{original_prompt}\n\n"
        "---\n"
        "【重写校验批注】\n"
        f"这是你第 {retry_index} 次因格式/结构校验失败而被退回重写。\n"
        f"最近一次失败原因：{reason}\n"
        "本轮重写时，格式正确是最高优先级，优先级高于内容表现、语言美感、角色氛围和细节发挥。\n"
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
        "之前的不合格输出如下（仅供修复参考）：\n"
        f"{bad_text}"
    )


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
        logger.warning(f"[dayflow] generation error for persona={persona_name}, reason={reason}")
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
