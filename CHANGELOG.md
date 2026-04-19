# Changelog

## v1.2.2 - 2026-04-19

**✨ 新增定制日程功能**

**1. 🎯 新增 `/定制日程` 命令**

* 用户可通过 `/定制日程 <要求>` 命令，在生成日程时注入额外的定制要求。
* 例如：`/定制日程 今天穿洛丽塔，下午约会`、`/定制日程 今天穿杏花微雨`。

**2. 🧠 意图解析机制**

* 当用户使用 `/定制日程` 时，Grok 会在风格研究的同时解析用户意图。
* 支持识别并覆盖：
  - `outfit_style`：穿搭风格大类（优先使用池中相近值）
  - `outfit_item`：具体单品名（如"杏花微雨"）
  - `schedule_main_type`：日程主线类型
  - `core_event_driver`：核心事件驱动
  - `outfit_adjustments`：穿搭调整（如"第二套下身换裤子"）

**3. 🔄 风格研究自动重搜索**

* 当用户指定的风格与随机抽取的不同时，会自动用新风格重新搜索风格参考。
* 例如：随机抽到"女仆装"，用户说"穿杏花微雨"，Grok 会重新搜索"杏花微雨"的风格信息。

**4. 📝 Prompt 注入优化**

* 用户定制要求会清晰地注入到最终生成的 Prompt 中。
* 明确标注穿搭风格、具体单品、穿搭调整等信息。

**5. ✅ 校验兼容**

* `validate_payload` 支持放行用户指定的池外风格。
* 通过 `user_specified_outfit_style` 标记区分用户指定和随机抽取。

**6. ⚙️ 新增配置项**

* `custom_schedule_intent_append`：定制日程意图解析追加模板，高级用户可自定义。

**7. 🔧 代码变更**

* `main.py`：新增 `/定制日程` 命令，`_generate_for_event` 支持 `extra_requirement` 参数。
* `service.py`：新增 `CUSTOM_SCHEDULE_INTENT_APPEND` 常量，`_research_style_reference` 支持意图解析，`generate_schedule` 支持意图覆盖和 Prompt 注入。
* `generator.py`：`validate_payload` 放行用户指定风格。
* `_conf_schema.json`：新增 `custom_schedule_intent_append` 配置项。

---

## v1.2.1 - 2026-04-19

**🐛 修复日程未注入系统提示词的遗漏问题**

**1. ✅ 新增 `on_llm_request` 钩子**

* 修复了设计之初就应存在但遗漏实现的功能：将今日日程注入到 LLM 的 system prompt 中。
* 现在 Bot 在回复时会"带着今天的生活上下文"说话，而不仅仅是套用人设。
* 注入内容包含：今日穿搭 + 今日日程。

**2. 🔄 注入清理机制**

* 每次请求前会先清理上次注入的内容，避免日程在对话历史中累积。
* 使用 `<DayFlow-Schedule>` 标签包裹注入内容，与 livingmemory 的 `<RAG-Faiss-Memory>` 标签互不干扰。

**3. 🔧 代码变更**

* `main.py`：新增 `on_llm_request()` 钩子、`_build_injection_text()` 和 `_remove_dayflow_injection()` 辅助函数。

---

## v1.2.0 - 2026-04-19

**🧭 日程输出格式重构与提示词全面优化**

**1. 🏗️ 日程输出格式从字符串改为结构化 timeline 数组**

* JSON 输出结构从 `{outfit_style, outfit, schedule}` 改为 `{outfit_style, outfit, summary, timeline[]}`
* `timeline` 数组中每个对象包含 `time_start`、`time_end`、`title`、`detail`、`outfit_change` 五个字段
* 新增 `summary` 字段：25字内概括今天的主题和心情
* 新增 `title` 字段：每个时段必须有创意标题，用诗意或生动的短语概括
* `schedule` 字符串字段保留，由 `timeline` 数组自动合成，兼容 DayMind 等下游插件

**2. 🧠 人设脱钩声明**

