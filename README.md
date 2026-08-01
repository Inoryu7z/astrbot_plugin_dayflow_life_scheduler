[![DayFlow Counter](https://count.getloli.com/get/@Inoryu7z.dayflow?theme=miku)](https://github.com/Inoryu7z/astrbot_plugin_dayflow_life_scheduler)

# 🌸 DayFlow · 心迹日程

给 Bot 一条贴近今天的生活轨迹，让它在开口之前，就先知道自己今天正在过怎样的一天。

**DayFlow** 是一个多人格日程生成插件。
它会为当前人格生成一份带有**穿搭、生活节奏与时间线**的今日日程，让 Bot 在回复时不只是套用人设，而是带着"今天的生活上下文"来说话。

更新日志：[CHANGELOG.md](https://github.com/Inoryu7z/astrbot_plugin_dayflow_life_scheduler/blob/main/CHANGELOG.md)

---

## ✨ 核心特性

### 👗 生成今日穿搭与日程

为每个persona生成一份结构化的今日生活安排，包含 `outfit`（穿搭描述）与 `schedule`（时间线安排）两部分。

生成时会综合参考：当前日期、人格设定、今日天气、穿搭风格、日程主线类型、核心事件驱动、近日日程历史、近期对话、DayMind 提供的前几日日记、Grok 联网风格研究结果。

### 🎨 优秀穿搭库 webui 设计系统

把"服装设计"从**运行时由 LLM 现想**提前到**手动 webui 中提前设计并入库**，让穿搭从"模型即兴发挥"变成"策展式挑选"。

内置 **Plugin Pages** webui（AstrBot 0.5.x+ 支持），打开后可见四个 tab：

- **📐 设计**：手动发起一次穿搭设计。可指定人格名、风格（如"洛丽塔"/"废土风"/"杏花微雨"）、主题描述、晨间/午后单品（留空则模型自由发挥）、调用提供商。设计完成后以"待审核"状态写入优秀库。
- **⭐ 优秀库**：浏览已入册的穿搭条目，支持分级管理（`starred` 标星收藏款 / `normal` 经典款，starred 在自动生成时拥有更高被抽中权重）；每条都包含晨间与午后两套完整搭配，符合双套换装结构；可点击"立即使用此条目生成今日日程"跳过风格池抽取与 Grok 研究直接使用；支持编辑分级、备注、单品描述与删除。
- **📝 提示词**：管理设计 tab 调用 LLM 时使用的 system / user prompt 模板，内置默认模板覆盖风格识别、晨午双套约束、单品颜色材质要求等维度，可自定义适配不同模型家族。
- **📊 概览**：查看优秀库统计——各风格条目数量分布、starred/normal 比例、最近入库与被引用时间、各人格入库数。

**与运行时生成的关系**：webui 是可选工作流，未启用时 DayFlow 仍走原有的"运行时 LLM 现想"逻辑。已入册条目会作为优先抽取源参与风格池抽取，starred 权重更高；也可关闭自动抽取强制只从优秀库挑选。

### 🎭 穿搭风格池

内置了丰富的默认风格池（中华风洛丽塔、甜系洛丽塔、改良韩服温柔风、芭蕾少女风、法式优雅风、旗袍、巫女服、花嫁、cosplay 等数十种），覆盖日常、甜美、纯欲、古典、二次元等主要方向。

所有风格都支持在配置中自由增删，也可通过 webui 设计 tab 提前设计特定风格的穿搭入库。

### 🛡️ 风格研究防跑偏

穿搭风格的名称往往比模型想象的更具体（"洛丽塔"≠"洛丽塔风格连衣裙"、"杏花微雨"是个意境词不是字面描述）。DayFlow 提供两层防跑偏机制：

- **Grok 联网风格研究**：若安装了 `astrbot_plugin_grok_web_search`，会在生成前对当前抽中的风格做一次联网研究，补足风格定义、核心识别点、误判禁区与同风格换装边界，再交给主模型生成穿搭与日程。带本地缓存避免重复搜索。
- **风格研究二次审查**：开启 `enable_style_review` 后，研究完成后会调用第二次 LLM 对研究结果进行七维审查（风格纯度、色彩和谐、材质对话、配饰逻辑、场景适配、晨午连贯、避雷命中），修正明显跑偏的研究结论。审查 LLM 可独立配置，失败时自动降级为使用原始研究结果。

同时具备**风格防重复**机制：检测到同一风格最近多次出现时，会要求获取历史穿搭的具体描述，让主模型基于历史做差异化设计，避免连续几天输出雷同的配饰、色彩、单品搭配。

### 🎲 日程变化与双轴结构

- **变化等级**：`schedule_variation_level` 支持"低 / 中 / 高 / 随机"，控制今天与前几天的差异程度，"随机"按 3:6:1 比例抽取，避免连续几天结构过于重复。
- **双轴约束**：`schedule_main_types`（今日总体节奏，如日常常规型、外出探索型、居家慵懒型）+ `core_event_drivers`（核心事件被什么推动，如任务驱动、情绪驱动、社交驱动），让日程不只是一段时间表，而是一个有方向的一天。

### ⏰ 自动定时生成与活跃会话绑定

- 按人格设置 `generate_time`，到点后自动为该人格生成当日日程，每个人格独立定时、同一天只生成一次。
- 自动生成时会尝试从 DayMind 的消息缓存和会话-人格映射中找到该人格最近活跃的会话，让生成的日程更贴近真实互动历史，而不是无来源硬生成。

### 🔗 日程细分（sub_events）

开启 `enable_subdivision` 后，日程生成成功时会复用同一套提供商逻辑将大骨架时段拆分为更细粒度的活动片段（如"下午出门逛街"→"在咖啡厅点了一杯手冲"）。细分骨架用于系统提示词注入和 DayMind 思考，让 Bot 的回应与内心活动都能落到具体经历上，而不是泛泛复述大骨架。

### 💾 本地持久化

已生成的日程保存到本地目录，重启后仍可读取。默认保留最近 3 天，可通过 `schedule_retention_days` 修改，填 `-1` 表示不限天数。

---

## 🧩 推荐搭配插件

- [`astrbot_plugin_daymind`](https://github.com/Inoryu7z/astrbot_plugin_daymind)：提供近期对话、近日日记、最近活跃会话等连续生活参考；DayFlow 也会把穿搭与日程喂给 DayMind 作为思考素材
- [`astrbot_plugin_grok_web_search_Inoryu7z`](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search_Inoryu7z)：提供联网风格研究，降低模型误解穿搭风格的概率

DayFlow 也可独立运行：未检测到 DayMind 时自动回退到仅基于当前触发消息和自身历史日程生成；未检测到 Grok 时跳过联网风格研究。

---

## 🎮 可用指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/今日日程` | 所有人 | 查看当前人格的今日日程；如果今天还没有日程，会提示使用 /生成日程 |
| `/生成日程` | 所有人 | 立即强制重生成当前人格的今日日程，忽略已有缓存 |
| `/定制日程 <要求>` | 所有人 | 根据用户要求定制今日日程，支持指定穿搭风格/单品、日程走向等 |
| `/明日日程 <要求>` | 所有人 | 提前设置明天的定制日程要求，明天自动生成时应用 |
| `/取消明日日程` | 所有人 | 取消已设置的明日定制日程要求 |
| `/今日细分` | 所有人 | 查看当前人格今日日程的细分活动（需启用 enable_subdivision） |
| `/生成细分` | 所有人 | 重新生成当前人格的今日细分活动（需启用 enable_subdivision） |
| `/查看人格日程` | 所有人 | 查看当前已启用人格、自动生成时间、provider 与重试配置 |
| `/dayflow_debug` | 管理员 | 输出当前人格的完整上下文、配置池、Grok 风格研究预览、来源预览、渲染后的 prompt 预览等 |

### 指令别名

- `/今日日程`：`life_today`、`dayflow_today`
- `/生成日程`：`life_renew`、`dayflow_gen`
- `/定制日程`：`life_custom`、`dayflow_custom`
- `/明日日程`：`life_tomorrow`、`dayflow_tomorrow`
- `/取消明日日程`：`life_cancel_tomorrow`、`dayflow_cancel_tomorrow`
- `/今日细分`：`dayflow_sub`、`查看细分`
- `/生成细分`：`dayflow_sub_gen`、`重新生成细分`
- `/查看人格日程`：`life_personas`、`dayflow_personas`
- `/dayflow_debug`：`查看日程调试`、`日程调试`

### `/定制日程` 使用示例

```
/定制日程 今天穿洛丽塔，下午约会
/定制日程 今天穿杏花微雨
/定制日程 第二套换装下身换成裤子
/定制日程 今天下午玩新游戏偶遇前男友
```

定制日程会通过 Grok 解析用户意图，覆盖随机抽取的风格/主线/驱动，再将用户要求注入生成 Prompt。

### `/明日日程` 使用示例

```
/明日日程 穿洛丽塔，下午约会
/明日日程 穿杏花微雨
/取消明日日程
```

明日日程要求会持久化保存，明天自动生成时应用，应用后自动清除。

---

## ⚙️ 主要配置项

### 全局配置

- `default_prompt_template`：默认生成模板
- `schedule_retention_days`：日程本地保留天数，默认 `3`，填 `-1` 表示不限天数
- `llm_timeout_seconds`：LLM 调用超时时间（秒），默认 `900`，设为 `0` 则使用框架默认超时
- `final_fallback_provider`：最终兜底提供商，当所有竞速提供商都失败后进行最后一次尝试，留空则不启用
- `custom_schedule_intent_append`：定制日程意图解析追加模板，高级用户可自定义
- `style_research_system_prompt`：发送给 Grok 的 system prompt，可自定义风格研究输出结构
- `style_research_query_template`：发送给 Grok 的查询模板，需保留 `{style_name}` 占位符
- `enable_style_review`：是否启用风格研究二次审查（默认 `false`）
- `style_review_provider_id`：二次审查专用 LLM 提供商，留空则使用风格研究同款 LLM

### 每个人格配置

- `select_persona`：绑定的人格名
- `enabled`：是否启用该人格配置
- `location`：所在地点（如 `"北京市海淀区"`），填写后 Grok 联网研究时会顺便查询该地点当日真实天气并覆盖天气池的随机值；留空则使用天气池随机抽取，适合异世界等虚构人设
- `presence_injection_level`：存在感注入等级（1=关闭 / 2=仅时间 / 3=引导分享日程 / 4=引导分享日程+心情），默认 `2`
- `presence_min_interval_minutes`：存在感注入最小间隔（分钟），默认 `0`（始终注入）
- `select_providers`：日程生成提供商（支持多个提供商并行竞速，谁先成功用谁的结果；只填一个则串行生成）
- `generate_time`：该人格自动生成日程的时间，如 `07:00`
- `retry_count`：生成失败后的修复重试次数
- `prompt_template`：人格专用**日程生成模板**，留空则回退到默认模板
- `style_research_template`：人格专用**风格研究模板**，留空则回退到全局 `style_research_system_prompt`
- `style_review_template`：人格专用**二次审查模板**，留空则回退到全局 `style_review_system_prompt`
- `schedule_variation_level`：日程变化等级（低 / 中 / 高 / 随机）
- `enable_subdivision`：启用日程细分（默认 `false`）
- `enable_style_review`：人格级覆盖全局二次审查开关
- `style_review_provider_id`：人格级二次审查 LLM 提供商
- `push_targets`：日程推送目标（UMO 字符串数组），日程生成后自动推送到指定的聊天会话
- `push_image_enabled`：推送时渲染为图片（默认 `false`），图片渲染失败时自动降级为纯文本

### 参考数量

- `reference_schedule_days`：生成时参考前几天的历史日程
- `reference_diary_days`：生成时参考前几日日记
- `reference_recent_count`：参考近期对话数量

### 约束池

DayFlow 会从以下池中取值注入 Prompt：

- `pool.today_weather`：今日天气池（若配置了 `location`，Grok 联网研究时获取的真实天气会覆盖此池的随机值）
- `pool.outfit_styles`：穿搭风格池
- `pool.schedule_main_types`：日程主线类型池
- `pool.core_event_drivers`：核心事件驱动池

---

## 📝 使用说明

1. 若 persona 未配置 `provider_id`，会优先尝试使用当前会话模型。
2. 若检测到 DayMind，DayFlow 会优先读取该人格的近日日记与消息缓存作为参考。
3. 若检测到 `astrbot_plugin_grok_web_search`，DayFlow 会优先对当前穿搭风格做联网研究，再把研究结果作为风格约束注入生成 Prompt。
4. 若人格配置了 `location`，Grok 联网研究时会顺便查询该地点当日真实天气，覆盖天气池的随机值；真实天气随风格研究一起缓存（有效期 1 天），不会额外发起 API 请求。
5. 若未检测到 DayMind 或对应接口不可用，DayFlow 会自动回退到仅基于当前触发消息和自身历史日程生成。
6. 若未检测到 Grok 搜索插件，DayFlow 仍可正常运行，只是不会启用联网风格研究和真实天气查询。
7. 高级用户可通过 schema 自定义 Grok 风格研究使用的 system prompt 与 query 模板；**注意：写坏模板会直接影响联网研究质量。**
8. `/生成日程` 会强制重生成当日日程，忽略已有缓存；`/今日日程` 则会在有缓存时直接返回。
9. 自动生成与手动生成都会写入本地持久化存储。
10. 同一人格在生成期间会加锁，避免重复并发生成。
11. 只有已配置且启用的人格才会生成日程，未配置人格会被拒绝。
