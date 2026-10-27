# 回复过滤与分段渲染

AstrBot 的 **OneBot v11 / aiocqhttp** 插件：清理大模型回复，短文本按标点分段并按所选模式过滤符号；**超过 150 字或包含 Markdown** 时，调用 [astrbot_plugin_browser](https://github.com/rytte/astrbot_plugin_browser/) 的共享 Chromium 渲染为 PNG 或 PDF。

不需要再启动第二套浏览器，不调用外部截图网站。

- 仓库：[Juscan/astrbot_plugin_reply_filter](https://github.com/Juscan/astrbot_plugin_reply_filter)
- 作者：**Jusc**
- 即将升级版本：**1.1.0**；变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 1.1.0：精细过滤，尤其适合自然聊天

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

这些是配置行为示例，不是本次新增模式已经完成本地或真实 QQ 验证的声明。

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
| `你好，今天开心吗？我很好！` | 顺序发送 `你好`、`今天开心吗`、`我很好` |
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
5. 保留原回复中的图片、文件等非文本组件及其顺序。混合消息会按各连续文本区分别渲染，不会把附件吞掉；因此一条混合回复可能生成多张图。

过滤只影响本插件发送给用户的显示内容，不重写模型上下文、数据库历史或其他插件直接发送的消息。

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
3. **升级 1.1.0 后确认依赖安装成功，尤其是新增的 `regex`。** 如依赖未自动安装，在 **AstrBot 使用的 Python 环境**安装 [requirements.txt](requirements.txt)：

   ```bash
   python -m pip install -r data/plugins/astrbot_plugin_reply_filter/requirements.txt
   ```

4. 重启或重载本插件。依赖安装或加载报错时，先查看 AstrBot 日志并解决问题，不要只保存配置后继续使用未重载的旧版本。
5. 按下面的配置页教程选择模式。默认 `force_non_streaming=true` 会对 OneBot 事件提前请求非流式；若仍出现逐字/逐段抢先发送，请关闭 AstrBot 当前会话/配置的流式输出，并排查其他插件。
6. 关闭 AstrBot 内置文本转图、分段、TTS 以及其他同类“最终回复处理”插件，避免冲突。它们不是本插件的渲染依赖。

ZIP 安装时，压缩包中应有 `astrbot_plugin_reply_filter/` 这一层目录，下一层即可看到 [main.py](main.py) 和 [metadata.yaml](metadata.yaml)。1.1.0 的插件包名为 `astrbot_plugin_reply_filter-1.1.0.zip`。

### 在 AstrBot 配置页启用自然聊天

不同 AstrBot 版本的按钮名称可能略有差异，按以下字段名寻找即可：

1. 打开 AstrBot WebUI → **插件管理**，找到“回复过滤与分段渲染”或 `astrbot_plugin_reply_filter`，打开该插件的**配置**。
2. 确认总开关 `enabled` 为开启。
3. 找到 `symbol_filter_mode` 的下拉选项，**从默认 `strict` 手动改选 `natural`**。仅升级版本、保持 `strict` 不变，仍是旧版严格过滤。
4. 保持 `strip_symbols=true`（去掉普通标点）和 `preserve_urls_numbers=true`（保护链接、邮箱、数字）；先保留默认 `split_symbols`。不必修改大模型提示词。
5. 按需设置 `render_mode`：`image` 为 PNG，`pdf` 为 PDF，`auto` 根据长度选择；不确定时保持 `image`。
6. 点击**保存**。安装/更新完成、依赖确认成功后重载插件；如当前界面提示配置需重载，也按提示执行。使用上面的自然聊天示例进行实际检查。
7. 需要额外规则时，将 `symbol_filter_mode` 改为 `custom`，再填 `custom_keep_symbols` / `custom_remove_symbols` 并保存。例如保留字段填 `,，`，可让中英文逗号留在短文本中且不按逗号分段。两个字段在 `strict` / `natural` 下不起作用。

### PDF 与分容器部署

默认 PNG 使用 `Image.fromBytes`，由适配器转为 base64，不要求 NapCat 读取 AstrBot 的图片磁盘路径。

PDF 使用 AstrBot `File` 组件：

- 同机部署时，协议端需要能够读取生成文件的本地路径。
- 分容器/分主机时，需共享**相同可读路径**，或正确配置 AstrBot 的 `callback_api_base`，并确保协议端可以访问 AstrBot 的文件回调地址。
- 本插件直接发送，不套用 RespondStage 的 `platform_settings.path_mapping`。仅改变路径前缀也不能保证文件回调/本地存在检查通过，不能把它当作已验证的跨容器方案。
- 先在自己的部署环境验证文件上传。仅 PNG 渲染成功不代表 PDF 文件上传成功。

**发送失败不自动重发。** OneBot 可能已经接受消息，只是确认超时；自动重试会导致重复消息。日志中会注明第几个发送组失败。文本回退针对的是**发送之前的渲染失败**，不是对文件上传失败的盲目重试。

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
| `max_messages` | `10` | 单次 LLM 结果最多几个 `event.send` 发送组，超出片段合并到最后组 |
| `send_interval` | `0.6` | 本插件同一回复各发送组之间的秒数 |
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

- 使用 `on_decorating_result` 处理最终消息链，发送后清掉当前结果，防止原文重复发送。
- AstrBot 的 `STREAMING_RESULT` 会绕过该钩子；`STREAMING_FINISH` 时消息已经发出。本插件不假装能收回已经发送的内容。
- 默认使用高优先级普通消息监听器提前请求非流式。这依赖当前 AstrBot 的 event extra 约定；其他插件仍可覆盖，**Live Mode 不支持**，不能保证所有第三方 runner 的流式行为。
- 普通监听器不调用 `send`、不设置回复、也不停止事件，不主动让未唤醒的群消息触发 LLM；但它会让这类 OneBot 消息多进入部分事件处理管线。
- 接管发送后不经过核心后续的回复前缀、自动 @/引用、内置转图、TTS、合并转发、RespondStage 路径映射和 `on_after_message_sent` 钩子。原消息链已有 @/引用会保留并尽量附着到内容消息。依赖这些行为的插件可能冲突。
- 同一会话在本插件内顺序发送，跨会话可以并发。其他插件/核心直接发送的消息不受这把锁约束；也不继承核心 `send_message_to_user` 的同文去重，不承诺跨工具/插件全局 exactly-once。
- 每一个 `LLM_RESULT` 独立处理；工具调用前后的多个模型回复不会因为一个事件级“处理过”标记而丢失。

### Markdown 与网络

支持 CommonMark 和表格、删除线。不包含联网语法高亮、LaTeX 数学排版或 Mermaid 渲染。

Markdown 转 HTML 时禁用原始 HTML，浏览器 JavaScript 关闭，并使用 CSP 禁止远程资源。Markdown 图片变成替代文字占位，不下载模型提供的图片地址、内网地址或本地文件。链接保留为可读/可点击文本，但渲染不打开目标。

过滤思考块是显示清理，不是通用防泄漏机制；`blocked_words` 也**不是敏感内容审核器**。严格模式去符号会改变运算式或表情语义；自然模式和有限颜文字启发式也不是所有文本的无损转换。需要精确保留时请关闭 `strip_symbols`，并检查分段和保护配置。

### 缓存与限额

PDF 缓存在 `data/plugin_data/astrbot_plugin_reply_filter/pdf/`，文件名使用随机 UUID。只清理本插件命名的过期 PDF，不删除其他文件。

单 PDF 上限 32 MiB，总缓存上限 256 MiB；缓存满时不淘汰可能正在上传的新文件，改走文本回退。过期清理是**生成新 PDF 时触发**，不是定时擦除；停用插件后如需删除历史回复，应手工清理该缓存目录。缓存包含回复正文，请保护磁盘权限。

超出发送组数量会合并尾部，**不截断内容**。OneBot 的文件/转发组件还可能被平台拆成更多物理消息，因此 `max_messages` 不是 QQ 消息数量或风控保证。极长文本的最后一个回退发送组仍可能超过协议端大小限制；本插件会记录发送失败，不承诺任意大小都能送达。

## 接口依据

- [AstrBot 输出钩子文档](https://docs.astrbot.app/dev/star/event-listener.html)
- [AstrBot 结果装饰管线：固定源码](https://github.com/AstrBotDevs/AstrBot/blob/dad255c60f7983fd63c7c0282ded1dde859fbc9a/astrbot/core/pipeline/result_decorate/stage.py)
- [browser 0.4.0 共享浏览器文档](https://github.com/rytte/astrbot_plugin_browser/blob/e87c2276dd7e5ed49b27319259f5d453a250bc57/docs/%E5%85%B1%E4%BA%AB%E6%B5%8F%E8%A7%88%E5%99%A8.md)
- [browser 公共 render_api.py](https://github.com/rytte/astrbot_plugin_browser/blob/e87c2276dd7e5ed49b27319259f5d453a250bc57/render_api.py)

本插件按请求动态取得 `get_registered_star("astrbot_plugin_browser").star_cls.service`，使用 `ready`、`render_html()`、`render_pdf()`；不接管外部浏览器的启动/关闭/重启。PDF 文件在本插件自己的数据目录，不存入插件源码目录。

## 许可证

[MIT](LICENSE)。