* 明确 persona_desc 中的对话约束（字数限制、标点限制等）不适用于日程生成
* 解决月见雪等角色人设污染日程叙述的问题（如20字限制导致日程事件也变短、标点自由导致全用空格）

**3. 🚫 禁止元标签泄漏**

* 禁止在 title/detail 中出现"核心事件"、"主线事件"、"XX驱动"等构思逻辑标签
* 这些是内部构思依据，不是日程内容，不得泄露到输出文本中

**4. ✨ 事件创造性要求**

* 新增正面引导：每个非固定事件应有记忆点（小插曲/小转折）
* 核心主线事件应有完整的起承转合：触发→发展→高潮→收束
* 禁止两个以上连续时段都是被动消磨型内容

**5. 👗 换装差异要求**

* 两次穿搭须在同一风格体系下呈现明显差异：单品选择、配色深浅、层次搭配、版型轮廓等至少两项不同
* 措辞改为性别中立，兼容男性角色

**6. 📊 优先级体系重构**

* 消除"必须严格遵循"通胀，统一为三层优先级：格式合规 > 人设脱钩 > 内容质量

**7. 🔁 反重复规则优化**

* 日记参考明确"只取连续性不取结构"，禁止复用日记中的事件序列和场景组合
* 日记是主观感受，日程是客观记录，两者体裁不同

**8. 🎲 随机度定义重写**

* 从"和前几天有多不同"改为"今天有多大事发生"
* 低=普通日常，中=小插曲日，高=重要事件日
* 去掉具体例子避免提示词污染

**9. 🌐 联网风格研究提示词重构**

* SYSTEM_PROMPT：新增"工作方式"段落，穿搭建议要求包含搭配理由，新增 `difference` 字段明确两套穿搭差异，删除"无法稳定输出 JSON 则允许纯文本"兜底
* QUERY_TEMPLATE：从7条指令性文本改为简洁搜索关键词，让搜索词和指令各司其职
* 渲染逻辑：`_render_style_reference` 新增渲染 `difference` 字段，穿搭建议条数上限从6提升到8

**10. 🔧 代码变更**

* `generator.py`：新增 `_synthesize_schedule_from_timeline()` 函数（兼容 DayMind），`normalize_payload` 自动合成 schedule，`validate_payload` 支持 timeline 数组校验，`extract_timeline` 支持从 dict 或 string 提取，`build_repair_prompt` 适配新格式
* `service.py`：返回值新增 `summary` 和 `timeline` 字段，`_render_style_reference` 新增 difference 渲染，`prompt_template_version` 升级为 `persona_full_template_v9_timeline_struct`
* `main.py`：新增 `_render_schedule_display()` 渲染函数，优先用 timeline 数组渲染（含标题），回退到 schedule 字符串
* `_conf_schema.json`：同步更新 `style_research_system_prompt` 和 `style_research_query_template` 的 default 值

---

## v1.1.2 - 2026-04-18

**🔧 无限重试修复与自动Fallback机制**

**1. 🐛 修复自动调度无限重试Bug**

* 之前自动调度失败时，`generate_schedule` 内部会重试N次LLM调用，调度器本身又会重试N次，导致指数级重试（如9×9=81次请求）。
* 现在新增 `auto_retry` 参数，自动调度场景下禁用 `generate_schedule` 内部重试，由调度器统一管理重试次数。
* 手动触发（`/生成日程`）仍保留完整重试机制。

**2. 🔄 修复空结果不触发Fallback的Bug**

* 之前 `call_llm_with_provider_fallback` 在 primary provider 返回空结果时不会触发 fallback，直接返回空。
* 现在无论异常还是空结果，都会正确切换到 fallback provider。

**3. 🌐 新增自动Fallback到聊天模型机制**

* 当配置的专家模型重试全部失败后，自动切换到聊天模型（session provider 或默认 provider）继续重试。
* 重试流程：Primary provider重试N次 → Fallback provider重试N次 → 全部失败则停止。
* 自动调度场景（无session）会 fallback 到 astrbot 默认聊天 provider。
* 手动触发场景会 fallback 到当前会话使用的聊天模型。

**4. 📊 增强Meta信息**

