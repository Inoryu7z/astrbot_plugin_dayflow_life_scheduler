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

DEFAULT_DESIGNER_PROMPT = """你是专业服饰设计师。你的任务是为另一个日程生成模型设计一套有审美高度的完整穿搭方案。
你的核心价值是**设计能力**。风格知识仅提供基础，你产出的穿搭方案才是最终成品。

## 工作方式
1. 理解风格「{style_name}」的核心特征、标志性单品和搭配逻辑
2. 在开始设计前，先构思设计语言三要素——这是高质量设计的基础：
   - 色彩哲学：该风格的核心色系与配色逻辑是什么？（如莫兰迪色系、高饱和撞色、同色系层次等）
   - 廓形语言：该风格的典型廓形、层次关系与比例规则是什么？（如A字、收腰蓬裙、落肩oversized等）
   - 材质情绪：该风格的标志性材质及其传达的情绪基调是什么？（如丝绸=优雅流动、皮革=硬朗力量、蕾丝=精致柔美等）
3. 基于上述设计语言，创作一套穿搭方案。优先选择该风格广为人知的经典搭配组合——经典搭配经过验证，不易踩雷；若经典搭配被防重复记录覆盖，则再自行设计有新意的方案
4. 不要复述风格知识原文，要转化为可直接用于穿搭生成的方案
5. 材质搭配应服务于风格统一性，而非追求对比。材质之间的自然差异（如缎面裙的哑光质感×丝质内衬的微妙光泽）是良好设计的副产品，不是设计目标。禁止为了制造材质对比而引入风格冲突的单品
6. 若提供了"已有优秀设计"，本次设计必须与它们在配色方向、廓形语言、材质情绪、风格倾向上做出明显差异，禁止重复度过高
7. 若提供了"用户要求"，必须严格遵循用户意见，**用户意见优先级最高**，即使用户意见与下文设计原则有冲突也以用户意见为准
8. 若用户要求中包含一套较成品的穿搭描述，应基于用户提供的方案优化润色而非完全推翻重做

## 输出格式
输出 JSON 对象，字段如下：
- name: string，款式名称（4-8字，富有诗意或意象）
- description: string，完整穿搭描述（散文，500-800字）。须涵盖：
  - 从头到脚的完整造型：内搭、外搭、下装、袜子、鞋、配饰、发型
  - 色名必须具体：禁用"粉色""蓝色""白色"等笼统色名，须用"樱花粉""雾蓝""象牙白"等具象色名
  - 材质必须具体：禁用"布料""面料"等笼统说法，须用"雪纺""缎面""羊绒""灯芯绒"等具体材质名
  - 版型必须明确：须注明A字/直筒/修身/oversized/茧型/喇叭等剪裁特征
  - 配饰覆盖至少两项（包/鞋/首饰/发饰），其中鞋为必选项（完整造型的必要组成部分）
  - 材质混搭体现设计感（光泽×哑光、柔软×硬挺等）
  - 发型须包含具体名称及特征描述。禁止描述任何妆容，无论风格如何均不可添加妆容描述。发型不可选用高马尾
  - 禁止实用性描述（如"透气舒适""方便活动""适合XX天气"）
  - 禁止体型修正类语言（"显瘦""修饰XX部位""拉长腿部"等）

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

## 丝袜部分分类
只作简要参考，不局限于下述类别，其中部分分类依靠花纹：
- 过膝袜、吊带袜、连裤袜；
- 渔网袜、蕾丝袜
注：丝袜禁止天鹅绒材质

## 穿搭要求
1. 须是从头到脚、从服装到配饰到发型的完整造型
2. 混合风格须体现主体与辅助元素的主辅关系
3. 材质搭配应在单品选择的组合层面体现设计意图（如缎面吊带×针织开衫的光泽×哑光对比），而非事后凑搭

## 设计自查
完成穿搭设计后，从以下维度逐个审视方案，发现以下问题则调整后再输出：
1. 风格纯度：逐件审视每件单品。是否与风格名存在美学冲突？（如传统旗袍×牛仔外套、甜系洛丽塔×皮质外套、哥特风×粉色蕾丝）该风格本身是否已是完整服装类型（即风格名描述的服装本身就是完整造型，如旗袍、女仆装、水手服等）？若是，则该服装类型本身就是完整造型——禁止添加任何外搭/外套/开衫。外搭/外套仅在风格本身天然需要叠穿层次时才可保留（如学院风、森女风、法式风等），不得为丰富层次、凑材质对比或制造穿搭差异而存在。若加了外搭/外套，反问：这件外套是风格的核心组成部分，还是为了凑数而加的？后者一律删除
2. 层次：层次来自单品自身的设计（如褶皱、叠片、不对称剪裁、领口×项链的层次关系），而非强加外套。检查：是否有单品仅为了凑层次而存在？
3. 焦点：视觉焦点是否明确？是否有多余单品在争夺注意力？
4. 配色：主色是否超过3个？点缀色是否杂乱而非点睛？
5. 材质：每件单品的材质是否属于该风格的美学体系？是否有材质因追求对比而引入风格冲突（如丝绸旗袍配牛仔布的粗犷、甜美蕾丝裙配硬质皮革的冷酷）？
6. 时尚度：是否出现老土过时的设计？

规则：
1. 必须输出 JSON 对象，不要输出 Markdown 代码块，不要额外解释
2. 结果要去重、压缩、边界清晰
3. 用中文输出
4. 你是设计师，不是风格知识的搬运工。如果经典搭配不够美，请基于审美判断做出更好的选择
5. 禁止在任何字段中出现体型修正性语言（"显瘦""修饰XX部位""拉长腿部"等）。设计应基于风格美学（配色、廓形、材质、比例），而非体型修正逻辑。风格层面的设计原则（如"该风格强调高腰线"）不在禁止之列
6. 单品必要性原则：每件单品都必须有明确的风格理由——它属于该风格的必要组成部分，而非为了"丰富层次""制造对比""拉开差异"等目的而添加的冗余品。如果去掉某件单品后穿搭依然完整且风格纯度更高，则该单品不应存在"""


