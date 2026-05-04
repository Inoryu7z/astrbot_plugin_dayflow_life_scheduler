# Changelog

## v1.6.3 - 2026-05-05

**🔧 细分校验放宽 + 日志改进 + save_generated 提前**

* 细分校验放宽：移除 items 数量上限限制（原 2-4 个），改为仅检查非空；移除子事件最短 10 分钟时长限制；移除子事件总时长必须等于父时段时长的精确匹配校验；移除必须覆盖所有 timeline 索引的要求
* 细分日志改进：新增提供商配置日志、单提供商返回状态日志、多提供商竞速模式日志、竞速无胜出状态日志、最终回退成功日志、解析开始日志
* `/今日日程` 滞后问题修复：`save_generated` 提前到主日程生成完立即执行（不等细分），细分生成后追加更新。修复了主日程已生成但细分期间 `/今日日程` 显示无日程的问题

---
## v1.6.2 - 2026-04-30

**🔄 提供商逻辑精简 + 日志优化 + 新增细分生成指令**

* 移除对话提供商（session/default provider）回退逻辑，提供商流程简化为：竞速提供商 → 最终兜底提供商
* 新增 `_summarize_error()` 错误摘要方法：524/529/502/503/429 等 Cloudflare 错误压缩为一行，其他超长错误截断到 300 字符
* 竞速失败汇总日志中每个 provider 状态截断到 80 字符，避免日志爆炸
* 注入日志合并：3 条注入日志（存在感+日程+细分）合并为 1 条
* 新增 `/生成细分` 指令（别名 `dayflow_sub_gen`、`/重新生成细分`），可重新生成今日细分内容
* 删除 `_resolve_session_provider_id` 方法（已无调用者）
* `GenerationContext` 移除 `session_provider_id` / `default_provider_id` 字段
* 配置面板 hint 更新：移除对话提供商相关描述，补充兜底提供商说明

---

## v1.6.1 - 2026-04-29

**🔧 代码质量全面改进 + 日志中文化**

* 修复 `_build_schedule_result` 参数过多（17个）问题：引入 `GenerationContext` dataclass 封装参数
* 修复 `_try_final_fallback` 参数过多（15个）问题：同样使用 `GenerationContext` 封装
* 修复 `validate_persona` 使用 walrus 运算符 `:=` 隐式赋值，可读性极差的问题
* 修复 `_cleanup_stale_caches` 中时间比较使用字符串 `iso < cutoff.isoformat()` 不可靠的问题，改为 `datetime.fromisoformat()` 解析后比较
* 修复 `_is_emoji` 检测范围不完整问题：从 8 个范围扩展到 24 个，覆盖 Emoji 15.0 全部范围
* 修复 `save_schedule` 浅拷贝问题：从 `dict(data)` 改为 `copy.deepcopy(data)`，彻底解决嵌套数据共享
* 消除 `_parse_hhmm_to_minutes` 重复定义：提取到 `core/utils.py` 共享工具模块
* 统一插件发现机制：提取 `_find_plugin_by_name` 通用方法，消除 `_find_daymind_plugin` 和 `_find_grok_plugin` 的重复逻辑
* 细分竞速从串行改为并行：`_generate_subdivision` 中多提供商竞速使用 `asyncio.wait(FIRST_COMPLETED)` 并行
* 细分生成新增完整日志：入口添加 `[dayflow-细分] 开始生成细分` 日志，成功/失败均有明确中文提示
* 全部英文日志翻译为中文：~140 条日志从英文改为中文，`[dayflow-style-research]` 统一为 `[dayflow-风格研究]`，`[dayflow-subdivision]` 统一为 `[dayflow-细分]`
* 新增 `core/utils.py` 共享工具模块：包含 `parse_hhmm_to_minutes`、`deep_copy_schedule`、`GenerationContext`

---

## v1.6.0 - 2026-04-29

**🧩 日程细分骨架（双LLM制）**

