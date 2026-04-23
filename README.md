[![DayFlow Counter](https://count.getloli.com/get/@Inoryu7z.dayflow?theme=miku)](https://github.com/Inoryu7z/astrbot_plugin_dayflow_life_scheduler)

# 🌸 DayFlow · 心迹日程

给 Bot 一条贴近今天的生活轨迹，让它在开口之前，就先知道自己今天正在过怎样的一天。

**DayFlow** 是一个多人格日程生成插件。  
它会为当前人格生成一份带有**穿搭、生活节奏与时间线**的今日日程，让 Bot 在回复时不只是套用人设，而是带着"今天的生活上下文"来说话。

---

## ✨ 它能做什么

### 👗 生成今日穿搭与日程

DayFlow 会根据当前人格配置，生成一份结构化的今日生活安排。

生成时会参考：
- 当前日期
- 当前人格设定
- 今日天气
- 穿搭风格
- 日程主线类型
- 核心事件驱动
- 近日日程历史
- 近期对话
- DayMind 提供的前几日日记
- Grok 联网风格研究结果

生成结果至少包含两部分：
- `outfit`：今日穿搭描述
- `schedule`：今日日程安排

### 🎲 日程变化等级系统

支持 `schedule_variation_level` 配置，支持"低 / 中 / 高 / 随机"：

- 用于控制今天和前几天的差异程度。
- "随机"按 3:6:1 比例抽取低/中/高。
- 避免连续几天结构过于重复。

### 🎯 双轴结构控制

支持双轴日程约束：

- `schedule_main_types`：今日总体节奏（如日常常规型、外出探索型、居家慵懒型等）。
- `core_event_drivers`：今日核心事件被什么推动（如任务驱动、情绪驱动、社交驱动等）。

### ⏰ 自动定时生成

支持按人格设置 `generate_time`，到点后自动为该人格生成当日日程。

- 每个人格独立定时。
- 同一天只会自动生成一次。

### 🔗 自动生成可绑定最近活跃会话

自动生成时会尝试从 DayMind 的消息缓存和会话-人格映射中找到该人格最近活跃的会话。

- 让生成的日程更贴近真实互动历史。
- 不是无来源硬生成。

### 💾 本地持久化日程

DayFlow 会把已生成的日程保存到本地目录，重启后仍可读取。

- 默认保留最近 3 天的日程。
- 可通过 `schedule_retention_days` 修改保留天数。
- 填 `-1` 表示不限天数。

## 🌼 推荐搭配插件

推荐与以下插件配合使用：

- [`astrbot_plugin_daymind`](https://github.com/Inoryu7z/astrbot_plugin_daymind)：提供近期对话、近日日记、最近活跃会话等连续生活参考
- [`astrbot_plugin_grok_web_search`](https://github.com/piexian/astrbot_plugin_grok_web_search)：提供联网风格研究，降低模型误解穿搭风格的概率

---

## 🌙 与 DayMind 协作

如果同时安装了 `astrbot_plugin_daymind`，DayFlow 会优先读取：

- 当前人格前几日日记
- 当前会话的近期对话缓存
- 该人格最近活跃的会话

这样生成出来的日程会更有连续性，更像"今天是从昨天延续而来"。

如果没有安装 DayMind，DayFlow 也可以独立运行。

---

### 🌐 Grok 联网风格研究

如果同时安装了 `astrbot_plugin_grok_web_search`，DayFlow 会在生成前对当前抽中的穿搭风格做一次联网研究。

这一步的核心目的不是单纯增加信息量，而是**降低模型对服装风格的误解概率**，尤其适合：
- 名称容易被字面误读的风格
- 容易与相近风格混淆的风格
- 混合型、细分型、圈内语义较强的风格

例如当风格词本身较特殊时，纯模型世界知识可能会发生错误联想；接入 Grok 后，可以先结合搜索结果补足风格定义、核心识别点、误判禁区与同风格换装边界，再交给主模型生成穿搭与日程。

这层联网研究主要会帮助模型明确：
- 这个风格到底是什么
- 两套穿搭都必须保留哪些识别点
- 晨间与午后换装应如何保持同一风格体系
- 常见误判与禁区是什么

同时具备：
- 本地缓存，避免重复搜索
- JSON 优先解析，失败时自动回退纯文本摘要
- 来源预览与调试信息输出
- 可在 schema 中自定义 Grok 风格研究 system prompt 与 query 模板，方便高级用户定制研究结构

## 🌼 适合的场景

如果你希望 Bot：
- 不同人格每天都有不同的生活安排
- 回复里自然带出日程感
- 在聊天时像是真的已经开始过今天
- 能与 DayMind 形成连续的生活流
- 在细分穿搭风格上尽量减少理解跑偏

那 DayFlow 会很适合你。

---

## 🎮 可用指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/查看日程` | 所有人 | 查看当前人格的今日日程；如果今天还没有日程，会先自动生成再返回 |
| `/生成日程` | 所有人 | 立即强制重生成当前人格的今日日程，忽略已有缓存 |
| `/定制日程 <要求>` | 所有人 | 根据用户要求定制今日日程，支持指定穿搭风格/单品、日程走向等 |
| `/明日日程 <要求>` | 所有人 | 提前设置明天的定制日程要求，明天自动生成时应用 |
| `/取消明日日程` | 所有人 | 取消已设置的明日定制日程要求 |
| `/查看人格日程` | 所有人 | 查看当前已启用人格、自动生成时间、provider 与重试配置 |
| `/dayflow_debug` | 管理员 | 输出当前人格的完整上下文、配置池、Grok 风格研究预览、来源预览、渲染后的 prompt 预览等 |

### 指令别名

- `/查看日程`：`life_show`、`dayflow_show`
- `/生成日程`：`life_renew`、`dayflow_gen`
- `/定制日程`：`life_custom`、`dayflow_custom`
- `/明日日程`：`life_tomorrow`、`dayflow_tomorrow`
- `/取消明日日程`：`life_cancel_tomorrow`、`dayflow_cancel_tomorrow`
- `/查看人格日程`：`life_personas`、`dayflow_personas`
- `/dayflow_debug`：`查看日程调试`、`日程调试`

### `/定制日程` 使用示例

```
/定制日程 今天穿洛丽塔，下午约会
/定制日程 今天穿杏花微雨
/定制日程 第二套换装下身换成裤子
/定制日程 今天下午玩新游戏偶遇前男友
```

定制日程会：
1. 通过 Grok 解析用户意图
2. 覆盖随机抽取的风格/主线/驱动
3. 将用户要求注入生成 Prompt

### `/明日日程` 使用示例

```
/明日日程 穿洛丽塔，下午约会
/明日日程 穿杏花微雨
/取消明日日程
```

明日日程会：
1. 保存定制要求到持久化存储
2. 明天自动生成时自动应用
3. 应用后自动清除

---

## ⚙️ 主要配置项

### 全局配置

- `default_prompt_template`：默认生成模板
- `schedule_retention_days`：日程本地保留天数，默认 `3`，填 `-1` 表示不限天数
- `presence_injection_level`：存在感注入等级（1=关闭 / 2=仅时间 / 3=引导分享日程 / 4=引导分享日程+心情），默认 `2`
- `presence_min_interval_minutes`：存在感注入最小间隔（分钟），默认 `0`（始终注入）
- `style_research_retry_count`：风格研究失败时的额外重试次数
- `style_research_system_prompt`：发送给 Grok 的 system prompt，可自定义风格研究输出结构
- `style_research_query_template`：发送给 Grok 的查询模板，需保留 `{style_name}` 占位符

### 每个人格配置

- `select_persona`：绑定的人格名
- `enabled`：是否启用该人格配置
- `location`：所在地点（如 `"北京市海淀区"`），填写后 Grok 联网研究时会顺便查询该地点当日真实天气并覆盖天气池的随机值；留空则使用天气池随机抽取，适合异世界等虚构人设
- `provider_id`：该人格使用的模型提供商（失败时可回退到会话 provider）
- `generate_time`：该人格自动生成日程的时间，如 `07:00`
- `retry_count`：生成失败后的修复重试次数
- `prompt_template`：人格专用模板；留空则回退到默认模板
- `schedule_variation_level`：日程变化等级（低 / 中 / 高 / 随机）

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
4. 若人格配置了 `location`，Grok 联网研究时会顺便查询该地点当日真实天气，覆盖天气池的随机值；真实天气随风格研究一起缓存（有效期1天），不会额外发起 API 请求。
5. 若未检测到 DayMind 或对应接口不可用，DayFlow 会自动回退到仅基于当前触发消息和自身历史日程生成。
6. 若未检测到 Grok 搜索插件，DayFlow 仍可正常运行，只是不会启用联网风格研究和真实天气查询。
7. 高级用户可通过 schema 自定义 Grok 风格研究使用的 system prompt 与 query 模板；**注意：写坏模板会直接影响联网研究质量。**
8. `/生成日程` 会强制重生成当日日程，忽略已有缓存；`/查看日程` 则会在有缓存时直接返回。
9. 自动生成与手动生成都会写入本地持久化存储。
10. 同一人格在生成期间会加锁，避免重复并发生成。
11. 只有已配置且启用的人格才会生成日程，未配置人格会被拒绝。

---

## 🛠️ TODO

- [x] 支持在使用 Grok 联网研究时，顺便输出用户指定地点的当天天气（v1.2.7）