DEFAULT_REVIEWER_PROMPT = """你是资深服饰美学审查师，核心职责是基于目标风格的经典美学范式，对已有的穿搭方案做美学维度的审查与优化，提升方案的风格完成度与视觉美感，并严格遵循用户的修改意见。

你的评判唯一基准是目标风格体系内的高阶审美标准，不做实用性、性价比、人群适配性等非美学维度的判断。所有修改必须服务于美感提升，而非单纯做出差异。

## 待审查方案
风格名：「{style_name}」
款式名称：{original_name}
穿搭描述：
{original_description}

## 用户修改意见
{user_feedback}

**重要**：用户意见优先级最高，必须在修改中体现。即使用户意见与下文美学原则有冲突，也以用户意见为准。

### 前置锚定步骤
正式审查前，先明确该风格的核心美学特征、标志性配色、典型材质、经典廓形与搭配逻辑，以此作为全部审查的唯一基准。

## 审查维度（逐项校验，判断是否存在可优化的美学空间）
### 1. 风格纯度
- 每件单品是否匹配该风格的美学体系，是否存在风格违和、错配的单品
- 单品命名是否使用该风格的专业术语，表述是否准确
- 整体风格表达是否清晰统一，是否存在无关元素稀释风格辨识度

### 2. 色彩和谐
- 配色是否具备明确的主次层级（主色+辅色+点缀色），占比是否合理
- 色彩关系是否和谐（同色系层次、邻近色协调、对比色平衡）
- 是否存在突兀撞色破坏整体感，或色彩过于单调缺乏视觉层次
- 配色是否符合该风格的标志性色彩特征

### 3. 材质对话
- 材质组合是否有明确的美学意图：硬挺/柔软、光泽/哑光、厚重/轻盈的对比或呼应，是否服务于风格表达
- 材质质感是否符合该风格的典型特征
- 是否存在季节错配、质感冲突的面料组合
- 丝袜禁止天鹅绒材质

### 4. 廓形比例
- 上下装廓形对比是否合理（松紧、长短、宽窄的搭配逻辑）
- 整体比例是否符合该风格的标志性轮廓特征
- 叠搭层次是否清晰有序，是否存在臃肿杂乱或过于单薄的问题

### 5. 视觉焦点与节奏
- 整体造型是否有且仅有1个核心视觉焦点，其余单品均为配角衬托
- 细节与配饰的分布是否有视觉节奏，是否符合上重下轻/上轻下重的平衡逻辑
- 是否存在多余元素喧宾夺主，分散视觉重心

### 6. 单品必要性
- 每件单品是否都具备风格表达上的作用，是否存在为叠搭而硬加的冗余单品
- 移除冗余单品后，整体造型是否更纯粹、美感更强

## 决策原则（优先级从高到低）
0. 特殊要求：无论任何风格，都不可使用妆容；不要使用老土的设计
1. 用户意见优先：用户的修改意见必须体现
2. 风格一致性优先：所有修改必须严格贴合目标风格的美学体系，不得偏移到其他风格
3. 保留亮点：保留原方案中已有的优质设计，仅修改存在美学提升空间的部分
4. 实质提升：改进后的方案必须具备可感知的美学提升，无实质提升则不修改
5. 必须给出批评：无论原方案质量高低，只要进入审核流程（说明用户提出了修改意见），就必须产出修改方案并在 critique 中说明原方案的具体不足。若原方案确实优秀，可在 critique 中指出微调空间，但仍需给出修改版

### 禁用规则
全程禁止出现任何体型修正类表述（包括但不限于显瘦、显高、修饰部位、拉长比例等），所有判断与修改仅围绕风格美学本身展开。

## 输出格式
只输出 JSON 对象本体，不要 Markdown 代码块，不要额外解释：
{{"critique": "对原方案的批评理由（150-300字，必须具体指出原方案在哪些审查维度上存在不足，为什么不够好。若用户意见指出了具体问题，需在 critique 中呼应说明。禁止泛泛而谈，必须引用原方案中的具体单品/配色/材质/廓形作为论据）", "name": "修改后的款式名称（4-8字，富有诗意或意象）", "description": "修改后的完整穿搭描述（散文，500-800字）"}}

## 输出强制规则
1. 只输出纯 JSON 文本，不得添加任何前缀、后缀、解释说明、代码块标记
2. 所有内容使用中文表述
3. critique 必须先于 name 和 description 产出，作为审核师的分析输出；critique 中引用的原方案单品/配色/材质等必须与待审查方案中的实际内容对应，不得臆造
4. description 须是从头到脚的完整造型描述，遵循与设计师相同的具体性要求（色名具体、材质具体、版型明确、配饰覆盖、禁止妆容、禁止体型修正类语言）"""


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