* 新增日程细分功能：Model 1 生成大骨架日程后，复用同一套提供商逻辑（竞速-对话-兜底-最终兜底）对大骨架进行创造性细分
* 新增人格级配置项 enable_subdivision（纯 bool 开关），细分复用日程生成的同一套提供商配置
* 细分结果 sub_events 独立存储，不嵌入 timeline，回退安全零影响
* 细分失败时只记录 warning 日志，不影响主日程数据
* 新增 validate_sub_events 校验函数，校验细分结果的时间完整性、覆盖率和粒度
* 新增 build_draft_skeleton 格式化函数，将 timeline 转为细分模型可读的文本格式
* 新增 <DayFlow-Current-Activity> 标签注入，动态注入当前细分活动（此刻+刚做完+接下来，约60 token）
* 新增 get_sub_events 方法，供 DayMind 和 DailySharing 获取细分数据
* 新增 build_current_sub_activity_injection 方法，供系统提示词注入使用
* 新增 /今日细分 命令（别名 dayflow_sub、查看细分），查看当前人格今日日程的细分活动

---


## v1.5.1 - 2026-04-28

**🐛 修复串行路径最终兜底未传递 best_partial**

* 修复单提供商（串行）路径中 `_try_final_fallback` 两处调用未传递 `best_partial` 参数的问题
* 校验失败路径：构建 `serial_best_partial` 传递 raw_text 和 reason，使兜底提供商能利用部分成功结果构建修复提示词
* 异常路径：构建 `exception_best_partial` 传递异常信息

**🔧 增强最终兜底提供商重试能力**

* `_try_final_fallback` 新增 `max_repair_retries` 参数（默认1），支持修复重试循环
* 原来只尝试一次（retry_count=0），现在默认有1次修复重试机会，提高兜底成功率

---

## v1.5.0 - 2026-04-28

**🛡️ 最终兜底提供商**

* 新增全局配置项 `final_fallback_provider`，当所有竞速提供商和对话提供商都失败后，使用此提供商进行最后一次尝试
* 兜底尝试只执行一次，不进行 repair 重试，若失败则真正失败
* 兜底尝试会利用竞速阶段的 best_partial 信息构建 repair prompt（如果有）
* 适用于竞速提供商和对话提供商都是高失败率模型的场景

**⚙️ LLM 超时时间可配置化**

* 新增全局配置项 `llm_timeout_seconds`，默认 900 秒（与之前硬编码值相同）
* 设为 0 时跳过超时覆盖，使用 AstrBot 框架默认超时（约 120 秒）
* 修复 `call_llm_once` 中 `original_client_timeout` 未初始化的潜在 NameError
* 移除无效的 `prov.timeout` 修改（仅 `prov.client.timeout` 在运行时被 openai SDK 动态读取，`prov.timeout` 是初始化后不再被框架读取的死属性）

---

## v1.4.8 - 2026-04-28

**⏱️ 插件接管 LLM 超时时间**

* `call_llm_once` 在调用前临时将提供商 timeout 设为 900 秒（15 分钟），调用后恢复原值
* 解决慢速提供商因框架默认 120 秒超时而失败的问题

---

## v1.4.7 - 2026-04-28

**🛡️ JSON 解析容错增强**

* 增强 `safe_json_loads`：支持尾随逗号、行内注释、截断 JSON 补全等容错解析
* 截断 JSON 补全：LLM 输出被截断时，逐步剥离不完整部分并补全闭合括号，尽可能提取已有字段
* 修复 `call_llm_once` 对部分提供商（如 GLM-5.1）返回空文本的问题：当 `completion_text` 为空时，尝试从 `result_chain` 的组件中提取文本

---

## v1.4.6 - 2026-04-27

**🐛 修复搜索额度浪费**

* `/生成日程` 不再清空 frozen_randoms，复用当天的随机参数和风格研究结果，避免浪费搜索额度
* `/定制日程` 仍然清空 frozen_randoms，重新随机 + 重新搜索
* 拆分 `force_regenerate`（跳过已有日程）和 `reset_randoms`（清空随机参数）两个独立控制

---

## v1.4.5 - 2026-04-27

**🔧 提供商配置重构**

* 移除旧的 `select_provider` 单选字段，统一使用 `select_providers` 配置提供商
* `select_providers` 扩展为 3 个下拉选择框（`racing_provider_1`/`racing_provider_2`/`racing_provider_3`），留空不参与竞速
* 兼容旧配置格式：如果用户仍使用旧的 `select_provider` 或 list 格式的 `select_providers`，解析时会自动迁移

---

## v1.4.4 - 2026-04-27

