import datetime
import json
import re
from typing import Any

from astrbot.api import logger


FORMAT_PRIORITY_APPEND_PROMPT = """

---
【格式输出优先级追加要求】
本任务首先是“格式合规任务”，其次才是“内容创作任务”。
若格式不合规，则整次输出直接视为失败。
请把“格式正确”放在最高优先级，优先级高于文采、人格演绎、氛围表达和内容丰富度。

你必须严格遵守以下规则：
1. 只允许输出 JSON 对象本体，不要 Markdown，不要代码块，不要解释，不要额外前后缀。
2. JSON 必须包含字段：outfit_style、outfit、schedule。
3. outfit_style 必须严格等于指定风格，不允许近义改写，不允许变体，不允许替换措辞。
4. outfit 的第一行必须严格写为：风格：{outfit_style}
5. 除了“风格：{outfit_style}”这行以外，不允许写成“【风格】”、“穿搭风格：”、“风格为：”或任何其他变体。
6. 如果你觉得内容表达与格式要求冲突，必须优先服从格式要求。
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

    return normalized


def validate_payload(payload: dict | None, persona: dict[str, Any]) -> tuple[bool, str]:
    if not payload:
        return False, "未解析出 JSON 对象"

    required_style = str(persona.get("outfit_style") or "").strip()
    outfit_style = str(payload.get("outfit_style") or "").strip()
    outfit = str(payload.get("outfit") or "").strip()
    schedule = str(payload.get("schedule") or "").strip()

    if not outfit:
        return False, "outfit 不能为空"
    if not schedule:
        return False, "schedule 不能为空"
    if required_style and outfit_style != required_style:
        return False, f'outfit_style 必须严格等于 "{required_style}"'
    if required_style:
        first_line = (outfit.splitlines()[0] if outfit.splitlines() else "").strip()
        if first_line != f"风格：{required_style}":
            return False, f'outfit 第一行必须为 "风格：{required_style}"'
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
        "如果格式与内容表达发生冲突，必须优先满足格式要求。\n"
        "之前的不合格输出如下（仅供修复参考）：\n"
        f"{bad_text}"
    )


def extract_timeline(schedule: str) -> list[dict[str, str]]:
    timeline = []
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
