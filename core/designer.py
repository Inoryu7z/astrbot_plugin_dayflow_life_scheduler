"""设计师与审核师 LLM 调用。

负责 webui 工作流的穿搭设计/迭代审核：
- 设计师：基于风格名 + 已有优秀设计（防重复）+ 用户输入（可选）→ 输出 {name, description} 散文
- 审核师：基于风格名 + 原设计 + 用户意见 → 输出修改后的 {name, description}

与运行时 Grok 风格研究的区别：
- 这里调用普通 LLM provider（用户可在 _conf_schema 配置，也可选 Grok 作为普通 provider）
- 不走 grok 搜索插件
- 输出是散文 name+description，与 STYLE_SUB_VARIANTS 条目格式一致
"""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger

from .curated_store import CuratedStore


# ------------------------------------------------------------------
# 默认提示词（散文输出，区别于运行时 Grok 研究的 JSON 输出）
# ------------------------------------------------------------------

DEFAULT_DESIGNER_PROMPT = """你是专业服饰设计师。基于风格名创作一套完整的穿搭方案，输出为散文描述。

## 工作方式
1. 理解风格「{style_name}」的核心特征、标志性单品和搭配逻辑
2. 构思设计语言三要素——这是高质量设计的基础：
   - 色彩哲学：该风格的核心色系与配色逻辑
   - 廓形语言：典型廓形、层次关系与比例规则
   - 材质情绪：标志性材质及其传达的情绪基调
3. 基于设计语言，创作一套穿搭方案
4. 若提供了"已有优秀设计"，本次设计必须与它们在配色方向、廓形语言、材质情绪、风格倾向上做出明显差异，禁止重复度过高
5. 若提供了"用户要求"，必须严格遵循用户意见，**用户意见优先级最高**，即使用户意见与下文设计原则有冲突也以用户意见为准
6. 若用户要求中包含一套较成品的穿搭描述，应基于用户提供的方案优化润色而非完全推翻重做

## 设计要求
- description 必须是完整散文，500-800字
- 从头到脚完整描述：内搭、外搭、下装、袜子、鞋、配饰、发型、妆容
- 色名必须具体：禁用"粉色""蓝色""白色"等笼统色名，须用"樱花粉""雾蓝""象牙白"等具象色名
- 材质必须具体：禁用"布料""面料"等笼统说法，须用"雪纺""缎面""羊绒""灯芯绒"等具体材质名
- 版型必须明确：须注明A字/直筒/修身/oversized/茧型/喇叭等剪裁特征
- 配饰覆盖至少两项（包/鞋/首饰/发饰）
- 材质混搭体现设计感（光泽×哑光、柔软×硬挺等）
- 禁止体型修正类语言（"显瘦""修饰XX部位""拉长腿部"等）

## 洛丽塔风格特殊规范
当风格名包含"洛丽塔"时，单品名称必须使用通用自然语言，严禁出现圈子专属术语或款式代号：
- ✓"蝴蝶结发箍" ✗"KC发饰"
- ✓"印花图案" ✗"柄图"
- ✓"连衣裙" ✗"JSK""OP"
- ✓"过膝长袜" ✗"OTK"

## 输出格式
只输出 JSON 对象本体，不要 Markdown 代码块，不要额外解释：
{{"name": "款式名称（4-8字，富有诗意或意象）", "description": "完整穿搭描述（散文）"}}"""


DEFAULT_REVIEWER_PROMPT = """你是资深服饰美学审查师。基于目标风格的经典美学范式，对已有的穿搭方案做美学维度的审查与优化，并严格遵循用户的修改意见。

## 待审查方案
- 风格名：「{style_name}」
- 款式名称：{original_name}
- 穿搭描述：
{original_description}

## 用户修改意见
{user_feedback}

**重要**：用户意见优先级最高，必须在修改中体现。即使用户意见与下文美学原则有冲突，也以用户意见为准。

## 审查维度（逐项校验是否有可优化空间）
1. **风格纯度**：每件单品是否匹配该风格的美学体系，是否存在风格违和的单品
2. **色彩和谐**：配色是否具备明确的主次层级，色彩关系是否和谐
3. **材质对话**：材质组合是否有明确的美学意图（硬挺/柔软、光泽/哑光对比）
4. **廓形比例**：上下装廓形对比是否合理，整体比例是否符合风格特征
5. **视觉焦点**：整体造型是否有且仅有1个核心视觉焦点
6. **单品必要性**：是否存在为叠搭而硬加的冗余单品

## 决策原则（优先级从高到低）
1. **用户意见优先**：用户的修改意见必须体现
2. **风格一致性**：所有修改必须严格贴合目标风格的美学体系
3. **保留亮点**：保留原方案中已有的优质设计，仅修改存在提升空间的部分
4. **实质提升**：改进后须具备可感知的美学提升

## 禁用规则
- 全程禁止出现任何体型修正类表述（显瘦、显高、修饰部位、拉长比例等）
- 不得偏移到其他风格

## 输出格式
只输出 JSON 对象本体，不要 Markdown 代码块，不要额外解释：
{{"name": "修改后的款式名称（4-8字）", "description": "修改后的完整穿搭描述（散文，500-800字）"}}"""