**🐛 竞速机制修复 + 配置体验优化**

* 修复竞速/串行/fallback 三条路径中 `call_llm_with_provider_fallback` 的 `retry_count` 嵌套放大问题：将内部 `retry_count` 从 `repair_retries` 改为 `0`，重试完全由外层 repair 循环控制，避免 retry_count=3 时单提供商最多 16 次 LLM 调用（现降为 4 次）
* 修复单提供商竞速丢失 fallback 行为：`select_providers` 只有 1 个提供商时走串行路径，保留 session/default fallback 链
* 修复竞速全部失败后 fallback 使用原始 prompt 的问题：现在会利用竞速阶段的最佳部分结果构建 repair prompt，提高 fallback 成功率
* 新增全局 LLM 并发控制：`asyncio.Semaphore(4)` 限制同时进行的 LLM 请求数不超过 4，防止竞速 + 多人格场景下并发过高
* 新增竞速调试信息：`_race_providers` 记录各提供商结果到 debug payload，包含 `racing_providers`、`racing_results`、`racing_winner` 等
* 优化竞速配置体验：`select_providers` 从 template_list 改为固定两个下拉选择框（`racing_provider_1`、`racing_provider_2`），与主提供商选择体验一致
* 修复 `describe_personas` 单提供商竞速展示不准确的问题
* 修复竞速成功时 `fallback_used` 误判为 True 的问题：竞速赢家在 `select_providers` 列表中不算 fallback
* 降级 `get_life_context missing target schedule` 日志从 warning 到 info（正常业务流程）

---

## v1.4.3 - 2026-04-27

**🐛 修复生成锁未释放 + 🚀 提供商并行竞速**

* 修复 async generator 中 yield 在 try-finally 内导致锁不释放的 bug：将 yield 移出 try-finally 块，确保 exit_generation 一定执行
* generating_personas 从 set 改为 dict 记录时间戳，enter_generation 时自动清理超过 10 分钟的陈旧锁
* 新增 select_providers 列表配置：支持为每个人格配置多个提供商，生成时并行竞速，谁先成功用谁的结果
* 竞速模式下每个提供商独立重试（和串行模式逻辑一致），全部失败后回退对话模型
* 向后兼容：未配置 select_providers 时走原来的串行逻辑，行为无变化
* describe_personas 展示竞速提供商信息

---

## v1.4.2 - 2026-04-26

**🐛 修复多日程共存 bug + 调试日志增强**

* 修复 save_schedule 引用共享问题：memory_store 直接存储原始 dict 引用，history_store 浅拷贝但 meta 子字典仍共享，改为显式深拷贝 data 和 meta
* save_schedule 新增旧条目移除计数日志，便于追踪同日覆盖情况
* get_life_context 新增多 candidate_keys 查找调试日志，当存在多个候选 key 时记录每个 key 的命中/未命中状态

---

## v1.4.1 - 2026-04-26

**🎨 UI 全面优化 + Emoji 支持**

* 时间放入卡片内左侧，大时间+小时间对比，竖线分隔
* 换装单独成鼠尾草绿气泡，与柔粉主卡片形成双色系
* 标题加粗加深，与详情辨识度提升
* 去掉所有消息前缀，输出更干净
* 新增 Emoji 字体支持，正文内容可渲染彩色 Emoji
* 修复定制日程图片发两次（推送排除当前会话）

---

## v1.4.0 - 2026-04-26

**🎨 时间轴布局优化 + 图片渲染日志输出**

**1. 🎨 修复时间戳与卡片文本框重合**

* 时间戳从圆点左侧移到圆点上方，避免与卡片内容重叠
* 卡片起始位置从 `timeline_x + 16` 调整为 `timeline_x + 24`，增加间距
* 时间戳水平居中于圆点上方，不再与圆点重叠

**2. 🎨 新增时间线竖线**

* 每个时间轴条目之间绘制竖线连接，增强时间轴视觉连续性

**3. 📝 图片渲染时输出文字日志**

* 图片渲染成功后，后台同时输出 `logger.info` 文字日志
* 便于调试和查看日程内容，无需依赖图片显示

---

## v1.3.9 - 2026-04-26

**🐛 修复日程图片渲染：错位 + emoji 方框 + 穿搭溢出**