* 新增 `default_provider_id` 字段，记录使用的默认 provider。
* `fallback` 字段现在会正确反映是否使用了 fallback provider。
* 日志新增 `fallback_used`、`default_provider` 等调试信息。

**5. 🏗️ 新增 `_get_default_provider_id` 方法**

* 支持多种 astrbot API 获取默认 provider 的方式。
* 为自动调度场景提供 fallback 目标。

---

## v1.1.1 - 2026-04-08

**🛠️ 联动读取与重复生成修复**

**1. 📅 新增目标日期读取语义**

* `get_life_context()` 与 `generate_schedule()` 现支持按 `target_date` 读取 / 生成日程。
* 解决了跨日场景下，外部插件为某一天生成内容时仍被迫按系统"今天"取日程的问题。

**2. 🔗 与 DayMind 的跨日联动修复**

* 配合 DayMind 调整后，前一日日记可稳定读取对应日期日程。
* 避免出现前一日日记生成过程中误判"今日日程不存在"而重复补生成的问题。

**3. ♻️ `/查看日程` 重复生成防抖**

* 命令路径改为统一经过 DayFlow 上下文读取接口判断是否已有今日日程。
* 降低已有缓存却仍误触发再次生成、导致同日结果被重复覆盖的概率。

---

## v1.1.0 - 2026-04-07

**🌐 Grok 联网风格研究接入**

**1. ✅ 新增 Grok 风格研究链路**

* 现在 DayFlow 可接入 `astrbot_plugin_grok_web_search`，在生成日程前先对当前抽中的穿搭风格做一次联网研究。
* 这一步不是为了单纯扩写内容，而是为了尽量减少模型对细分穿搭风格的误解。
* 尤其适合名称容易被字面误读、容易与相近风格混淆、或本身带有较强圈内定义的风格词。

**2. 🧠 新增风格研究结构化约束**

* Grok 返回结果会优先按结构化方式约束后续生成，重点覆盖：
  - 风格定义
  - 核心识别点
  - 晨间第一套建议
  - 午后第二套换装建议
  - 常见误判与禁区
* 目标是让主模型在生成穿搭与日程时，优先服从联网研究结果，而不是按字面联想自由发挥。

**3. ♻️ 新增风格研究缓存与降级机制**

* 相同穿搭风格的研究结果现在会写入本地缓存，避免重复联网搜索。
* 若 Grok 结果无法稳定解析为 JSON，会自动降级为纯文本摘要继续参与生成。
* 若未安装 Grok 搜索插件，DayFlow 仍可正常运行，只是不会启用联网风格研究。

**4. 🔍 调试信息补充风格研究视角**

* `/dayflow_debug` 现在可查看风格研究相关调试信息。
* 包括缓存命中、查询预览、原始返回预览、解析结果预览与来源预览。
* 便于排查风格理解跑偏、搜索质量不足或研究结果不稳定的问题。

**5. 🛠️ schema 暴露 Grok 提示词模板**

* 现在已把 Grok 风格研究使用的 system prompt 与 query 模板暴露到 schema。
* 高级用户可直接在配置中自定义风格研究的输出结构、研究重点与检索措辞。
* 若自定义模板格式错误，会自动回退到内置 query 模板；但模板写坏仍可能影响研究质量，需自行确认。

**6. 📝 README 与推荐搭配插件说明更新**

* README 已补充 Grok 联网风格研究说明。
* 推荐搭配插件新增：
  - `astrbot_plugin_daymind`
  - `astrbot_plugin_grok_web_search`
* 同时补充了新增 schema 配置项与高级定制说明。

---

## v1.0.0 - 2026-04-05

**🌸 结构性优化**

**1. ✅ 人格启用校验变严格**

* 之前未配置的人格也能走生成流程，会偷偷按默认人格兜底。
* 现在新增 `is_persona_configured()` 校验：只有已在 DayFlow 中启用的人格才会生成日程。
* 未配置人格会直接拒绝，返回"人格未在 Dayflow 中启用"。

**2. 🔄 `/生成日程` 改为强制重生成**

