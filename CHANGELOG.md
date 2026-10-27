# 更新记录

仓库：[Juscan/astrbot_plugin_reply_filter](https://github.com/Juscan/astrbot_plugin_reply_filter) · 作者：**Jusc**

## 1.1.2

### 恢复默认独立气泡与间隔

- 新增 `send_mode`，默认 **`segments`**：普通纯文本短回复直接按段独立气泡，无需开启核心 `segmented_reply`；`send_interval` 默认 **0.6 秒**，恢复为每次提前发送后的等待，设为 `0` 可关闭插件等待。不将网络及框架处理耗时承诺为精确定时。
- 可选 **`send_mode=core`** 保留 1.1.1 的仅原位装饰行为，整链交核心、不提前发送；此模式的间隔由核心分段配置控制，不使用插件 `send_interval`。README 保留完整核心多气泡教程，但改为仅供 core 选择的可选小节，不再作为默认安装步骤。
- segments **仅提前发送链首连续 `Plain` 的前 n−1 段**；最后一段及其后的全部图片、表情、文件与交错内容仍留在原 LLM 结果/chain，供后续装饰与正常 `RespondStage` 发送。插件不 `clear_result` / `stop_event`，不手动 after、不代发表情或附件，也不替其他插件补发。
- 遇到链首 `At` / `Reply` / 图片等非 `Plain` 不跨过去抢发，附件边界后的文本保留换行但可能合在核心尾部；原始非 `Plain` 对象与顺序保留。这是附件安全例外，不承诺任意交错混链都独立气泡，也不要求表情独占气泡。
- 保留尾部让已核对的 `priority=99999` 表情插件仍有附图与 after pending 处理机会；正文滤空仍保留附件及后续附图机会。最终全空不保证发送或 after，不将源码分析或模拟测试当作实际 OneBot 联调结论。

### 发送异常与跨插件边界

- 每次提前发送前从原链移除即将尝试的前缀，防止核心随后重复发送；发送异常或取消不自动重试，未尝试尾部仍保留给核心，可能合成一条。确认失败可能是 OneBot 已接收，也可能未送达；既不保证失败段不丢失，也不承诺 exactly-once。
- 提前发出的纯文本不会再经过后续装饰、核心前缀、自动 @ / quote、路径映射或 after；这些只可能作用于保留给核心的尾部，不能声称每段都走核心或 after。必须由后续插件查看、重写或审核整条未发送回复时，应选择 core 并实际验证。
- after 仍按核心实际条件执行；触发 after 不等于 OneBot 已确认成功。插件不吞取消不代表整条管线终止，所用框架可能捕获异常/取消后继续。
- 渲染或等待期间如结果/链已被替换，不强行覆盖新链；提前发送内容无法撤回。不承诺所有插件排序、跨会话严格顺序、无重发或普遍兼容。
- 默认 segments 建议关闭此前按 1.1.1 教程启用的核心分段，以及核心 t2i / TTS / 合并转发，避免二次处理保留尾部。本插件不暗改用户核心配置。所有图片/PDF 保留给核心，可按核心条件使用路径映射；跨容器共享文件、可读路径和回调 URL 仍须实际配置与验收。

### 保持行为与发布

- 独立发送的短文本段不人为添加尾换行，混链核心尾部可用换行分隔；`max_messages` 仍是文本段软上限，只合并相邻 `Plain`，多出短段在尾段以换行合并，不跨附件、不丢附件或截断内容。渲染失败回退按原文本拼接，不凭空新增分隔换行。
- 保持 strict / natural / custom、默认 strict、自然模式完整 emoji / 有限颜文字保护与规则优先级；保持严格大于 150 个非空白字符或真正 Markdown 才渲染、browser 共享 Chromium、安全限制与 PDF 缓存边界。
- 安装包改为 `astrbot_plugin_reply_filter-1.1.2.zip`，Release 改为 `v1.1.2`；保留 1.1.1 / 1.1.0 历史记录与旧 Release。

### 验证状态

- **Windows / Python 3.12 本地通过 226 项测试（203 项 core 回归 + 23 项 1.1.2 新增分段发送测试）、Ruff 与编译检查。** 发布校验脚本与打包脚本成功，产出 `astrbot_plugin_reply_filter-1.1.2.zip`（含 23 个文件）。
- 已覆盖默认独立气泡与间隔、core 仅装饰、链首连续文本边界、前置 At / Reply / 附件及交错混链不抢发、异常与取消后的未尝试尾部、并发链替换，以及保留核心尾部的后续附图/after 条件。测试使用 AstrBot / browser 桩和独立模拟管线；真实 OneBot、图片/PDF 端到端、跨容器 PDF 与远端 CI 仍须单独验收。

## 1.1.1

以下为当时的历史行为；当前默认发送方式与兼容边界以 1.1.2 为准。

### 修复回复生命周期与表情插件协作

- 修复真正原因：旧 `on_decorating_result` 直接 `event.send()` 后 `clear_result()`，后续插件装饰钩子虽然仍调用却读不到结果；正常 `RespondStage` 的 `after_message_sent` 不执行，非混发表情的 pending 队列因而丢失。不是“后续插件钩子未调用”。
- 改用 `priority=100000` 提前处理 OneBot 非流式 LLM 文本，过滤 / 分段 / 渲染后**原地修改原结果的 `chain`**，保留非 `Plain` 组件及相对顺序、结果的 LLM 类型和 metadata，交正常核心发送。不自行 `send` / `clear_result` / `stop_event`，不手动调用或补发其他插件钩子。
- 尽量先过滤，再让常见 `priority=99999` 的表情插件附图；正文滤空后它仍可选择只发图。`100000` 并非通用“最早优先级”，不承诺所有插件排序或兼容性，也不声称已完成真实 OneBot 联调。
- 恢复正常核心路径中的回复前缀、自动 @ / quote、路径映射、同文去重及 after 钩子；均按核心条件执行。最终空链、空白文本或只有 `At` / `Reply` 仍可能被核心跳过发送与 after。核心分段会把原 `At` / `Reply` 移至首个非语音组件，不承诺任意原位置、跨会话严格顺序、无重发或全局 exactly-once。
- 未处理异常时保留原结果交框架，不清空结果或吞附件；原文可能继续发送，**本插件不是内容安全审核器**。发送及异常处理交核心 / 适配器，本插件不自行重试。

### 兼容变化与核心多气泡配置

- 旧 `send_interval` 仅为升级兼容保留，**不再控制实际发送间隔**。
- `max_messages` 改为**文本段软上限**，仅合并相邻 `Plain`，不跨附件合并、不丢附件、不截断内容；核心按组件发送及其他插件追加仍可能产生更多消息，不是最终 API 调用数或 QQ 消息数上限。
- 未开启核心分段时，同一 chain 中的短文本段在一个消息内以换行显示。需要多气泡，在 AstrBot 当前配置的 `platform_settings.segmented_reply` 设置 `enable=true`、`only_llm_result=true`、`split_mode='regex'`、原始正则 `[\s\S]+`、`content_cleanup_rule=''`、`interval_method='random'`、`interval='0.6,0.6'`。
- 核心分段按每个 `Comp` 发送，首个组件前也等待，并会对文本 `strip()`；不是本插件逐段调用 API。[README.md](README.md) 提供局部 JSON（正确转义反斜杠）及配置页逐项设置，不要求覆盖整个配置文件，UI 字段展示可能随 AstrBot 版本不同。
- 删除“关闭内置分段”的旧建议；仍建议关闭核心 t2i / TTS / 合并转发等整链替换处理。核心 t2i 在插件装饰之后执行，可能覆盖结果并丢失混链图片；本插件不暗改核心配置，也不调用 `result.use_t2i(False)`，关闭核心 t2i 不影响本插件 browser 渲染。
- PDF 重新遵循核心 `platform_settings.path_mapping`；跨容器仍需共享文件及可读路径，或可达的文件回调 URL。路径映射不等于自动共享文件，不宣称跨容器 PDF 已验证。

### 保持行为与发布

- 保持 1.1.0 的 strict / natural / custom 模式、默认 strict、自然模式符号与完整 emoji / 有限颜文字保护、自定义规则优先级；升级不自动选择 natural。
- 保持先清理、再按整条回复路由：默认恰好 150 个非空白字符仍为短文本，严格大于 150 或真正 Markdown 才渲染；长文本 / Markdown 不套用短文本符号规则。
- 保持 browser 共享 Chromium、禁用原始 HTML / JavaScript / 远程资源、渲染失败文本回退和 PDF 缓存边界。
- 更新安装包名为 `astrbot_plugin_reply_filter-1.1.1.zip`，GitHub 更新与 Release 步骤改为 `v1.1.1`；保留以下 1.1.0 历史记录，发布时不覆盖旧 Release。

### 验证状态

- Windows / Python 3.12 本地通过 **203 项测试**、Ruff 与编译检查。覆盖结果 / chain 身份和 LLM metadata、媒体顺序、文本软合并、异常回退、渲染中结果变更、取消传播及自然模式回归。
- 新增明确区分的模拟核心测试：后续附图、滤空后改为仅图、正常发送后 pending 补图各一次，以及空链 / 空白 / 仅 At Reply 不保证 after。没有由过滤钩子自行发送、清空、停止事件或手动补发来掩盖生命周期错误。
- 测试使用 AstrBot / browser 桩和独立模拟管线，不代表真实框架运行。真实 OneBot 表情协作、图片 / PDF 端到端、跨容器 PDF 以及远端 CI 仍须单独验收。

## 1.1.0

### 精细过滤与自然聊天

- 新增 `symbol_filter_mode`：`strict` / `natural` / `custom`。默认 `strict` 兼容旧版；自然聊天需要在 AstrBot 配置页**手动选择 `natural`**，不用修改大模型提示词。
- `natural` 移除普通标点，保留 Unicode S 类符号及 `~～…%％‰‱@#&/\_*-`，完整保留家庭 ZWJ、肤色、旗帜、键帽等 emoji 组合。
- 通过有限启发式完整保护常见颜文字，如 `(^_^)`、`(｡･ω･｡)ﾉ`、`(๑•̀ㅂ•́)و`、`T_T`、`>_<`、`:-)`、`¯\_(ツ)_/¯`。不承诺识别全部颜文字，普通括号内容不受保护。
- 已识别颜文字不会仅因 `>_<` 被误判为 Markdown；真正 Markdown 仍然渲染。
- 新增 `custom_keep_symbols` / `custom_remove_symbols`，默认空字符串，仅在 `custom` 生效，各自长度 ≤ 256；按字符匹配，不是正则。custom 以 natural 为基础，删除优先于保留；删除命中 emoji / 颜文字内部任一字符时整原子删除。
- URL、邮箱、结构化数字的保护优先级最高；需先关闭 `preserve_urls_numbers` 才能用符号规则修改其内部字符。
- 保护/保留项优先于 `split_symbols`，例如 natural 波浪线、custom 保留的逗号不分段。
- `strip_symbols=false` 关闭所有符号移除，包括 custom 删除；分段规则仍执行，但保护/保留项不分段。

### 保持原有管线与安全约束

- 仍然先清理思考块、异常控制字符、字面词句，再按整条回复路由；默认严格大于 150 个非空白字符或包含真正 Markdown 才渲染。
- 长文本 / Markdown 使用清理后的原文渲染，不应用短文本去符号或自定义删除；保留附件及顺序、渲染失败完整文本回退等行为。
- 保留 OneBot 非流式处理、发送失败不自动重发、浏览器禁用 JavaScript / 原始 HTML / 远程资源、PDF 缓存和跨容器部署等既有边界。详细限制见 [README.md](README.md)。

### 依赖、使用与发布说明

- 新增 `regex` 依赖。更新后确认 [requirements.txt](requirements.txt) 中依赖安装成功，**再重载插件**。
- [README.md](README.md) 新增 AstrBot 配置页操作教程、自然聊天示例和 custom 优先级说明；更新现有 GitHub 仓库的网页步骤，保留 Git 可选方式。
- 1.1.0 安装包名：`astrbot_plugin_reply_filter-1.1.0.zip`。

### 验证状态

- Windows / Python 3.12 本地通过 187 项测试（123 项原有回归 + 64 项新增模式测试）、Ruff 检查和编译检查。
- 修复数字保护截断键帽 emoji、全零算式误判颜文字、颜文字式链接目的地漏判 Markdown；修复 PDF 缓存并发清理时文件在符号链接检查阶段消失导致写入中断的竞态。
- 测试使用模拟 AstrBot / browser，不代表真实 OneBot 图片 / PDF 端到端验收已完成，也不代表远端 CI 已运行。发布前仍需确认本次 CI 和实际部署验收。

## 1.0.0

- 初始功能：OneBot v11 非流式 LLM 回复清理、短文本分段 / 去符号，以及借用 browser 共享 Chromium 的 PNG / PDF 渲染。
- 包含 URL / 邮箱 / 结构化数字保护、附件顺序保留、渲染失败文本回退、缓存限额和发送节流等能力。
- 旧版本测试可跑通为用户报告，不代表 1.1.0 新增功能的验证结论。