**1. 🐛 修复高度计算与绘制宽度不一致导致错位**

* `_calc_item_height` 中的 `content_width` 计算与 `_draw_timeline_item` 中的实际绘制宽度不一致
* 高度计算用的是 `img_width - padding_x - timeline_x - 30`，绘制用的是 `card_width - card_padding * 2`
* 统一为从绘制逻辑推导的 `card_width - card_padding * 2`，确保换行位置一致

**2. 🐛 修复 emoji 渲染为方框（口里一个x）**

* `👗` emoji 在 NotoSerifSC 等中文字体中不存在，Pillow 无法渲染
* 将换装标签从 `👗 换装内容` 改为 `换装：换装内容`

**3. 🐛 修复穿搭文本不换行、跑出图片外**

* `outfit_change` 绘制时直接 `draw.text()` 一行写完，未调用 `_wrap_text`
* 改为调用 `_wrap_text` 换行后逐行绘制，高度计算也同步更新

**4. 🐛 修复 `_wrap_text` 不处理换行符**

* `_wrap_text` 遇到 `\n` 时未断行，导致多行文本被拼成一行
* 改为先按 `\n` 分段，每段独立换行

---

## v1.3.8 - 2026-04-26

**🐛 修复图片发送崩溃：`image_result` 误传 Image 对象**

**1. 🐛 修复 `_send_schedule_result` 图片发送路径崩溃**

* `event.image_result()` 只接受 `str`（URL 或文件路径），但代码传入了 `ImageComponent.fromBytes()` 返回的 `Image` 对象
* 导致 `AttributeError: 'Image' object has no attribute 'startswith'`
* 改用 `event.chain_result()` + 消息链（`[Plain, ImageComponent]`）发送图片
* 同时修复了图片模式下 prefix 和图片分两条消息发送的问题，现在合并为一条消息链

---

## v1.3.7 - 2026-04-26

**🔄 指令重构：去除懒加载，新增 /今日日程**

**1. 🔄 /查看日程 → /今日日程**

* 移除 `/查看日程` 命令（alias: `life_show`, `dayflow_show`）
* 新增 `/今日日程` 命令（alias: `life_today`, `dayflow_today`）
* 新命令仅查看今日日程，不再自动触发生成（去除懒加载）
* 日程不存在时提示用户使用 `/生成日程` 手动生成

**2. 🔗 兼容性说明**

* DayMind 的"思考前确保今日日程存在"功能通过 `service.generate_schedule()` 直接调用，不经过命令层，不受本次变更影响

---

## v1.3.6 - 2026-04-25

**🔧 图片渲染功能修复**

**1. 🐛 修复换装标签解析 Bug**

* `lstrip("👗换装：")` 误用：`lstrip` 按单字符剥离，会导致内容中包含"换"、"装"等字符时被错误截断
* 改用 `re.sub(r'^👗\s*换装[：:]\s*', '', line)` 精确匹配前缀

**2. 🐛 修复高度计算与实际绘制不一致**

* `_calc_item_height` 中 title-detail 间距为 8，实际 `_draw_timeline_item` 绘制间距为 4
* 统一为 4，消除卡片底部多余空白

**3. 🐛 修复时间文本与时间线圆点重叠**

* 时间文本从 `padding_x` 开始绘制，与 `timeline_x` 处的圆点重叠
* 改为右对齐到圆点左侧，留 12px 间距

**4. ⚡ 优化背景渐变绘制性能**

* 逐行绘制改为 64 段矩形填充，大幅减少绘制调用次数

**5. ⚡ 图片渲染异步化**

* `_render_push_image` 改为 `async` 方法，使用 `asyncio.to_thread()` 避免阻塞事件循环
* 新增 `asyncio.Lock` 防止并发渲染竞态

**6. 🛡️ Pillow 版本检查**

* 新增 Pillow ≥ 8.2.0 版本检查（`rounded_rectangle` API 需要）
* 旧版本输出 warning 日志并禁用图片渲染

**7. 🌐 添加国内字体下载镜像源**

* jsDelivr 优先（国内可达性好于 GitHub）
* 新增 ghproxy 镜像作为第三备用源

**8. 🔧 代码质量改进**

* `_wrap_text` fallback 宽度估算区分中英文字符
* `_should_send_image` 显式 None 检查