# ------------------------------------------------------------------
# JSON 解析工具
# ------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中提取 JSON 对象。容忍 markdown 代码块、前后说明文字。"""
    text = str(text or "").strip()
    if not text:
        return None
    # 优先尝试代码块
    match = _JSON_BLOCK_RE.search(text)
    if match:
        candidate = match.group(1).strip()
    else:
        candidate = text
    # 找到第一个 { 到最后一个 }
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    snippet = candidate[start: end + 1]
    try:
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    # 尝试更宽松的解析（容忍尾逗号等）
    try:
        cleaned = re.sub(r",\s*([}\]])", r"\1", snippet)
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _format_designer_prompt(
    template: str,
    style_name: str,
    existing_outfits: list[dict[str, str]] | None = None,
    user_input: str | None = None,
) -> str:
    """格式化设计师提示词。占位符：{style_name}。已有设计与用户要求作为追加段。"""
    template = str(template or "").strip()
    if not template:
        template = DEFAULT_DESIGNER_PROMPT
    try:
        prompt = template.format(style_name=style_name)
    except Exception:
        prompt = template + f"\n\n本次需要设计的风格：「{style_name}」"
    append_parts: list[str] = []
    if existing_outfits:
        lines = ["", "## 已有优秀设计（防重复参考，本次设计必须与之做出明显差异）"]
        for idx, item in enumerate(existing_outfits, 1):
            name = str(item.get("name") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not name or not desc:
                continue
            preview = desc if len(desc) <= 400 else desc[:400].rstrip() + "…"
            lines.append(f"{idx}. 「{name}」：{preview}")
        append_parts.append("\n".join(lines))
    if user_input and user_input.strip():
        append_parts.append(
            "## 用户要求（优先级最高，必须严格遵循）\n" + user_input.strip()
        )
    if append_parts:
        prompt += "\n\n" + "\n\n".join(append_parts)
    return prompt


def _format_reviewer_prompt(
    template: str,
    style_name: str,
    original_name: str,
    original_description: str,
    user_feedback: str,
) -> str:
    """格式化审核师提示词。占位符：{style_name} {original_name} {original_description} {user_feedback}"""
    template = str(template or "").strip()
    if not template:
        template = DEFAULT_REVIEWER_PROMPT
    try:
        return template.format(
            style_name=style_name,
            original_name=original_name,
            original_description=original_description,
            user_feedback=user_feedback,
        )
    except Exception as e:
        logger.warning(f"[dayflow-设计师] 审核师提示词格式化失败，回退拼接: {e}")
        return (
            template
            + f"\n\n## 待审查方案\n风格名：「{style_name}」\n款式名称：{original_name}\n穿搭描述：\n{original_description}\n\n## 用户修改意见\n{user_feedback}"
        )


# ------------------------------------------------------------------
# 设计师/审核师
# ------------------------------------------------------------------


class OutfitDesigner:
    """设计师 + 审核师 LLM 调用封装。"""

    def __init__(
        self,
        context: Any,
        curated_store: CuratedStore,
        designer_provider_id: str | None = None,
        reviewer_provider_id: str | None = None,
        fallback_provider_getter=None,
        llm_timeout_seconds: int = 0,
    ):
        self.context = context
        self.curated_store = curated_store
        self.designer_provider_id = designer_provider_id
        self.reviewer_provider_id = reviewer_provider_id
        # 兜底：当 designer/reviewer provider 未配置时，调用此回调获取默认 provider id
        self._fallback_provider_getter = fallback_provider_getter
        self._llm_timeout_seconds = int(llm_timeout_seconds or 0)

    def _resolve_provider_id(self, provider_id: str | None) -> str | None:
        if provider_id and provider_id.strip():
            return provider_id.strip()
        if self._fallback_provider_getter is not None:
            try:
                result = self._fallback_provider_getter()
                if isinstance(result, str) and result.strip():
                    return result.strip()
            except Exception:
                pass
        return None

    def _get_prompts(self) -> dict[str, str]:
        prompts = self.curated_store.get_prompts()
        return {
            "designer": prompts.get("designer") or DEFAULT_DESIGNER_PROMPT,
            "reviewer": prompts.get("reviewer") or DEFAULT_REVIEWER_PROMPT,
        }

    async def _call_llm(self, prompt: str, provider_id: str | None) -> str:
        """调用普通 LLM provider。复用 dayflow service 的调用模式。"""
        effective_id = self._resolve_provider_id(provider_id)
        if not effective_id:
            raise RuntimeError("未配置设计师/审核师提供商，也无法获取默认提供商")
        prov = None
        try:
            prov = self.context.get_provider_by_id(effective_id)
        except Exception:
            prov = None
        original_timeout = None
        if prov and self._llm_timeout_seconds > 0:
            client = getattr(prov, "client", None)
            if client and hasattr(client, "timeout"):
                original_timeout = client.timeout
                client.timeout = self._llm_timeout_seconds
        try:
            llm_resp = await self.context.llm_generate(chat_provider_id=effective_id, prompt=prompt)
        finally:
            if original_timeout is not None and prov is not None:
                client = getattr(prov, "client", None)
                if client and hasattr(client, "timeout"):
                    client.timeout = original_timeout
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

    # ------------------------------------------------------------------
    # 设计师：首次设计
    # ------------------------------------------------------------------
    async def design(
        self,
        style_name: str,
        user_input: str | None = None,
        max_existing_for_anti_repetition: int = 5,
    ) -> dict[str, Any]:
        """让设计师创作一套穿搭。

        Returns:
            {"success": bool, "name": str, "description": str, "error": str}
        """
        style_name = str(style_name or "").strip()
        if not style_name:
            return {"success": False, "error": "风格名不能为空"}
        # 拉取已有优秀设计作为防重复参考
        existing = self.curated_store.get_outfits(style_name)
        if existing and len(existing) > max_existing_for_anti_repetition:
            # 按 use_count 降序取最近常用的作为参考（避免注入过多上下文）
            existing = sorted(existing, key=lambda x: -int(x.get("use_count") or 0))[:max_existing_for_anti_repetition]
        prompts = self._get_prompts()
        prompt = _format_designer_prompt(
            prompts["designer"],
            style_name=style_name,
            existing_outfits=existing,
            user_input=user_input,
        )
        logger.info(f"[dayflow-设计师] 触发设计: style={style_name}, has_user_input={bool(user_input)}, existing_count={len(existing)}")
        try:
            raw = await self._call_llm(prompt, self.designer_provider_id)
        except Exception as e:
            logger.warning(f"[dayflow-设计师] LLM 调用失败: {e}")
            return {"success": False, "error": f"LLM 调用失败：{e}"}
        if not raw:
            return {"success": False, "error": "LLM 返回为空"}
        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning(f"[dayflow-设计师] 输出解析失败: {raw[:300]}")
            return {"success": False, "error": "LLM 输出无法解析为 JSON 对象", "raw": raw}
        name = str(parsed.get("name") or "").strip()
        description = str(parsed.get("description") or "").strip()
        if not name or not description:
            return {"success": False, "error": "LLM 输出缺少 name 或 description 字段", "raw": raw}
        logger.info(f"[dayflow-设计师] 设计完成: style={style_name}, name={name}, desc_len={len(description)}")
        return {"success": True, "name": name, "description": description}

    # ------------------------------------------------------------------
    # 审核师：迭代修改
    # ------------------------------------------------------------------
    async def review(
        self,
        style_name: str,
        original_name: str,
        original_description: str,
        user_feedback: str,
    ) -> dict[str, Any]:
        """让审核师基于原设计与用户意见产出修改后的方案。

        Returns:
            {"success": bool, "name": str, "description": str, "error": str}
        """
        style_name = str(style_name or "").strip()
        original_name = str(original_name or "").strip()
        original_description = str(original_description or "").strip()
        user_feedback = str(user_feedback or "").strip()
        if not style_name or not original_name or not original_description:
            return {"success": False, "error": "风格名、原款式名、原描述均不能为空"}
        if not user_feedback:
            return {"success": False, "error": "用户修改意见不能为空"}
        prompts = self._get_prompts()
        prompt = _format_reviewer_prompt(
            prompts["reviewer"],
            style_name=style_name,
            original_name=original_name,
            original_description=original_description,
            user_feedback=user_feedback,
        )
        logger.info(f"[dayflow-审核师] 触发审核: style={style_name}, original={original_name}, feedback_len={len(user_feedback)}")
        try:
            raw = await self._call_llm(prompt, self.reviewer_provider_id)
        except Exception as e:
            logger.warning(f"[dayflow-审核师] LLM 调用失败: {e}")
            return {"success": False, "error": f"LLM 调用失败：{e}"}
        if not raw:
            return {"success": False, "error": "LLM 返回为空"}
        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning(f"[dayflow-审核师] 输出解析失败: {raw[:300]}")
            return {"success": False, "error": "LLM 输出无法解析为 JSON 对象", "raw": raw}
        name = str(parsed.get("name") or "").strip()
        description = str(parsed.get("description") or "").strip()
        if not name or not description:
            return {"success": False, "error": "LLM 输出缺少 name 或 description 字段", "raw": raw}
        logger.info(f"[dayflow-审核师] 审核完成: style={style_name}, name={name}, desc_len={len(description)}")
        return {"success": True, "name": name, "description": description}
