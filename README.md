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

## 🌙 与 DayMind 协作

如果同时安装了 `astrbot_plugin_daymind`，DayFlow 会优先读取：

- 当前人格前几日日记
- 当前会话的近期对话缓存
- 该人格最近活跃的会话

这样生成出来的日程会更有连续性，更像"今天是从昨天延续而来"。

如果没有安装 DayMind，DayFlow 也可以独立运行。

---

## 🌼 适合的场景

如果你希望 Bot：
- 不同人格每天都有不同的生活安排
- 回复里自然带出日程感
- 在聊天时像是真的已经开始过今天
- 能与 DayMind 形成连续的生活流
- 不把旧日程伪装成今天的内容

那 DayFlow 会很适合你。

---

## 🎮 可用指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/查看日程` | 所有人 | 查看当前人格的今日日程；如果今天还没有日程，会先自动生成再返回 |
| `/生成日程` | 所有人 | 立即强制重生成当前人格的今日日程，忽略已有缓存 |
| `/查看人格日程` | 所有人 | 查看当前已启用人格、自动生成时间、provider 与重试配置 |
| `/dayflow_debug` | 管理员 | 输出当前人格的完整上下文、配置池、渲染后的 prompt 预览等 |

### 指令别名

- `/查看日程`：`life_show`、`dayflow_show`
- `/生成日程`：`life_renew`、`dayflow_gen`
- `/查看人格日程`：`life_personas`、`dayflow_personas`
- `/dayflow_debug`：`查看日程调试`、`日程调试`

---

## ⚙️ 主要配置项

### 全局配置

- `default_prompt_template`：默认生成模板
- `schedule_retention_days`：日程本地保留天数，默认 `3`，填 `-1` 表示不限天数

### 每个人格配置

- `select_persona`：绑定的人格名
- `enabled`：是否启用该人格配置
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

- `pool.today_weather`：今日天气池
- `pool.outfit_styles`：穿搭风格池
- `pool.schedule_main_types`：日程主线类型池
- `pool.core_event_drivers`：核心事件驱动池

---

## 📝 使用说明

1. 若 persona 未配置 `provider_id`，会优先尝试使用当前会话模型。
2. 若检测到 DayMind，DayFlow 会优先读取该人格的近日日记与消息缓存作为参考。
3. 若未检测到 DayMind 或对应接口不可用，DayFlow 会自动回退到仅基于当前触发消息和自身历史日程生成。
4. `/生成日程` 会强制重生成当日日程，忽略已有缓存；`/查看日程` 则会在有缓存时直接返回。
5. 自动生成与手动生成都会写入本地持久化存储。
6. 同一人格在生成期间会加锁，避免重复并发生成。
7. 只有已配置且启用的人格才会生成日程，未配置人格会被拒绝。
