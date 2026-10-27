# 回复过滤与分段渲染

AstrBot 的 **OneBot v11 / aiocqhttp** 插件：清理大模型回复，短文本按标点分段并按所选模式过滤符号；**超过 150 字或包含 Markdown** 时，调用 [astrbot_plugin_browser](https://github.com/rytte/astrbot_plugin_browser/) 的共享 Chromium 渲染为 PNG 或 PDF。

不需要再启动第二套浏览器，不调用外部截图网站。

- 仓库：[Juscan/astrbot_plugin_reply_filter](https://github.com/Juscan/astrbot_plugin_reply_filter)
- 作者：**Jusc**
- 版本：**1.1.1**；变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 1.1.1：保留核心发送流程，修复表情插件协作

旧版在 `on_decorating_result` 内直接 `event.send()`，随后 `clear_result()`。后续插件的装饰钩子虽然仍会被调用，却读不到回复结果；正常 `RespondStage` 的 `after_message_sent` 流程也不会执行，导致依赖该流程补发的**非混发表情 pending 队列丢失**。问题不是“后续插件钩子完全没有调用”。

1.1.1 将装饰钩子优先级设为 **`100000`**，提前过滤、分段或渲染 LLM 文本，**原地修改原结果的 `chain`**，保留图片、文件、`At`、`Reply` 等非 `Plain` 组件、结果的 LLM 类型和 metadata，然后交给 AstrBot 正常核心发送。插件不自行 `send` / `clear_result` / `stop_event`，也不手动补发或调用其他插件钩子。

这样会尽量先完成文本过滤，再让常见优先级 **`99999`** 的表情插件附图；正文删空后，后续插件仍可选择只发图。**不保证所有插件的实际排序或兼容性，尚未完成真实 OneBot 联调。** 短文本多气泡现在依赖核心分段设置，旧 `send_interval` 不再控制发送，详见[多气泡配置](#短文本多气泡由-astrbot-核心发送)。

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

选择 `natural`，保持默认分段符号和其他默认设置：

```text
输入：你好呀～😊，今天也要开心(^_^)！
输出：你好呀～😊
      今天也要开心(^_^)
```

```text
输入：预算 ¥100，温度 26℃，加油 👍🏽！
输出：预算 ¥100
      温度 26℃
      加油 👍🏽
```

这些是文本处理行为示意，不表示 1.1.1 的核心发送与表情协作已完成本地或真实 QQ 验证。

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
| `你好，今天开心吗？我很好！` | 生成 `你好`、`今天开心吗`、`我很好` 三个文本段；默认同一消息内换行，启用核心分段后可分气泡 |
| 普通文本恰好 150 个非空白字符 | 仍按短文本处理 |
| 普通文本 151 个非空白字符 | 一张 PNG |
| `**重点**`、标题、列表、表格、代码块等 Markdown | 即使不到 150 字，也渲染 PNG |
| 配置 `render_mode=pdf` | 需要渲染时，发送 A4 PDF 文件 |
| 配置 `render_mode=auto` | 需要渲染且超过 3000 字时 PDF，否则 PNG |
| 图片渲染失败 | 默认尝试 PDF；仍失败则完整文本回退 |

**顺序很重要：**

1. 只接管 OneBot 的非流式 `LLM_RESULT`，不拦截普通命令回复。
2. 合并相邻文本组件，移除 `think` / `thinking` / `analysis` 标签及内容、异常控制字符、配置的字面词句。
3. 按清理后的**整条回复**决定路由：非空白 Unicode 字符数严格大于阈值，或 Markdown 语法树存在格式节点，即进入渲染。标点也计入字数；不是分词后的“词数”。识别出的颜文字不单独触发 Markdown 误判，不影响真实 Markdown 的识别。
4. 只有短文本才分段、按所选模式去符号。**不能先破坏 Markdown 再渲染。**
5. 保留原回复中的图片、文件等非 `Plain` 组件及其链内相对顺序。混合消息会按各连续文本区分别渲染，不会把附件吞掉；因此一条混合回复可能生成多张图。
6. 将处理后的文本段或渲染组件写回原结果的 `chain`，保留结果对象、LLM 类型与 metadata，由后续插件和核心决定最终发送。发生未处理异常时保留原结果交给框架，不清空结果或吞掉附件；这也意味着清理失败时原文可能继续发送，本插件不是内容安全审核器。

过滤只影响交给核心发送的显示内容，不重写模型上下文、数据库历史或其他插件直接发送的消息。

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
3. **升级至 1.1.1 后确认依赖安装成功；从 1.0.0 升级时尤其留意 1.1.0 新增的 `regex`。** 如依赖未自动安装，在 **AstrBot 使用的 Python 环境**安装 [requirements.txt](requirements.txt)：

   ```bash
   python -m pip install -r data/plugins/astrbot_plugin_reply_filter/requirements.txt
   ```

4. 重启或重载本插件。依赖安装或加载报错时，先查看 AstrBot 日志并解决问题，不要只保存配置后继续使用未重载的旧版本。
5. 按下面的配置页教程选择模式。默认 `force_non_streaming=true` 会对 OneBot 事件提前请求非流式；若仍出现逐字/逐段抢先发送，请关闭 AstrBot 当前会话/配置的流式输出，并排查其他插件。
6. 建议关闭核心文本转图（t2i）、TTS、合并转发，以及会替换整条消息链的同类处理，避免覆盖处理结果或丢失混链附件。核心 t2i 在插件装饰钩子之后执行，可能将整链替换；关闭它不影响本插件自己的 browser 渲染。本插件不会暗改用户的核心配置，也不调用 `result.use_t2i(False)`。**不要为此关闭核心分段**；需要多气泡时按下文启用。

ZIP 安装时，压缩包中应有 `astrbot_plugin_reply_filter/` 这一层目录，下一层即可看到 [main.py](main.py) 和 [metadata.yaml](metadata.yaml)。1.1.1 的插件包名为 `astrbot_plugin_reply_filter-1.1.1.zip`。

### 在 AstrBot 配置页启用自然聊天

不同 AstrBot 版本的按钮名称可能略有差异，按以下字段名寻找即可：

1. 打开 AstrBot WebUI → **插件管理**，找到“回复过滤与分段渲染”或 `astrbot_plugin_reply_filter`，打开该插件的**配置**。
2. 确认总开关 `enabled` 为开启。
3. 找到 `symbol_filter_mode` 的下拉选项，**从默认 `strict` 手动改选 `natural`**。仅升级版本、保持 `strict` 不变，仍是旧版严格过滤。
4. 保持 `strip_symbols=true`（去掉普通标点）和 `preserve_urls_numbers=true`（保护链接、邮箱、数字）；先保留默认 `split_symbols`。不必修改大模型提示词。
5. 按需设置 `render_mode`：`image` 为 PNG，`pdf` 为 PDF，`auto` 根据长度选择；不确定时保持 `image`。
6. 点击**保存**。安装/更新完成、依赖确认成功后重载插件；如当前界面提示配置需重载，也按提示执行。使用上面的自然聊天示例进行实际检查。
7. 需要额外规则时，将 `symbol_filter_mode` 改为 `custom`，再填 `custom_keep_symbols` / `custom_remove_symbols` 并保存。例如保留字段填 `,，`，可让中英文逗号留在短文本中且不按逗号分段。两个字段在 `strict` / `natural` 下不起作用。

### 短文本多气泡：由 AstrBot 核心发送

1.1.1 只把短文本切成同一 `chain` 中的多个 `Plain`，不逐段调用 API。**未开启核心分段时，这些短文本段通常在一个消息内以换行显示，不是多个气泡。** 旧配置 `send_interval` 仅为升级兼容保留，不再控制实际发送间隔。

需要多个气泡时，请打开 **AstrBot 当前会话实际使用的配置**，找到 `platform_settings.segmented_reply`，逐项设置：

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

核心分段按消息组件（`Comp`）发送，图片等组件也可能单独形成消息；**首个组件发送前也会等待**，并非仅段间等待。核心还会对文本 `strip()`，因此首尾空白不保证保留。这些是核心行为，不是本插件每段调用 API 或自己的发送节流；核心、适配器及其他插件可能产生更多消息。

### PDF 与分容器部署

默认 PNG 使用 `Image.fromBytes`，由适配器转为 base64，不要求 NapCat 读取 AstrBot 的图片磁盘路径。

PDF 使用 AstrBot `File` 组件：

- 同机部署时，协议端需要能够读取生成文件的本地路径，或能通过核心提供的文件回调取得文件。
- 1.1.1 交由核心 `RespondStage` 发送，会遵循 `platform_settings.path_mapping`。分容器/分主机时仍须提供正确的共享文件与可读路径映射，或正确配置 `callback_api_base` 并确保协议端可访问文件回调 URL。
- 路径映射只负责路径转换，不会自动共享文件，也不保证回调 URL 可达。**跨容器 PDF 方案尚未完成真实环境验证**；仅 PNG 渲染成功不代表 PDF 文件上传成功。

本插件不自行重试发送。OneBot 可能已经接受消息，只是确认超时；重复发送会造成重复消息。实际发送、去重和异常处理交给核心及适配器，不承诺无重发或 exactly-once。文本回退针对的是**发送之前的渲染失败**，不是对文件上传失败的盲目重试。

## 配置参考

完整配置定义见 [_conf_schema.json](_conf_schema.json)。保存错误类型、越界或非有限数值会明确报错，不静默更改。

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `force_non_streaming` | `true` | 提前对 OneBot 事件写入 `enable_streaming=false` |
| `long_text_threshold` | `150` | 非空白字符严格大于此值才判长文本 |
| `render_mode` | `image` | `image` / `pdf` / `auto` |
| `auto_pdf_threshold` | `3000` | auto 模式严格大于此值改用 PDF |
| `pdf_on_image_error` | `true` | PNG 渲染失败后再试 PDF |
| `split_symbols` | 换行及中英文常用标点 | 逐字符分隔，不是正则；分隔符不发送，保护/保留项优先且不分段 |
| `strip_symbols` | `true` | 启用所选模式的短文本符号移除；关闭时也不执行自定义删除，但仍分段 |
| `symbol_filter_mode` | `strict` | `strict` 兼容旧版；`natural` 自然聊天；`custom` 在自然模式上自定义 |
| `custom_keep_symbols` | `""` | 仅 custom 生效；逐字符保留，优先于分段；长度 ≤ 256，不是正则 |
| `custom_remove_symbols` | `""` | 仅 custom 生效；逐字符删除，优先于保留；命中表情内任一字符则整原子删除；长度 ≤ 256 |
| `preserve_urls_numbers` | `true` | 最高优先级保护 HTTP(S) URL、邮箱、小数、版本、日期、时间、百分比和带符号数字 |
| `remove_think` | `true` | 移除指定思考标签块，未闭合尾部也移除 |
| `blocked_words` | `[]` | 字符串列表，按列表顺序、区分大小写、字面替换为空 |
| `max_segment_length` | `150` | 短文本单段软上限；不拆坏保护的原子 |
| `max_messages` | `10` | 文本段数量的软上限；仅合并相邻 `Plain`，不跨附件合并或丢弃附件，不是最终消息数上限 |
| `send_interval` | `0.6` | 仅为升级兼容保留，不再控制实际发送；间隔由核心分段配置决定 |
| `render_timeout` | `30.0` | 每次渲染总超时，含排队；PNG/PDF 分别计时 |
| `image_width` | `960` | 视口宽度，320～1920px |
| `font_size` | `22` | 图片字号，12～48px；PDF 使用打印字号 12pt |
| `max_render_chars` | `100000` | 单个文本区含空白的最大渲染字符数，超出则完整文本回退 |
| `cache_retention_hours` | `24` | 下次生成 PDF 时清理超时缓存，1～168 小时 |

推荐：

- 日常聊天：**手动选择 `symbol_filter_mode=natural`**；默认 `strict` 是为兼容旧版保留的。
- 有针对性的保留/删除需求：选择 `custom`，再填写两个自定义字段。
- 不想进行符号移除：关闭 `strip_symbols`；分段仍有效，保护/保留项仍不拆分。
- 希望过滤 URL/小数里的符号：必须关闭 `preserve_urls_numbers` 后再按模式配置，但这会损坏链接、邮箱和数字，请谨慎。
- 文档为主：`render_mode=auto`，PDF 在手机端可放大、搜索和复制。

默认严格模式短文本示例：

```text
输入：价格 -3.14，版本 v1.2.3，访问 https://example.com/a?x=1。
输出：价格 -3.14
      版本 v1.2.3
      访问 https://example.com/a?x=1
```

分隔符以外的标点/符号按所选模式处理；去符号阶段的删除不额外创建段落。可自行把需要分段的字符加入 `split_symbols`，但保护/保留项始终优先。URL 应与后文用空白或中文句末标点分开，插件不能可靠推断网址后紧贴的普通文字是否属于 URL。

## 限制与安全说明

### 流式与生命周期

- `on_decorating_result(priority=100000)` 仅原地装饰 OneBot 非流式 `LLM_RESULT` 的消息链，保留原结果类型和 metadata；不自行发送、清空结果、停止事件，也不手动调用或补发其他插件钩子。
- `100000` 可早于常见 `99999` 表情插件处理，但不是通用的“最早优先级”；其他插件可使用更高优先级、改写链或自行发送，不保证所有插件排序或兼容性。正文滤空后后续插件可附图，仅图结果也可继续正常核心发送。
- 保留正常核心发送路径后，核心回复前缀、自动 @ / quote、路径映射、`send_message_to_user` 的同文去重与 `on_after_message_sent` 可按核心条件执行。**最终空链、空白文本或只有 `At` / `Reply` 时，核心仍可能跳过发送和 after 钩子**，不能保证每个事件都触发 after。
- 原链中的 `At` / `Reply` 会保留，但启用核心分段时，核心会将它们移至**首个非语音组件**，不保证任意原位置。核心分段、去重和适配器行为均由所用 AstrBot 版本决定；不承诺跨会话严格顺序、无重发或全局 exactly-once。
- 核心文本转图、TTS、合并转发等整链转换仍可在后续覆盖结果，建议关闭；本插件不会替用户修改这些核心配置。核心文本转图并不是本插件 browser 渲染的依赖。
- AstrBot 的 `STREAMING_RESULT` 会绕过该钩子；`STREAMING_FINISH` 时消息已经发出。本插件不假装能收回已经发送的内容。
- 默认使用高优先级普通消息监听器提前请求非流式。这依赖当前 AstrBot 的 event extra 约定；其他插件仍可覆盖，**Live Mode 不支持**，不能保证所有第三方 runner 的流式行为。
- 普通监听器不调用 `send`、不设置回复、也不停止事件，不主动让未唤醒的群消息触发 LLM；但它会让这类 OneBot 消息多进入部分事件处理管线。
- 每一个 `LLM_RESULT` 独立处理；工具调用前后的多个模型回复不会因为一个事件级“处理过”标记而丢失。

### Markdown 与网络

支持 CommonMark 和表格、删除线。不包含联网语法高亮、LaTeX 数学排版或 Mermaid 渲染。

Markdown 转 HTML 时禁用原始 HTML，浏览器 JavaScript 关闭，并使用 CSP 禁止远程资源。Markdown 图片变成替代文字占位，不下载模型提供的图片地址、内网地址或本地文件。链接保留为可读/可点击文本，但渲染不打开目标。

本插件**不是内容安全审核器**。过滤思考块是显示清理，不是通用防泄漏机制；`blocked_words` 也不是敏感内容审核器。发生未处理异常时保留原结果给框架，原文可能继续发送，不提供 fail-closed 内容拦截。严格模式去符号会改变运算式或表情语义；自然模式和有限颜文字启发式也不是所有文本的无损转换。需要精确保留时请关闭 `strip_symbols`，并检查分段和保护配置。

### 缓存与限额

PDF 缓存在 `data/plugin_data/astrbot_plugin_reply_filter/pdf/`，文件名使用随机 UUID。只清理本插件命名的过期 PDF，不删除其他文件。

单 PDF 上限 32 MiB，总缓存上限 256 MiB；缓存满时不淘汰可能正在上传的新文件，改走文本回退。过期清理是**生成新 PDF 时触发**，不是定时擦除；停用插件后如需删除历史回复，应手工清理该缓存目录。缓存包含回复正文，请保护磁盘权限。

`max_messages` 只是**文本段软上限**：仅在相邻 `Plain` 之间合并，不跨图片、文件等附件合并，也不为达上限丢弃附件或截断内容。混链因附件隔开而无法继续合并时可以超过上限；核心按组件分段、其他插件追加内容、OneBot 文件/转发拆分也可能增加实际消息数。因此它不是 QQ 消息数量或风控保证。极长回退文本仍可能超过协议端大小限制，最终发送由框架处理，不承诺任意大小都能送达。

## 接口依据

- [AstrBot 输出钩子文档](https://docs.astrbot.app/dev/star/event-listener.html)
- [AstrBot 结果装饰管线：固定源码](https://github.com/AstrBotDevs/AstrBot/blob/dad255c60f7983fd63c7c0282ded1dde859fbc9a/astrbot/core/pipeline/result_decorate/stage.py)
- [AstrBot 正常发送与 after 钩子：固定源码](https://github.com/AstrBotDevs/AstrBot/blob/dad255c60f7983fd63c7c0282ded1dde859fbc9a/astrbot/core/pipeline/respond/stage.py)
- [代表性表情包管理插件的装饰与发送后补图逻辑](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/d0843c16eac11b19d1ca64a394749ee13fc93805/mixins/event_handlers.py#L1470-L1668)（源码核对，不等于对用户部署完成联调）
- [browser 0.4.0 共享浏览器文档](https://github.com/rytte/astrbot_plugin_browser/blob/e87c2276dd7e5ed49b27319259f5d453a250bc57/docs/%E5%85%B1%E4%BA%AB%E6%B5%8F%E8%A7%88%E5%99%A8.md)
- [browser 公共 render_api.py](https://github.com/rytte/astrbot_plugin_browser/blob/e87c2276dd7e5ed49b27319259f5d453a250bc57/render_api.py)

本插件按请求动态取得 `get_registered_star("astrbot_plugin_browser").star_cls.service`，使用 `ready`、`render_html()`、`render_pdf()`；不接管外部浏览器的启动/关闭/重启。PDF 文件在本插件自己的数据目录，不存入插件源码目录。

## 许可证

[MIT](LICENSE)。