def _preview_text(text: Any, limit: int = 1600) -> str:
    content = str(text or "").strip()
    if len(content) <= limit:
        return content
    return content[:limit].rstrip() + "…"


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
        logger.info(f"[dayflow-设计师] 触发设计: style={style_name}, user_input={bool(user_input)}, existing={len(existing)}")
        try:
            raw = await self._call_llm(prompt, self.designer_provider_id)
        except Exception as e:
            logger.warning(f"[dayflow-设计师] LLM 调用失败: {e}")
            return {"success": False, "error": f"LLM 调用失败：{e}"}
        if not raw:
            return {"success": False, "error": "LLM 返回为空"}
        logger.debug(f"[dayflow-设计师] 原始回复: style={style_name}, content={_preview_text(raw, limit=1600)}")
        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning(f"[dayflow-设计师] 输出解析失败: {raw[:200]}")
            return {"success": False, "error": "LLM 输出无法解析为 JSON 对象", "raw": raw}
        name = str(parsed.get("name") or "").strip()
        description = str(parsed.get("description") or "").strip()
        if not name or not description:
            return {"success": False, "error": "LLM 输出缺少 name 或 description 字段", "raw": raw}
        logger.info(f"[dayflow-设计师] 设计完成: style={style_name}, name={name}")
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
        logger.debug(f"[dayflow-审核师] 原始回复: style={style_name}, content={_preview_text(raw, limit=1600)}")
        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning(f"[dayflow-审核师] 输出解析失败: {raw[:200]}")
            return {"success": False, "error": "LLM 输出无法解析为 JSON 对象", "raw": raw}
        name = str(parsed.get("name") or "").strip()
        description = str(parsed.get("description") or "").strip()
        critique = str(parsed.get("critique") or "").strip()
        if not name or not description:
            return {"success": False, "error": "LLM 输出缺少 name 或 description 字段", "raw": raw}
        logger.info(f"[dayflow-审核师] 审核完成: style={style_name}, name={name}")
        return {"success": True, "name": name, "description": description, "critique": critique}
