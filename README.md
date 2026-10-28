# 回复过滤与分段渲染

AstrBot 的 **OneBot v11 / aiocqhttp** 插件：清理大模型回复，短文本按标点分段并按所选模式过滤符号，**普通纯文本短回复默认每段独立气泡、间隔 0.6 秒**；**超过 150 字或包含 Markdown** 时，调用 [astrbot_plugin_browser](https://github.com/rytte/astrbot_plugin_browser/) 的共享 Chromium 渲染为 PNG 或 PDF。

不需要再启动第二套浏览器，不调用外部截图网站。

- 仓库：[Juscan/astrbot_plugin_reply_filter](https://github.com/Juscan/astrbot_plugin_reply_filter)
- 作者：**Jusc**
- 版本：**1.1.3**；变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 1.1.3：取消本次回复时丢弃旧尾部

用户在 **AstrBot 4.29.3** 中报告：`main.py` 的 `_send_prefix` 在段间 `sleep` 被新消息打断并得到 `CancelledError` 后，出现持续不回复。**这条日志只说明取消发生的位置，不能单凭它证明永久无响应的完整根因。** 已核对精确 `v4.29.3` 源码：结果装饰阶段对每个装饰 hook 捕获 `BaseException`；旧版仅重新抛出取消时，尚未发送的尾部仍在结果中，框架可能继续后续装饰或发送。

1.1.3 在 `filter_reply` 的整个准备（含渲染）、发送与等待过程捕获 `CancelledError`，并以 `_check_cancelled` 的当前任务取消计数检查，防止依赖吞取消后继续提交或发送。`_discard_cancelled_reply(event)` **仅在取消路径**设置 event-local `agent_stop_requested=true`，清空该事件当前结果的 `chain` 并 `event.clear_result()`，随后以 **裸 `raise` 继续传播取消**；插件不主动吞取消，不 `stop_event`、不 `uncancel` / `recancel`，不重试、不启动后台 sender。本次未发送文本与附件一并丢弃，已发出的消息无法撤回。

不调用 `stop_event`，是为了让 **AstrBot 4.29.3 在既有的 `BaseException` 捕获之后**，按正常路径耗尽上游持锁异步生成器，避免以 STOP 提前 `break` 后依赖生成器终结器延迟收尾；这不是对所有框架、锁或服务清理的保证。插件清理后继续传播取消，内部渲染/发送等服务函数也仍传播，不清零任务取消计数；**4.29.3 框架仍会捕获并记录取消**，不能把插件继续 `raise` 当作框架顶层任务一定标记为 cancelled 的保证。`agent_stop_requested` 是已核对的 4.29.3 协作停止 flag，用于避免旧 agent 在工具中间 yield 后继续；它不是稳定公开的 runner API，不保证即时停止运行中的服务或所有第三方 runner。清空结果让按 `None` 检查的后续装饰早退，也不等于保证任何第三方 hook 都不会另建结果。

**外部停止边界：**入口、渲染提交及分段发送处显式检查 `event.is_stopped()`，已停止时不继续提交或发送。若打断插件已经调用 `stop_event()`，清空结果不能撤销 STOP，scheduler 仍可能提前 `break` 并依赖生成器终结器收尾；本插件不会调用 `continue_event()` 撤销其他代码的停止决定。不能据此保证外部 STOP、服务不响应取消或第三方挂起的所有场景均恢复。

**正常路径不清空结果、不停止事件**：仍默认 `segments` 独立气泡、间隔 0.6 秒，最后一段及附件留给后续装饰、核心发送与符合条件的 after，不吞掉正常表情。普通 `Exception` 发送失败仍保留未尝试尾部交核心；这与 `CancelledError` 丢弃旧尾部是两条不同路径。新请求使用独立 event，不应继承旧 event 的协作停止标记；取消路径不设置全局锁或会话禁用标记。

AstrBot 4.29.3 仍可能打印 `CancelledError`；**不能以日志消失作为修复判断，日志本身也不是再次卡死的证据**。真实 OneBot 环境下持续不回复的复现与修复验收仍待完成，也不承诺无条件释放所有锁或取消所有服务。永久无响应的完整原因仍需结合用户实际使用的打断插件、相关开关及打断前后完整日志排查；严重卡住时可临时关闭本插件并重启 AstrBot，保留现场信息，不要暗改核心配置。验证状态见[测试与验证](#测试与验证)。

## 1.1.2 历史新增：恢复默认独立气泡与发送间隔

1.1.1 改为只装饰原结果，避免旧版 `event.send()` 后 `clear_result()` 干扰表情插件；但多气泡依赖额外开启核心分段。1.1.2 恢复第一版的日常使用方式：**默认 `send_mode=segments`，无需开启 AstrBot 核心 `segmented_reply`，普通纯文本短回复每段一个气泡，`send_interval=0.6` 再次控制段间等待。**

为保留附件和后续插件的处理机会，正常路径中 `segments` **只提前发送链首连续 `Plain` 段的前 n−1 段**，每次发送后等待配置间隔；最后一段及其后的全部附件、交错内容仍留在原 LLM 结果及其 `chain` 中，交后续装饰钩子和正常 `RespondStage`。插件不代发图片、表情、文件等附件；遇到前置 `At` / `Reply` / 图片等非 `Plain` 组件，不跨过它们抢发。混链边界后的文本保留换行，但可能合在最后一条，**不是任意交错混链都独立气泡**。

正常路径不 `clear_result` / `stop_event`，不手动调用 after 或替其他插件补发；1.1.3 仅在取消路径清空当前结果链、`clear_result()` 并重新抛出取消，不调用 `stop_event()`。已核对的 `priority=99999` 表情插件仍可向保留尾部附图，或由核心尾部的 after 流程处理 pending 队列；**这不是对所有插件的兼容保证，也不是实际 OneBot 联调结论。** 可选 `send_mode=core` 保留 1.1.1 的仅原位装饰方式，整链不提前发送；只有这种模式需要可选核心分段配置来获得多个气泡。提前发送的文本段不会再经过后续装饰或核心发送处理，前缀、自动 quote、路径映射及 after 等只可能作用于保留给核心的尾部，详见[发送模式](#发送模式独立气泡与核心兼容)。

## 精细过滤，尤其适合自然聊天（1.1.0 引入）

新增 `symbol_filter_mode`，提供 `strict`、`natural`、`custom` 三种短文本符号过滤模式。**默认仍为 `strict`，兼容旧版行为；升级后不会自动变成自然模式。** 日常聊天请在 AstrBot 插件配置页**手动选择 `natural`**，不需要修改大模型提示词。

| 模式 | 短文本行为（`strip_symbols=true` 时） |
| --- | --- |
| `strict` | 兼容旧版：移除 Unicode P（标点）/ S（符号）类字符，包括大部分 emoji；URL、邮箱、结构化数字仍按保护开关处理 |
| `natural` | 去掉普通标点，保留 Unicode S 类符号、下列聊天符号、完整 emoji 组合和已识别的常见颜文字 |
| `custom` | 以 `natural` 为基础，再应用 `custom_keep_symbols` / `custom_remove_symbols`；删除优先于保留 |

自然模式额外保留的聊天符号是 `~～…%％‰‱@#&/\_*-`。emoji 组合按整体保护，例如家庭 ZWJ `👨‍👩‍👧‍👦`、肤色 `👍🏽`、旗帜 `🇨🇳`、键帽 `1️⃣`，不会拆成零散字符。

常见颜文字示例：`(^_^)`、`(｡･ω･｡)ﾉ`、`(๑•̀ㅂ•́)و`、`T_T`、`>_<`、`:-)`、`¯\_(ツ)_/¯`。识别采用**有限启发式**，不承诺识别所有颜文字；**普通括号内容不受保护**，不会因为文字放在括号里就完整保留括号。已识别颜文字不会仅因其中的 `>_<` 被误判为 Markdown；真正的 Markdown 仍按原规则渲染。

### 自然聊天示例

选择 `natural`，保持默认分段符号、`send_mode=segments` 和其他默认设置：

```text
输入：你好呀～😊，今天也要开心(^_^)！
气泡 1：你好呀～😊
气泡 2：今天也要开心(^_^)
```

```text
输入：预算 ¥100，温度 26℃，加油 👍🏽！
气泡 1：预算 ¥100
气泡 2：温度 26℃
气泡 3：加油 👍🏽
```

段间默认等待 0.6 秒；这些是正常路径的预期文本处理与发送方式示意，不表示已完成 1.1.3 的真实 QQ 验证。

### 自定义与优先级

- `custom_keep_symbols`、`custom_remove_symbols` 默认都是空字符串，**只在 `custom` 模式生效**；各自长度不超过 **256 个字符**。按字符串中的每个字符匹配，**不是正则表达式**，也不是逗号分隔的规则列表。
- `custom` 以 `natural` 为基础。一个字符同时出现在保留和删除列表时，**删除优先**。删除列表命中已识别 emoji 或颜文字内部的任一字符时，**删除整个原子**，不会残留半个表情。例如删除 `🏽` 会移除整个 `👍🏽`。
- `preserve_urls_numbers=true` 时，HTTP(S) URL、邮箱和结构化数字的保护**优先级最高**，自定义删除也不能修改它们。确实要过滤其中的符号，必须先关闭 `preserve_urls_numbers`；这可能破坏链接、邮箱或数字格式。
- **保留字符优先于 `split_symbols`**：自然模式保留的波浪线即使出现在分段列表中也不拆；自定义保留 `,，` 后，这两个逗号不删除、也不分段。完整的受保护 emoji、颜文字和 URL/数字原子同样不从内部拆开。
- `strip_symbols=false` 会关闭符号移除，**包括 `custom_remove_symbols` 的删除**，但 `split_symbols` 分段仍执行，分隔符仍不发送；保护/保留项不参与分段。
- 以上模式只作用于**短文本**。大于阈值或真正 Markdown 的渲染输入仍是经过思考块、控制字符、字面词句清理后的原文，**不应用短文本去符号或自定义删除规则**。

## 处理效果与运行管线

| 清理后的回复 | 默认行为 |
| --- | --- |
| `你好，今天开心吗？我很好！` | `你好`、`今天开心吗`、`我很好` 三个独立气泡，段间默认等待 0.6 秒，无需核心分段 |
| 普通文本恰好 150 个非空白字符 | 仍按短文本处理 |
| 普通文本 151 个非空白字符 | 一张 PNG |
| `**重点**`、标题、列表、表格、代码块等 Markdown | 即使不到 150 字，也渲染 PNG |
| 配置 `render_mode=pdf` | 需要渲染时，发送 A4 PDF 文件 |
| 配置 `render_mode=auto` | 需要渲染且超过 3000 字时 PDF，否则 PNG |
| 图片渲染普通失败（非取消） | 默认尝试 PDF；仍失败则完整文本回退 |

**顺序很重要：**

1. 只处理 OneBot 的非流式 `LLM_RESULT`，不拦截普通命令回复。
2. 合并相邻文本组件，移除 `think` / `thinking` / `analysis` 标签及内容、异常控制字符、配置的字面词句。
3. 按清理后的**整条回复**决定路由：非空白 Unicode 字符数严格大于阈值，或 Markdown 语法树存在格式节点，即进入渲染。标点也计入字数；不是分词后的“词数”。识别出的颜文字不单独触发 Markdown 误判，不影响真实 Markdown 的识别。
4. 只有短文本才分段、按所选模式去符号。**不能先破坏 Markdown 再渲染。** 独立发送的短文本段不人为添加尾换行；留给核心的混链尾部可用换行分隔。超过文本段软上限时，多出的相邻短段可在尾段用换行合并，不截断内容。普通渲染失败回退按原文本拼接，不额外插入分隔换行；取消不走文本回退。
5. 正常路径保留原回复中的图片、文件等所有非 `Plain` 对象及顺序，不由本插件提前发送附件。混合消息按各连续文本区分别渲染，因此可能生成多张图；生成的图片/PDF 同样留给核心。
6. 正常路径将处理结果原位写回原 LLM 结果。默认 `segments` 只提前发送链首连续 `Plain` 的前 n−1 段，保留最后一段及后续所有组件；链首遇非 `Plain` 就不抢发，不跨附件边界继续提前发送。混链尾部的文本可用换行保留分段，但实际可能同条显示，不强求附件独占气泡。可选 `core` 不提前发送，整链留给后续插件和核心。
7. 提前发送前，先从原链剔除即将尝试的前缀，防止框架随后重复发送。普通 `Exception` 发送失败不自动重试：OneBot 可能已接受消息，只是确认失败；未尝试的后缀仍留给核心，可能合成一条。失败段也可能未送达，不保证不丢失。
8. `CancelledError` 与普通发送失败分开处理：准备、渲染、发送、等待或任务取消计数检查发现取消时，设置本 event 的 `agent_stop_requested=true`，清空当前结果链并 `clear_result()`，最终输出钩子以裸 `raise` 继续传播取消；不 `stop_event` / `uncancel` / `recancel`，不续发本次旧文本或附件，不重试、不后台续发。内部服务函数仍传播取消；4.29.3 框架按既有逻辑捕获取消并记录日志后，可在结果为 `None` 时继续正常排空管线。正常路径遇到并发替换不强行覆写新链；已发送内容无法撤回，不承诺 exactly-once。

过滤只改变显示内容，不重写模型上下文、数据库历史或其他插件直接发送的消息。未处理的普通清理/渲染异常会尽量保留原结果交框架，原文可能继续发送；这不包括上述取消丢弃路径，本插件不是内容安全审核器。已经尝试发送的前缀不会因失败而自动回填或重发。

## 环境要求

- AstrBot **>= 4.27，< 5**。
- OneBot v11 适配器，即 AstrBot 中的平台类型 `aiocqhttp`；例如正确接入 AstrBot 的 NapCat / LLOneBot。未声明 OneBot v12、QQ 官方接口或其他平台兼容。
- 安装并启用 **astrbot_plugin_browser >= 0.4.0**，确保它自己的 Chromium 服务可以正常使用。
- Python >= 3.10；实际版本还须满足 AstrBot 和 browser 插件各自要求。
- 渲染机器需要可用的中文字体，例如 Noto Sans CJK SC / 微软雅黑。字体缺失请按 browser 插件文档配置。
- 1.1.0 新增 Python 依赖 **`regex`**，用于完整 emoji 组合等处理；更新后请确认 [requirements.txt](requirements.txt) 中的依赖安装成功，再重载插件。

外部 browser 插件不是 pip 依赖，**不会由本插件静默安装**。

## 安装与更新

1. 在 AstrBot 插件管理中，先通过 `https://github.com/rytte/astrbot_plugin_browser` 安装并启用浏览器插件，按其文档完成浏览器环境安装。
2. 安装本插件时，在插件管理页填入 `https://github.com/Juscan/astrbot_plugin_reply_filter`；已有安装则使用插件管理页的更新操作。也可将本目录放到 AstrBot 的 `data/plugins/astrbot_plugin_reply_filter/`。
3. **升级至 1.1.3 后确认依赖安装成功；从 1.0.0 升级时尤其留意 1.1.0 新增的 `regex`。** 如依赖未自动安装，在 **AstrBot 使用的 Python 环境**安装 [requirements.txt](requirements.txt)：

   ```bash
   python -m pip install -r data/plugins/astrbot_plugin_reply_filter/requirements.txt
   ```

4. 重启或重载本插件。依赖安装或加载报错时，先查看 AstrBot 日志并解决问题，不要只保存配置后继续使用未重载的旧版本。
5. 普通纯文本短回复保持默认 `send_mode=segments`、`send_interval=0.6` 即为独立气泡。**若曾按 1.1.1 教程开启核心分段，建议关闭核心 `segmented_reply`，避免保留尾部再次被处理。** 不需要为默认模式配置核心分段正则；前置引用/@ 或混链附件属于下文的安全例外。
6. 默认 `force_non_streaming=true` 会对 OneBot 事件提前请求非流式；若仍出现逐字/逐段抢先发送，请关闭 AstrBot 当前会话/配置的流式输出，并排查其他插件。
7. 建议关闭核心文本转图（t2i）、TTS、合并转发，以及会替换整条消息链的同类处理，避免二次处理保留尾部或丢失混链附件。核心 t2i 在插件装饰钩子之后执行，可能将整链替换；关闭它不影响本插件自己的 browser 渲染。本插件不会暗改用户的核心配置，也不调用 `result.use_t2i(False)`。仅当选择 `send_mode=core` 且需要多气泡时，才按可选教程开启核心分段。

ZIP 安装时，压缩包中应有 `astrbot_plugin_reply_filter/` 这一层目录，下一层即可看到 [main.py](main.py) 和 [metadata.yaml](metadata.yaml)。1.1.3 的插件包名为 `astrbot_plugin_reply_filter-1.1.3.zip`。

### 在 AstrBot 配置页启用自然聊天

不同 AstrBot 版本的按钮名称可能略有差异，按以下字段名寻找即可：

1. 打开 AstrBot WebUI → **插件管理**，找到“回复过滤与分段渲染”或 `astrbot_plugin_reply_filter`，打开该插件的**配置**。
2. 确认总开关 `enabled` 为开启，`send_mode=segments`、`send_interval=0.6`；无需另开核心分段。
3. 找到 `symbol_filter_mode` 的下拉选项，**从默认 `strict` 手动改选 `natural`**。仅升级版本、保持 `strict` 不变，仍是旧版严格过滤。
4. 保持 `strip_symbols=true`（去掉普通标点）和 `preserve_urls_numbers=true`（保护链接、邮箱、数字）；先保留默认 `split_symbols`。不必修改大模型提示词。
5. 按需设置 `render_mode`：`image` 为 PNG，`pdf` 为 PDF，`auto` 根据长度选择；不确定时保持 `image`。
6. 点击**保存**。安装/更新完成、依赖确认成功后重载插件；如当前界面提示配置需重载，也按提示执行。使用上面的自然聊天示例进行实际检查。
7. 需要额外规则时，将 `symbol_filter_mode` 改为 `custom`，再填 `custom_keep_symbols` / `custom_remove_symbols` 并保存。例如保留字段填 `,，`，可让中英文逗号留在短文本中且不按逗号分段。两个字段在 `strict` / `natural` 下不起作用。

### 发送模式：独立气泡与核心兼容

以下描述正常路径；1.1.3 取消路径会设置本 event 的协作停止 flag、清空当前结果链并 `clear_result()` 后重新抛出取消，不调用 `stop_event()`，也不保留旧尾部供继续发送。

| 配置 | 行为与用途 |
| --- | --- |
| `send_mode=segments`（默认） | 普通纯文本短回复每段独立气泡；只提前发送链首连续 `Plain` 的前 n−1 段，最后一段及后续组件留给后续装饰与核心；无需核心分段 |
| `send_interval=0.6` | segments 模式下，每次提前发送后等待的秒数；设为 `0` 关闭插件等待。框架处理和网络耗时会额外增加间隔，并非严格定时保证 |
| `send_mode=core` | 只原位装饰结果，不提前发送；整链由核心处理。不开核心分段时通常在同一消息显示，具体拼接由核心/适配器决定 |

默认 `segments` 是普通纯文本短回复多气泡的推荐方式。为了让表情插件还能附图，**至少保留链首最后一段及其后的全部组件**；保留尾部可同时包含正文和附件，也可由后续插件补成仅图片。链首若有 `At` / `Reply` / 图片等非 `Plain`，不跨过它们抢发；附件边界后文本不再独立提前发送，可用换行保留分段并合在核心尾部。这是附件安全例外，不承诺任意混链全独立气泡，也不保证表情独占气泡。正文滤空时仍保留附件及后续附图机会；最终全空不保证发送或 after。

**兼容边界：**提前发送的纯文本不再经过后续 `on_decorating_result`、核心前缀、自动 @ / quote、路径映射及 after；这些处理只可能作用于保留给核心的尾部，不能声称每个气泡都经过核心或触发 after。其他插件若必须查看、重写或审核整条未发送的回复，应改选 `core` 并实际验证。

#### 可选：core 模式的 AstrBot 核心多气泡教程

**仅供 `send_mode=core` 使用，不是默认模式的安装步骤。** `core` 不逐段调用 API，`send_interval` 也不控制核心间隔。需要多个气泡时，打开 **AstrBot 当前会话实际使用的配置**，找到 `platform_settings.segmented_reply`：

1. 启用分段回复：`enable=true`。
2. 仅处理 LLM 结果：`only_llm_result=true`。
3. 分段方式：`split_mode='regex'`。
4. 正则表达式：原始字符串 **`[\s\S]+`**，不加引号或捕获组。它匹配每个已有文本组件的全部内容，避免把本插件分好的文本再按标点拆一次。
5. 内容清理规则：`content_cleanup_rule=''`（空字符串）。
6. 间隔方式：`interval_method='random'`；间隔范围：`interval='0.6,0.6'`。
7. 保存并按界面提示使当前配置生效，再验证实际发送。不同 AstrBot 版本的页面名称或字段展示可能不同，请以所用版本为准。

以下仅展示**相关字段的局部 JSON**，用于对照；请合并到现有字段，保留其他配置，**不要用它覆盖整个配置文件**。JSON 里的反斜杠要写两次，配置页正则输入框则填写上面的原始字符串：

```json
{
  "platform_settings": {
    "segmented_reply": {
      "enable": true,
      "only_llm_result": true,
      "split_mode": "regex",
      "regex": "[\\s\\S]+",
      "content_cleanup_rule": "",
      "interval_method": "random",
      "interval": "0.6,0.6"
    }
  }
}
```

核心分段按消息组件（`Comp`）发送，图片等组件也可能单独形成消息；**首个组件发送前也会等待**，并非仅段间等待。核心还会对文本 `strip()`，并可能移动 `At` / `Reply`，不是本插件的发送节流。核心、适配器及其他插件可能产生更多消息；切回 `segments` 时建议关闭这套核心分段设置。

### PDF 与分容器部署

默认 PNG 使用 `Image.fromBytes`，由适配器转为 base64，不要求 NapCat 读取 AstrBot 的图片磁盘路径。

PDF 使用 AstrBot `File` 组件：

- 同机部署时，协议端需要能够读取生成文件的本地路径，或能通过核心提供的文件回调取得文件。
- 正常路径中本插件不提前代发附件：`core` 整链、`segments` 保留的全部图片/PDF 都由核心 `RespondStage` 处理，可按核心配置使用 `platform_settings.path_mapping`。segments 提前发送的纯文本不经过该核心路径映射，但不会把 PDF 抢先发走。取消时本次未发送图片/PDF 随旧尾部一起丢弃，不再交核心续发。
- 分容器/分主机仍须提供正确的共享文件与可读路径，或正确配置 `callback_api_base` 并确保协议端可访问文件回调 URL。路径映射只负责路径转换，不会自动共享文件，也不保证回调 URL 可达。**跨容器 PDF 方案尚未完成真实环境验证**；仅 PNG 渲染成功不代表 PDF 文件上传成功。

本插件不自动重试发送。OneBot 可能已经接受消息，只是确认超时；重复发送会造成重复消息。segments 普通 `Exception` 发送失败时不重发已尝试的前缀，尚未尝试的后缀留给核心，可能合成一条；失败段也可能未送达，不保证不丢失。`CancelledError` 路径则设置本 event 的协作停止 flag、清空当前结果链并 `clear_result()`，最终输出钩子重新抛出取消，不 `stop_event`，不保留旧尾部续发。核心及适配器仍有各自的发送与异常处理，不承诺无重发或 exactly-once。文本回退针对的是**发送之前的普通渲染失败**，不是取消后的续发，也不是对文件上传失败的盲目重试。

## 配置参考

完整配置定义见 [_conf_schema.json](_conf_schema.json)。保存错误类型、越界或非有限数值会明确报错，不静默更改。

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `force_non_streaming` | `true` | 提前对 OneBot 事件写入 `enable_streaming=false` |
| `send_mode` | `segments` | 默认独立气泡；可选 `core` 仅装饰原链，整链由核心发送 |
| `send_interval` | `0.6` | segments 每次提前发送后的等待秒数；`0` 关闭插件等待；core 模式不使用 |
| `long_text_threshold` | `150` | 非空白字符严格大于此值才判长文本 |
| `render_mode` | `image` | `image` / `pdf` / `auto` |
| `auto_pdf_threshold` | `3000` | auto 模式严格大于此值改用 PDF |
| `pdf_on_image_error` | `true` | PNG 普通渲染失败（非取消）后再试 PDF |
| `split_symbols` | 换行及中英文常用标点 | 逐字符分隔，不是正则；分隔符不发送，保护/保留项优先且不分段 |
| `strip_symbols` | `true` | 启用所选模式的短文本符号移除；关闭时也不执行自定义删除，但仍分段 |
| `symbol_filter_mode` | `strict` | `strict` 兼容旧版；`natural` 自然聊天；`custom` 在自然模式上自定义 |
| `custom_keep_symbols` | `""` | 仅 custom 生效；逐字符保留，优先于分段；长度 ≤ 256，不是正则 |
| `custom_remove_symbols` | `""` | 仅 custom 生效；逐字符删除，优先于保留；命中表情内任一字符则整原子删除；长度 ≤ 256 |
| `preserve_urls_numbers` | `true` | 最高优先级保护 HTTP(S) URL、邮箱、小数、版本、日期、时间、百分比和带符号数字 |
| `remove_think` | `true` | 移除指定思考标签块，未闭合尾部也移除 |
| `blocked_words` | `[]` | 字符串列表，按列表顺序、区分大小写、字面替换为空 |
| `max_segment_length` | `150` | 短文本单段软上限；不拆坏保护的原子 |
| `max_messages` | `10` | 文本段软上限；只合并相邻 `Plain`，多出短段在尾段用换行合并；不跨附件、不丢内容，不是最终消息数上限 |
| `render_timeout` | `30.0` | 每次渲染总超时，含排队；PNG/PDF 分别计时 |
| `image_width` | `960` | 视口宽度，320～1920px |
| `font_size` | `22` | 图片字号，12～48px；PDF 使用打印字号 12pt |
| `max_render_chars` | `100000` | 单个文本区含空白的最大渲染字符数，超出则完整文本回退 |
| `cache_retention_hours` | `24` | 下次生成 PDF 时清理超时缓存，1～168 小时 |

推荐：

- 日常聊天：保持 **`send_mode=segments`、`send_interval=0.6`**，关闭核心分段，并**手动选择 `symbol_filter_mode=natural`**；默认 `strict` 是为兼容旧版保留的。
- 下游插件必须处理整条未发送回复：改选 `send_mode=core`，需要多气泡时再配置核心分段。
- 有针对性的保留/删除需求：选择 `custom`，再填写两个自定义字段。
- 不想进行符号移除：关闭 `strip_symbols`；分段仍有效，保护/保留项仍不拆分。
- 希望过滤 URL/小数里的符号：必须关闭 `preserve_urls_numbers` 后再按模式配置，但这会损坏链接、邮箱和数字，请谨慎。
- 文档为主：`render_mode=auto`，PDF 在手机端可放大、搜索和复制。

默认严格模式短文本示例：

```text
输入：价格 -3.14，版本 v1.2.3，访问 https://example.com/a?x=1。
气泡 1：价格 -3.14
气泡 2：版本 v1.2.3
气泡 3：访问 https://example.com/a?x=1
```

分隔符以外的标点/符号按所选模式处理；去符号阶段的删除不额外创建段落。可自行把需要分段的字符加入 `split_symbols`，但保护/保留项始终优先。URL 应与后文用空白或中文句末标点分开，插件不能可靠推断网址后紧贴的普通文字是否属于 URL。

## 限制与安全说明

### 流式与生命周期

- `on_decorating_result(priority=100000)` 只处理 OneBot 非流式 `LLM_RESULT`。正常路径保留原结果、LLM 类型、metadata 和非文本对象；默认 segments 只提前发送链首连续 `Plain` 的前 n−1 段，最后一段及其后的附件/交错内容留给核心，core 只原位装饰。两种模式的**正常路径**均不清空结果、不停止事件、不代发附件、不手动调用 after 或替其他插件补发；**取消路径**则设置本 event 的 `agent_stop_requested=true`，清空当前结果链并 `clear_result()` 后重新抛出取消，放弃本次未发送文本与附件，不 `stop_event`，不手动清零或重新发起任务取消。
- `100000` 可早于已核对的 `99999` 表情插件装饰处理，但不是通用的“最早优先级”；其他插件可使用更高优先级、改写链或自行发送，不保证所有插件排序或兼容性。正文滤空后后续插件仍可附图；不意味着每个事件必然有图或必然发送。
- **segments 提前发出的纯文本不经过后续装饰和核心发送；核心前缀、自动 @ / quote、路径映射、同文去重及 after 只可能作用于保留尾部。** core 则按核心实际条件处理整链。最终空链、空白文本或只有 `At` / `Reply` 时，核心仍可能跳过发送与 after；after 触发本身也不等于 OneBot 已确认成功。
- 原链的 `At` / `Reply` 会保留；链首出现它们或其他非 `Plain` 时不跨过去抢发。因此带前置引用/@ 或交错附件的回复可能由核心合在一条，不保证所有气泡带 @ / 引用或所有混链独立分段。启用核心分段时，核心还可能移动它们，不保证任意原位置。已发送的纯文本不能被后续插件收回、审核或重写；有此需求请使用 core。
- 插件不自动重试普通 `Exception` 发送失败，未尝试尾部仍留给核心；`CancelledError` 或任务取消计数检查发现取消则丢弃本次旧尾并重新抛出取消。AstrBot 4.29.3 每个装饰 hook 外层捕获 `BaseException`，仅重新抛出取消不能保证整条管线停止；1.1.3 清理当前结果并设置本 event 的协作停止 flag 后继续传播取消，内部服务函数也仍传播，不清零任务取消计数。4.29.3 框架仍会捕获并记录取消，不能把插件级传播当作框架顶层取消传播或终止所有外部服务的保证。不重试与移除已尝试前缀是避免重复的防护，不保证失败段不丢失，也不是全局 exactly-once 保证；不承诺跨会话严格顺序。
- 不使用 `stop_event()` 结束本插件的取消路径，以便核心正常耗尽上游异步生成器；但已有的外部 STOP 不会被清空结果撤销，也不会被本插件用 `continue_event()` 撤销，scheduler 仍可能提前退出并依赖终结器收尾。event-local `agent_stop_requested` 只是已核对的 4.29.3 协作停止 flag，不是稳定公开 runner API；不能保证第三方 runner、挂起插件或不响应取消的服务都恢复。
- 正常路径在渲染或等待期间若原结果/链已被其他代码替换，不强行覆盖新链；取消路径会清空该事件当前结果链并 `clear_result()`。已提前发送的消息无法撤回。核心文本转图、TTS、合并转发等后续转换仍可覆盖正常保留尾部，建议关闭；本插件不会替用户修改这些配置。
- 新请求使用独立 event；不得将旧请求的取消/停止状态沿用到新请求。`CancelledError` 日志仍可能出现，不以日志消失判断恢复，也不承诺无条件释放所有锁。持续卡住需记录打断插件、相关开关和前后完整日志；可临时关闭本插件并重启 AstrBot 排查，不暗改核心配置。
- AstrBot 的 `STREAMING_RESULT` 会绕过该钩子；`STREAMING_FINISH` 时消息已经发出。本插件不假装能收回已经发送的内容。
- 默认使用高优先级普通消息监听器提前请求非流式。这依赖当前 AstrBot 的 event extra 约定；其他插件仍可覆盖，**Live Mode 不支持**，不能保证所有第三方 runner 的流式行为。
- 普通监听器不调用 `send`、不设置回复、也不停止事件，不主动让未唤醒的群消息触发 LLM；但它会让这类 OneBot 消息多进入部分事件处理管线。
- 正常路径中每一个 `LLM_RESULT` 独立处理；工具调用前后的多个模型回复不会因为一个事件级“处理过”标记而丢失。取消时则协作停止本 event 的旧 agent，不续发旧结果。

### Markdown 与网络

支持 CommonMark 和表格、删除线。不包含联网语法高亮、LaTeX 数学排版或 Mermaid 渲染。

Markdown 转 HTML 时禁用原始 HTML，浏览器 JavaScript 关闭，并使用 CSP 禁止远程资源。Markdown 图片变成替代文字占位，不下载模型提供的图片地址、内网地址或本地文件。链接保留为可读/可点击文本，但渲染不打开目标。

本插件**不是内容安全审核器**。过滤思考块是显示清理，不是通用防泄漏机制；`blocked_words` 也不是敏感内容审核器。清理/渲染阶段未处理的普通异常发生时，原文可能继续由框架发送，不提供 fail-closed 内容拦截；`CancelledError` 的旧尾丢弃仅服务于取消生命周期，不是内容审核。严格模式去符号会改变运算式或表情语义；自然模式和有限颜文字启发式也不是所有文本的无损转换。需要精确保留时请关闭 `strip_symbols`，并检查分段和保护配置。

### 缓存与限额

PDF 缓存在 `data/plugin_data/astrbot_plugin_reply_filter/pdf/`，文件名使用随机 UUID。只清理本插件命名的过期 PDF，不删除其他文件。

单 PDF 上限 32 MiB，总缓存上限 256 MiB；缓存满时不淘汰可能正在上传的新文件，改走文本回退。过期清理是**生成新 PDF 时触发**，不是定时擦除；停用插件后如需删除历史回复，应手工清理该缓存目录。缓存包含回复正文，请保护磁盘权限。

`max_messages` 只是**文本段软上限**：仅在相邻 `Plain` 之间合并，多出的短段可在尾段用换行合并；不跨图片、文件等附件合并，也不为达上限丢弃附件或截断内容。混链因附件隔开而无法继续合并时可以超过上限；核心按组件分段、其他插件追加内容、OneBot 文件/转发拆分也可能增加实际消息数。因此它不是 QQ 消息数量或风控保证。极长回退文本仍可能超过协议端大小限制，不承诺任意大小都能送达。

## 接口依据

- [AstrBot 输出钩子文档](https://docs.astrbot.app/dev/star/event-listener.html)
- [AstrBot v4.29.3 结果装饰阶段：精确 tag 源码](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/result_decorate/stage.py)（已核对每个装饰 hook 的 `BaseException` 捕获；不代表真实部署验收）
- [AstrBot v4.29.3 scheduler：精确 tag 源码](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/scheduler.py)（已核对；不代表永久无响应根因已完整确定）
- [AstrBot 结果装饰管线：固定源码](https://github.com/AstrBotDevs/AstrBot/blob/dad255c60f7983fd63c7c0282ded1dde859fbc9a/astrbot/core/pipeline/result_decorate/stage.py)
- [AstrBot 正常发送与 after 钩子：固定源码](https://github.com/AstrBotDevs/AstrBot/blob/dad255c60f7983fd63c7c0282ded1dde859fbc9a/astrbot/core/pipeline/respond/stage.py)
- [代表性表情包管理插件的装饰与发送后补图逻辑](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/d0843c16eac11b19d1ca64a394749ee13fc93805/mixins/event_handlers.py#L1470-L1668)（源码核对，不等于对用户部署完成联调）
- [browser 0.4.0 共享浏览器文档](https://github.com/rytte/astrbot_plugin_browser/blob/e87c2276dd7e5ed49b27319259f5d453a250bc57/docs/%E5%85%B1%E4%BA%AB%E6%B5%8F%E8%A7%88%E5%99%A8.md)
- [browser 公共 render_api.py](https://github.com/rytte/astrbot_plugin_browser/blob/e87c2276dd7e5ed49b27319259f5d453a250bc57/render_api.py)

本插件按请求动态取得 `get_registered_star("astrbot_plugin_browser").star_cls.service`，使用 `ready`、`render_html()`、`render_pdf()`；不接管外部浏览器的启动/关闭/重启。PDF 文件在本插件自己的数据目录，不存入插件源码目录。

## 许可证

[MIT](LICENSE)。
