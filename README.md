# 插件由ai辅助生成

# 回复过滤与分段渲染

AstrBot 的 **OneBot v11 / aiocqhttp** 插件：清理大模型回复，短文本按标点分段并去符号；**超过 150 字或包含 Markdown** 时，调用 [astrbot_plugin_browser](https://github.com/rytte/astrbot_plugin_browser/) 的共享 Chromium 渲染为 PNG 或 PDF。

不需要再启动第二套浏览器，不调用外部截图网站。

## 处理效果

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
3. 按清理后的**整条回复**决定路由：非空白 Unicode 字符数严格大于阈值，或 Markdown 语法树存在格式节点，即进入渲染。标点也计入字数；不是分词后的“词数”。
4. 只有短文本才分段、去符号。**不能先破坏 Markdown 再渲染。**
5. 保留原回复中的图片、文件等非文本组件及其顺序。混合消息会按各连续文本区分别渲染，不会把附件吞掉；因此一条混合回复可能生成多张图。

过滤只影响本插件发送给用户的显示内容，不重写模型上下文、数据库历史或其他插件直接发送的消息。

## 环境要求

- AstrBot **>= 4.27，< 5**。
- OneBot v11 适配器，即 AstrBot 中的平台类型 `aiocqhttp`；例如正确接入 AstrBot 的 NapCat / LLOneBot。未声明 OneBot v12、QQ 官方接口或其他平台兼容。
- 安装并启用 **astrbot_plugin_browser >= 0.4.0**，确保它自己的 Chromium 服务可以正常使用。
- Python >= 3.10；实际版本还须满足 AstrBot 和 browser 插件各自要求。
- 渲染机器需要可用的中文字体，例如 Noto Sans CJK SC / 微软雅黑。字体缺失请按 browser 插件文档配置。

外部 browser 插件不是 pip 依赖，**不会由本插件静默安装**。

## 安装

1. 在 AstrBot 插件管理中，先通过 `https://github.com/rytte/astrbot_plugin_browser` 安装并启用浏览器插件，按其文档完成浏览器环境安装。
2. 安装本仓库：发布后可在插件管理页填你的仓库地址；或将本目录放到 AstrBot 的 `data/plugins/astrbot_plugin_reply_filter/`。
3. 如依赖未自动安装，在 **AstrBot 使用的 Python 环境**安装 [requirements.txt](requirements.txt)：

   ```bash
   python -m pip install -r data/plugins/astrbot_plugin_reply_filter/requirements.txt
   ```

4. 重启或重载本插件，在配置页选择 `image`、`pdf` 或 `auto`。
5. 默认 `force_non_streaming=true` 会对 OneBot 事件提前请求非流式。若仍出现逐字/逐段抢先发送，请关闭 AstrBot 当前会话/配置的流式输出，并排查其他插件。
6. 关闭 AstrBot 内置文本转图、分段、TTS以及其他同类“最终回复处理”插件，避免冲突。它们不是本插件的渲染依赖。

ZIP 安装时，压缩包中应有 `astrbot_plugin_reply_filter/` 这一层目录，下一层即可看到 [main.py](main.py) 和 [metadata.yaml](metadata.yaml)。

### PDF 与分容器部署

默认 PNG 使用 `Image.fromBytes`，由适配器转为 base64，不要求 NapCat 读取 AstrBot 的图片磁盘路径。

PDF 使用 AstrBot `File` 组件：

- 同机部署时，协议端需要能够读取生成文件的本地路径。
- 分容器/分主机时，需共享**相同可读路径**，或正确配置 AstrBot 的 `callback_api_base`，并确保协议端可以访问 AstrBot 的文件回调地址。
- 本插件直接发送，不套用 RespondStage 的 `platform_settings.path_mapping`。仅改变路径前缀也不能保证文件回调/本地存在检查通过，不能把它当作已验证的跨容器方案。
- 先在自己的部署环境验证文件上传。仅 PNG 渲染成功不代表 PDF 文件上传成功。

**发送失败不自动重发。** OneBot 可能已经接受消息，只是确认超时；自动重试会导致重复消息。日志中会注明第几个发送组失败。文本回退针对的是**发送之前的渲染失败**，不是对文件上传失败的盲目重试。

## 配置

完整配置定义见 [_conf_schema.json](_conf_schema.json)。保存错误类型、越界或非有限数值会明确报错，不静默更改。

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `force_non_streaming` | `true` | 提前对 OneBot 事件写入 `enable_streaming=false` |
| `long_text_threshold` | `150` | 非空白字符严格大于此值才判长文本 |
| `render_mode` | `image` | `image` / `pdf` / `auto` |
| `auto_pdf_threshold` | `3000` | auto 模式严格大于此值改用 PDF |
| `pdf_on_image_error` | `true` | PNG 渲染失败后再试 PDF |
| `split_symbols` | 换行及中英文常用标点 | 逐字符分隔，不是正则；分隔符不发送 |
| `strip_symbols` | `true` | 短文本移除 Unicode P/S 类字符，包括大部分 emoji |
| `preserve_urls_numbers` | `true` | 保护 HTTP(S) URL、邮箱、小数、版本、日期、时间、百分比和带符号数字 |
| `remove_think` | `true` | 移除指定思考标签块，未闭合尾部也移除 |
| `blocked_words` | `[]` | 字符串列表，按列表顺序、区分大小写、字面替换为空 |
| `max_segment_length` | `150` | 短文本单段软上限；不拆坏保护的原子 |
| `max_messages` | `10` | 单次 LLM 结果最多几个 `event.send` 发送组，超出片段合并到最后组 |
| `send_interval` | `0.6` | 本插件同一回复各发送组之间的秒数 |
| `render_timeout` | `30.0` | 每次渲染总超时，含排队；PNG/PDF 分别计时 |
| `image_width` | `960` | 视口宽度，320～1920px |
| `font_size` | `22` | 图片字号，12～48px；PDF 使用打印字号 12pt |
| `max_render_chars` | `100000` | 单个文本区含空白的最大渲染字符数，超出则完整文本回退 |
| `cache_retention_hours` | `24` | 下次生成 PDF 时清理超时缓存，1～168小时 |

推荐：

- 日常聊天：保持默认设置。
- 不想删除 emoji/运算符：关闭 `strip_symbols`，分段仍有效。
- 所有符号都想删除，包括 URL/小数里的标点：关闭 `preserve_urls_numbers`，但这会损坏链接、邮箱和数字，请谨慎。
- 文档为主：`render_mode=auto`，PDF 在手机端可放大、搜索和复制。

默认短文本示例：

```text
输入：价格 -3.14，版本 v1.2.3，访问 https://example.com/a?x=1。
输出：价格 -3.14
      版本 v1.2.3
      访问 https://example.com/a?x=1
```

分隔符以外的标点/符号在去符号阶段直接删除，不额外创建段落；可以自行把需要分段的字符加入 `split_symbols`。URL 应与后文用空白或中文句末标点分开，插件不能可靠推断网址后紧贴的普通文字是否属于 URL。

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

过滤思考块是显示清理，不是通用防泄漏机制；`blocked_words` 也**不是敏感内容审核器**。默认去符号会改变运算式或表情语义；需要精确保留时请关闭该项。

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