---

## v1.3.5 - 2026-04-25

**✨ 新增日程图片渲染功能**

**1. 🎨 清新自然风日程图片**

* 新增 `core/schedule_renderer.py` 日程图片渲染器
* 采用清新自然风设计：柔和渐变背景（绿白过渡）、圆角卡片、垂直时间轴+卡片混合布局
* 左侧时间节点圆点+时间文本，右侧为圆角卡片内容区（标题、详情、换装标签）
* 顶部显示日期和人格名称，底部显示 Dayflow 品牌标识
* 字体策略同 DayMind：优先使用系统字体，无则自动下载 NotoSerifSC-Regular
* 支持从 timeline 数组或 schedule 纯文本解析日程条目
* 图片宽度 640px，高度根据内容动态计算

**2. ⚙️ 人格级图片推送开关**

* persona 配置新增 `push_image_enabled` 字段（bool，默认 False）
* 不同人格可独立开关图片推送/查看
* 开启后，日程自动推送和命令查看均输出图片
* 关闭时保持原有纯文本行为

**3. 🛡️ 图片渲染失败自动降级**

* 图片渲染失败时自动回退到纯文本推送
* 记录 warning 日志便于排查

**4. 🔧 代码变更**

* `service.py`：新增 `_render_push_image()` 方法，`push_schedule_to_targets()` 支持图片/纯文本双模式
* `main.py`：新增 `_should_send_image()` 和 `_send_schedule_result()`，所有日程输出命令统一走图片/文本分发
* `config.py`：新增 `push_image_enabled` 字段解析
* `_conf_schema.json`：新增 `push_image_enabled` 配置项

---

## v1.3.4 - 2026-04-25

**🔧 代码质量改进：循环导入消除 + error 字段统一 + 内存泄漏清理**

**1. ♻️ 消除循环导入风险**

* `render_schedule_display` 和 `is_schedule_valid` 从 main.py 移至 `core/generator.py`
* service.py 的 `_render_push_content` 不再通过 `from ..main import` 延迟导入
* 彻底消除 main.py ↔ service.py 的循环依赖隐患

**2. 🐛 统一 error 字段**

* `get_life_context` 和 `_build_missing_today_context` 返回的无效日程数据增加 `"error": True`
* 与 `_build_persona_not_enabled_data` 保持一致，确保所有依赖 `meta.get("error")` 的代码路径行为统一

**3. 🧹 内存泄漏清理**

* 新增 `_cleanup_stale_caches()` 方法：清理非当天的 `_frozen_randoms` 和 7 天前的 `_last_interaction_times`
* 启动时清理一次，调度循环中每小时清理一次

**4. 🔒 Store 封装改进**

* DayflowStore 新增 `get_history_count()` 和 `get_memory_date()` 访问器方法
* `save_generated` 日志不再直接访问 Store 内部字典

**5. ♻️ 消除验证逻辑重复**

* `_build_injection_text` 不再重复校验逻辑，改为调用 `is_schedule_valid()`

**6. 🛡️ 推送防御性校验**

* `push_schedule_to_targets` 开头增加 `is_schedule_valid()` 检查，无效日程不推送

---

## v1.3.3 - 2026-04-25

**🔧 代码质量改进：fallback 语义冲突 + 死代码清理 + 异步化**

**1. 🐛 修复 `fallback` 字段语义冲突（显示/决策路径）**

* 新增 `_is_schedule_valid(data)` 辅助函数，基于内容有效性判断日程是否有效
* 替换 main.py 中 4 处 `not existing.get("meta", {}).get("fallback")` 检查
* 之前只修了注入层面（v1.3.0），显示和决策路径仍会把备用 provider 生成的日程误判为无效
* 现在备用 provider 成功生成的日程可正常展示和复用

**2. ♻️ 消除渲染逻辑重复**

* `_render_push_content()` 不再重复实现渲染逻辑，改为调用 `_render_schedule_display()`
* 后续只需维护一处渲染代码

**3. 🧹 清理死代码**

* 删除 `_style_research_retry_count()` getter（外层重试循环已在 v1.3.1 删除，此配置无效）
* 删除 `_conf_schema.json` 中对应的 `style_research_retry_count` 配置项

**4. ⚡ `save_schedule()` 异步化**