* 之前 `/生成日程` 会先看当天缓存，有缓存就直接返回。
* 现在明确区分：
  - `/查看日程`：有缓存则直接返回，没有才生成。
  - `/生成日程`：强制重生成当天日程，忽略已有缓存。
* 提示文案也区分成"正在生成"和"正在强制重生成"。

**3. 📅 `get_life_context()` 只返回今日日程**

* 之前如果今天没有日程，会回退到最新历史日程。
* 现在严格改为：只返回今天的有效日程。
* 如果今天没有有效日程，会返回"今日尚无有效日程，已拒绝回退到旧日程"，不再伪装。

**4. ⏰ 自动调度新增 trigger 消费状态**

* 之前只在固定分钟命中时触发，错过就可能跳过。
* 现在改为 `now_minutes >= trigger_minutes` 即可触发。
* 新增 `trigger_key = YYYY-MM-DD@HH:MM`，每个触发点每天只消费一次。
* 已有今日日程时会把 trigger 消费掉，生成失败则不消费，后续可继续重试。

**5. 🔗 自动生成可绑定最近活跃会话**

* 自动生成时会尝试从 DayMind 的消息缓存和会话-人格映射中找到该人格最近活跃的会话。
* 让生成的日程更贴近真实互动历史，而不是无来源硬生成。

**6. 🎲 新增日程变化等级系统**

* 新增 `schedule_variation_level` 配置，支持"低 / 中 / 高 / 随机"。
* "随机"按 3:6:1 比例抽取低/中/高。
* 用于控制今天和前几天的差异程度，避免连续几天结构过于重复。

**7. 🎯 日程约束升级为双轴结构控制**

* 约束池现在升级为：
  - `schedule_main_types`：今日总体节奏（如日常常规型、外出探索型、居家慵懒型等）。
  - `core_event_drivers`：今日主要被什么推动（如任务驱动、情绪驱动、社交驱动等）。
* 生成校验和 Prompt 约束都改为双轴控制，让生成结果更稳、变化度更可控。

**8. 🔀 Provider 调用支持主备回退**

* 新增 `call_llm_with_provider_fallback()`：
  - `primary` = 人格配置 provider 或会话 provider。
  - `fallback` = 会话 provider（当人格配置 provider 存在且与会话 provider 不同时）。
* 主 provider 失败时可自动回退到会话 provider，提高稳定性。

**9. 📦 历史存储从轻量文本升级为完整 payload**

* 之前历史日程只存 `date` / `outfit` / `schedule` 轻量字段。
* 现在改为存储完整 payload，包括 `meta` / `timeline` / `weather` / `memo` 等。

**10. 📝 新增近三日结构摘要参考**

* 除了历史日程原文，还会提取近几天的前几行结构，形成摘要。
* 帮模型理解最近几天的整体编排习惯，让变化等级机制更有抓手。

---

## v0.2.3 - 2026-04-01

### Fixed
- 修复 `/查看日程` 在部分人格场景下无法正确命中当天缓存、反而重复触发生成的问题
- 修复不同人格显示名 / 配置名不一致时，日程读写键不统一导致的缓存失效问题
- 修复生成流程中"已经有缓存却仍先提示正在生成"的体验问题
- 修复穿搭字段 `outfit` 第一行格式在部分模型输出下连续校验失败的问题
- 增加本地 payload 规范化逻辑：当模型输出 `【风格】:`、`穿搭风格：`、`风格为：` 等变体时，会优先自动矫正为 `风格：xxx`
- 修复重写提示词构造中的字符串问题，降低 repair prompt 异常风险

### Changed
- 配置页中的"默认 Prompt 模板"改为 Markdown 编辑器模式，支持更长文本、更清晰的结构化编辑
- 人格级 `prompt_template` 也改为 Markdown 编辑器模式，便于单独定制
- 默认天气池移除温度信息，改为仅保留纯天气描述，避免默认值过于僵硬
- 优化部分 schema 文案与提示信息，提升配置可理解性
- README 恢复为原有展示风格，并同步保留本次配置项说明更新

### Docs
- 补充 `CHANGELOG.md`，记录本次修复与配置项调整
