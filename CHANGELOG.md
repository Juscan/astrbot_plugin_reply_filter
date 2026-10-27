# 更新记录

仓库：[Juscan/astrbot_plugin_reply_filter](https://github.com/Juscan/astrbot_plugin_reply_filter) · 作者：**Jusc**

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