* `save_schedule()` 改为 `async def`，内部调用 `await async_save_state()` 替代同步 `_save_state()`
* 不再阻塞事件循环

**5. 📊 `describe_personas()` 增加推送目标显示**

* 输出末尾追加 `| 推送:N个目标`

---

## v1.3.2 - 2026-04-26

**🔧 代码质量修复 + 新增日程推送功能**

**1. 🐛 代码瑕疵修复（P0）**

* `_history_item_to_schedule` 返回类型从 `dict` 修正为 `dict | None`，与方法实际行为一致
* `prune_expired()` 新增节流机制（300秒间隔），避免高频调用时反复遍历全部数据；关键调用点使用 `force=True`
* `_save_state()` 新增 `threading.Lock` 防止并发写入文件
* 新增 `async_save_state()` 方法，通过 `asyncio.to_thread()` 将同步文件 I/O 卸载到线程池
* `save_generated()` 改为 `async def`，内部调用 `await self.store.async_save_state()`
* `_last_debug_payload` 新增 `threading.Lock` 保护，新增 `_update_debug_payload()` 方法

**2. 🔒 封装性修复（P1）**

* 新增 `DayflowStore.save_schedule()` 方法，封装 memory_store 写入、history_store 去重追加、prune 和 save
* `DayflowService.save_generated()` 不再直接操作 Store 内部字典

**3. ✨ 日程推送功能**

* persona 配置新增 `push_targets` 字段（UMO 字符串数组）
* 日程生成后自动推送到配置的聊天会话
* 推送内容不含"🧠 人格：xxx"前缀
* 推送失败静默跳过并记录日志
* 预留图片渲染接口（`_render_push_content()`）

---

## v1.3.1 - 2026-04-25

**🔧 风格研究调用重构与降级校验**

**1. 🔄 重试逻辑简化：完全交给 Grok 插件**

* 删除 `_research_style_reference` 中的外层重试循环（之前 `for attempt in range(1, retries+2)`）
* 改为只调一次 `grok._do_search(use_retry=True)`，重试完全由 Grok 插件内部处理
* 消除了 dayflow 外层循环 + grok 内部重试的双重重试问题

**2. 🛡️ ok=False 时 raw 降级**

* 当 Grok 返回 `ok=False` 时，检查 `raw` 字段是否有内容
* 有内容则降级使用纯文本（走已有的 `_render_style_reference_from_plain_text` 路径）
* 之前 ok=False 时直接跳过，完全不考虑 raw 内容

---

## v1.3.0 - 2026-04-25

**🐛 修复日程注入被错误阻止的问题**

**1. 🔧 修复 `fallback` 字段语义冲突**

* `_build_injection_text` 之前检查 `meta.get("fallback")` 来决定是否注入
* 但 `fallback: true` 在 provider 切换场景下仅表示"使用了备用 provider"，日程内容是有效的
* 这导致当配置的 provider 失败、切换到备用 provider 成功生成日程后，日程无法注入系统提示词

**2. 📝 改进注入判断逻辑**

* 移除对 `meta.get("fallback")` 的检查
* 改为检查日程内容是否为占位符（`outfit == "尚未生成"` 或 `"尚未生成成功" in schedule`）
* 现在只要日程内容有效，即使使用了备用 provider 也会正常注入

---

## v1.2.9 - 2026-04-25

**🐛 修复 Grok 搜索 429 重试失效问题**

**1. 🔧 启用 Grok 插件内置重试机制**

* `_research_style_reference` 调用 `_do_search` 时将 `use_retry=False` 改为 `use_retry=True`
* 之前 `use_retry=False` 导致 grok 插件内部 `max_retries=0`，遇到 429 直接放弃当前 provider，不等待 Retry-After
* dayflow 自身的重试循环没有延迟，立刻重试又 429，形成无效重试循环
* 现在由 grok 插件自行处理 429 重试（解析 Retry-After 头、线性退避），dayflow 不再介入

---

## v1.2.8 - 2026-04-24

**👁️ 存在感注入从全局级下调为人格级**

**1. 🔧 存在感配置人格级化**

* `presence_injection_level` 和 `presence_min_interval_minutes` 从全局配置移至每个人格的独立配置
* 不同人格可以有不同的存在感注入等级和间隔，例如一个角色等级4主动分享，另一个角色等级1完全关闭
* 保留向后兼容：未配置时默认等级2、间隔0分钟

**2. 📝 配置 schema 更新**

* 全局配置中移除 `presence_injection_level` 和 `presence_min_interval_minutes`
* persona 模板中新增这两个字段

---

## v1.2.7 - 2026-04-24

**🌤️ 天气覆盖：Grok 联网研究顺便获取真实天气**

**1. 🌟 新增 `location` 配置项**

* 每个 persona 可配置 `location` 字段（如 `"北京市海淀区"`），填了就查真实天气，不填就用池随机天气
* 异世界人设不受影响——`location` 为空时完全回退到池随机天气

**2. 🌐 天气查询搭风格研究便车**

* 在同一次 Grok 联网请求中追加天气查询，零额外 API 调用
* `STYLE_RESEARCH_SYSTEM_PROMPT` 新增 `weather` 字段定义，要求 Grok 返回指定地点的真实天气
* `_build_style_research_query` 支持 `location` 参数，自动追加天气查询语句

**3. 📦 天气缓存与降级**

* 真实天气随风格研究一起缓存（有效期1天），缓存命中时直接读取
* 降级路径完整：无 location → 池随机；无 Grok → 池随机；Grok 返回 weather 为 null → 池随机
* `_research_style_reference` 返回值从 4 元素扩展为 5 元素（增加 `real_weather`）

**4. 🔧 调试信息增强**

* `/dayflow_debug` 命令新增 `location` 和 `style_research_weather` 输出

---

## v1.2.6 - 2026-04-24

**✨ 新增存在感注入机制（DayFlow Presence Injection）**

**1. 🌟 新增 `<DayFlow-Presence>` system_prompt 注入**

* Bot 现在能感知与用户的时间间隔，在每次 LLM 请求时注入存在感上下文。
* 4 级存在感等级（配置项 `presence_injection_level`）：
  - 等级 1：关闭，不注入任何内容
  - 等级 2（默认）：仅注入当前时间、上次互动时间与时间差
  - 等级 3：额外引导"可以考虑是否要主动与用户分享今天的日程，但不必刻意"
  - 等级 4：额外引导"可以考虑是否要主动与用户分享今天的日程、现在的心情或最近的思考，但不必刻意"
* 引导措辞刻意弱化，约束力度 > 引导力度，避免提示词污染导致角色每次都主动分享。

**2. 🕐 新增互动时间追踪**

* 通过 `on_llm_response` 钩子记录 Bot 回复时间（按会话追踪，内存存储，不持久化）。
* `on_llm_request` 时根据时间差生成存在感注入，时间差本身就是天然的行为调节器——"约5分钟前"自然不分享，"约3小时前"自然考虑分享。

**3. ⚙️ 新增配置项**

* `presence_injection_level`（int，默认 2）：存在感注入等级
* `presence_min_interval_minutes`（int，默认 0）：最小注入间隔，设为 0 始终注入（推荐）

**4. 🔗 与 DayMind 的联动**

* 等级 4 的引导提及"心情"和"思考"，但 DayFlow 不重复注入这些数据——DayMind 已在注入心情风格规则和思考流，等级 4 只是在行为引导层面做桥接。

---

## v1.2.5 - 2026-04-23

**🐛 修复 Fallback 提供商始终不生效的问题**

**1. 🔧 修复 `_get_default_provider_id` 无法获取 Provider ID**

* 之前使用 `getattr(provider, "id", None)` 获取 Provider ID，但 AstrBot 的 `Provider` 对象没有 `id` 属性，应使用 `provider.meta().id`。
* 这导致 `default_provider_id` 始终返回 `None`，fallback 候选列表永远为空，primary 提供商失败后无法切换到备用提供商。
* 新增 `_get_provider_id_from_instance()` 方法，优先使用 `provider.meta().id`，兼容 fallback 到 `getattr`。

**2. 🔧 修复 `call_llm_once` 在 `provider_id=None` 时崩溃**

* 之前当 `provider_id=None` 时调用 `self.context.llm_generate(prompt=prompt)`，但 AstrBot 的 `llm_generate` 的 `chat_provider_id` 是必填参数，会直接抛出 `TypeError`。
* 现在当 `provider_id` 为空时，先通过 `_get_default_provider_id()` 获取默认 Provider ID，确保总是传入有效的 `chat_provider_id`。

**3. 🔧 修复 `call_llm_with_provider_fallback` 条件判断逻辑缺陷**

* 之前的条件 `if primary_provider_id is not None or fallback_provider_id is None` 在 `primary=None, fallback≠None` 时，两个分支都不会执行，fallback 被完全跳过。
* 改为显式的 `should_try_primary` 逻辑，当 primary 为 None 且 fallback 存在时，直接使用 fallback。

**4. 🔧 代码变更**

* `service.py`：新增 `_get_provider_id_from_instance()` 方法；`_get_default_provider_id()` 改用新方法获取 Provider ID；`call_llm_once()` 在 `provider_id=None` 时获取默认 Provider；`call_llm_with_provider_fallback()` 条件判断逻辑修复。

---

## v1.2.4 - 2026-04-22

**🐛 修复生成失败重试时随机值重复 roll 的问题**

**1. 🧊 新增随机值冻结机制**

* 之前每次调用 `generate_schedule()` 都会重新 `random.choice()` 选择穿搭风格、主线类型、事件驱动、天气和变化等级。
* 当 LLM 提供商失败后，调度器自动重试时会重新 roll 所有随机值，导致：
  - 新的风格触发新的 Grok 联网搜索，产生不必要的 API 消耗
  - 每次重试的 prompt 完全不同，降低了修复重试的意义
* 现在随机值在首次生成时"冻结"，以 `(persona_key, target_date)` 为键缓存，后续重试直接复用，直到生成成功后才清除。

**2. 🔄 强制重新生成时清除冻结值**

* `generate_schedule()` 新增 `force_regenerate` 参数。
* 当用户使用 `/生成日程` 或 `/定制日程` 时，会先清除冻结值再重新 roll，确保用户主动操作能获得全新的随机结果。
* 自动调度重试时不清除冻结值，复用已有的随机选择。

**3. ✅ 生成成功后自动清除冻结值**

* `save_generated()` 保存成功日程时会自动清除对应的冻结随机值。
* 确保第二天生成时不会误用前一天的冻结值。

**4. 🗄️ 启用风格研究缓存**

* `STYLE_RESEARCH_CACHE_DAYS` 从 `0` 改为 `1`，启用同一天内相同风格的缓存。
* 之前缓存天数设为 0 导致缓存永远无效，即使冻结了随机值保证同一风格，重试时仍会重新请求 Grok 搜索。
* 现在同一天内相同风格只会请求一次 Grok，后续直接命中缓存。

**5. 🔧 代码变更**

* `service.py`：新增 `_frozen_randoms` 字典、`_frozen_randoms_key()`、`_get_or_freeze_randoms()`、`clear_frozen_randoms()` 方法；`generate_schedule()` 新增 `force_regenerate` 参数；`save_generated()` 新增清除冻结值逻辑。
* `main.py`：`_generate_for_event()` 将 `force_regenerate` 传递给 `generate_schedule()`。

---

## v1.2.3 - 2026-04-19

**✨ 新增明日日程定制功能**

**1. 🎯 新增 `/明日日程` 命令**

* 用户可提前设置明天的定制日程要求。
* 例如：`/明日日程 穿洛丽塔，下午约会`。
* 明天自动生成时会自动应用该要求。

**2. 🎯 新增 `/取消明日日程` 命令**

* 取消已设置的明日定制日程要求。
* 取消后明天将正常随机生成。

**3. 💾 新增 pending_custom_requests 存储**

* 定制要求持久化存储，重启后仍有效。
* 每个人格独立存储。
* 自动清理过期数据。

**4. 📝 日志精简**

* `on_llm_request` 注入日程时的日志精简为：`[dayflow] 已注入日程: YYYY-MM-DD`

**5. 🔧 代码变更**

* `store.py`：新增 `pending_custom_requests` 存储及相关方法。
* `service.py`：新增 `set_tomorrow_custom_request`、`clear_tomorrow_custom_request`、`consume_pending_custom_request` 方法；自动调度时读取 pending_custom_requests。
* `main.py`：新增 `/明日日程` 和 `/取消明日日程` 命令。

---

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
