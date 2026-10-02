# Capture · 流量工作台

基于 mitmproxy 的本地抓包工具。后端 Python，前端原生 HTML/CSS/JavaScript，不需要 Node 构建。

第一次使用请看[使用指南](capture/support/docs/使用指南.md)；修改代码或让 Agent 接手开发请看[架构与模块地图](capture/support/docs/架构与模块_AI.md)。
后续改进与本轮稳定性修复记录在[优化建议与检查记录](capture/support/docs/优化建议与检查记录.md)。

## 启动

要求 Python 3.12 或更高版本；本项目已经使用 `.venv` 安装依赖。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Windows 使用 `.venv\Scripts\python.exe`。当前采集通道使用 Unix socket，首版主要验证 macOS；Windows 适配尚未验证。

打开 `http://127.0.0.1:8765`，管理服务启动后代理即开始转发，点击“开始抓包”才创建会话并保存请求。客户端设置 HTTP/HTTPS 代理 `127.0.0.1:8080`。HTTPS 解密先阅读 [证书安装](capture/support/docs/证书安装.md)。默认列表解密且列表为空，TLS 全部透传。

启动配置为 `startup.toml`，支持管理端口、自动开始抓包和第三方 Hook 模块。首次启动的常用代理配置在 `config.py` 的 `DEFAULT_SETTINGS` 字典中。也可以执行 `python main.py --config /path/to/startup.toml`。
MCP 已支持条件查询、参数来源与链路候选分析、请求对比、编辑重放和开发者查询日志，参见 [MCP 接入](capture/support/docs/MCP接入.md)。

## 当前功能

- [三维请求参数关系网](capture/support/docs/数据链路.md)：选字段追踪关联请求、节点/参数高亮浮窗、分支展开、实时增量、人工备注与视图保存；分析独立进程运行。

- 独立 mitmdump 进程常驻转发；“开始抓包”启用记录，“停止抓包”仅停止记录，不断开客户端代理连接。退出管理服务时才终止代理。
- TLS 全部解密 / 列表解密 / 全部透传；拒绝开关与域名列表，拒绝优先。
- WebSocket 实时更新；域名 / URL 搜索、分页、单选、多选、Shift 连选与筛选结果选择。
- 顶部“筛选”提供常用条件，“多条件筛选”可添加条件和子组；每组自行选择“全部满足 AND”或“任一满足 OR”，子组即括号，界面会预览实际逻辑与当前会话匹配数量。支持域名、URL、方法、状态码（200 / 4xx / 400-499）、记录状态、来源、响应类型、请求/响应头、错误信息、耗时和大小。应用后列表、分页与“选择筛选结果”共用同一规则；目录筛选仍叠加生效。最多 4 层、15 组、30 个条件，暂不筛选正文。
- 原始请求、实际请求与响应详情，保留重复 Header 和原始正文。
- “完整查看”弹窗按需读取已保存的完整报文；JSON 响应支持格式化文本和可折叠树，大数组分批展开。侧栏和弹窗均提供爬虫工具外链，不自动发送抓包内容。
- 请求重放、编辑重放、批量次数和间隔、取消任务。重放使用 httpx，不执行 hook、不自动跟随重定向。
- cURL、Python/httpx、HAR、CSV、JSON 导出。一次最多导出 1000 条记录。
- 操作菜单支持删除选中请求、清空当前批次全部请求、删除当前批次；重放菜单支持单批次删除与清空。删除需确认且无法恢复，正在执行的抓包或重放必须先停止。
- 每次开始抓包创建独立的 SQLite 会话目录。界面仅显示本次程序启动创建的会话，重新启动后从空列表开始；历史目录仅保留，不自动加载或修改。
- 中文证书文档与公开 CA 下载；文档代码支持逐行及整段复制，保留原始缩进。
- 白色 / 黑色主题，主题自动保存；筛选浮层、弹窗和详情展开动画，遵循系统“减少动态效果”设置。实时刷新不重复播放动画。
- 列表表头固定；搜索、筛选、多条件筛选和“重放选中”直接显示，批量选择、导出、删除与重放次数/间隔在“更多”菜单中。窄窗口工具栏自动重排；点击请求才展示详情，窄窗口使用侧边抽屉，可关闭或按 Esc 收起。
- 请求列表展示完整 URL，支持域名 → 路径目录树；抓包和重放都可按目录及子目录筛选。
- 侧栏仅保留请求目录；拖动分隔条调整宽度，低于 80px 完全收起，收起后向右拖动 24px 即展开；侧栏边缘居中的按钮支持点击收起或展开、按住拖动调宽；按住 280ms 进入拖动模式，长按未移动也不触发点击；移动超过 6px 可直接拖动，顶部按钮恢复，宽度与状态自动记忆。顶部“重放记录”切换本次启动的重放批次，“抓包列表”返回最近抓包；历史记录仍在设置中管理。
- 设置支持直连或 HTTP / HTTPS 外部代理（可选用户名密码），抓包与重放共用。修改连接方式前须停止抓包。
- 顶部动态显示本机路由选出的局域网 IP 和代理端口，每 15 秒刷新；仅本机监听时明确标注。局域网客户端使用前需把监听地址设为 `0.0.0.0`。
- 独立历史管理页面 `/history.html`，可按日期 / ID / 类型检索、打开详情、下载完整 ZIP 会话包，或输入完整 ID 确认删除。本次启动会话在主界面停止后管理，历史页只管理归档数据。

## 性能处理

实时事件按类型合并；请求列表只重建发生变化的行，会话计数最多每两秒刷新，后台标签页暂停列表刷新。详情预览最多展示 64 KiB，不携带重复 Base64，完整导出与编辑重放仍保留原始数据。
SQLite 复用最多 8 个连接，常用查询建索引，历史会话摘要缓存；空正文不创建文件，同一请求正文不会在响应阶段重复传输和保存。压缩正文在预览时限制展开大小，解码不占用写入锁。
导出在服务端逐条写入临时文件，下载结束后删除；CSV 只查询摘要。抓包事件只序列化一次，队列同时限制事件数与字节数；超出容量时显示丢失事件计数。队列仍可能在持续超载时丢弃事件，不保证无限吞吐量。

## 参数处理扩展

在根目录 [hook_template.py](hook_template.py) 直接填写自己的 Hook，或在 `startup.toml` 的 `[extensions].hook_modules` 中列出第三方 Python 模块。类继承 `capture.plugins.BaseHook`、设置唯一 `name`，并按需实现同步 `on_request(flow, context)` 和 `on_response(flow, context)`；两个方法默认都不修改报文。模块导入时注册，在“设置 → 扩展”按注册名添加、启用并调整顺序。更多内置示例在 `capture/plugins/hooks.py`。旧配置里的 `plugins/request_hook.py` 等路径会迁移成同名注册项。
每次完整启动 `main.py` 时，Web 与代理进程固定该次启动配置中的 Hook 模块集合。运行期间修改 `startup.toml` 不会隐式增删模块；端口或 Hook 执行顺序等设置导致代理重启时，也沿用当前 main 进程启动时的模块集合。完整重启 `main.py` 后才会读取新的模块列表。运行时不要编辑插件源码；源码变更也需完整重启 main。开始新抓包会重建 Hook 实例，但不重新发现模块。更改启用状态与列表前要停止本次抓包；总开关可以在运行中切换。

`flow` 是完整 mitmproxy HTTPFlow，可修改 URL、Query、Headers、Cookie、表单或二进制正文。
`context` 提供 `session_id`、`config`、`logger`、`now_ms()` 和当前 `hook_name`。任一 Hook 异常时返回 502，错误包含注册名与阶段，后续 Hook 不执行。请求阶段会分别保存原始请求和最终发送请求；响应阶段保存修改后的响应。记录中的 `executed_hooks` 列出成功执行的注册名。
hook 改写到被拒绝域名时也会被阻止。TLS 透传流量不会触发 HTTP hook。

## 项目目录

```text
main.py、mcp_server.py      应用与 MCP 启动入口
config.py、startup.toml     配置定义与启动配置
hook_template.py            可编辑的请求/响应 Hook 模板
requirements.txt            依赖版本
capture/                    应用代码与资源
  agent_mcp/               MCP 服务、查询工具和审计
  backend/                 组合根、生命周期、SQLite、重放、导出和独立链路分析
    api/                   按领域拆分的 HTTP/WebSocket 路由
  engine/                  mitmproxy 代理进程、TLS 策略与请求 Hook
  plugins/                 Hook 基类、注册表与内置示例
  web/                     原生 Web UI 和本地 Three.js 依赖
  support/                 不常修改的辅助内容
    docs/                  使用说明、界面截图与性能测量
    research/cases/        独立逆向分析案例和复核脚本
    scripts/               开发与性能测量脚本
    tests/                 自动化测试
data/                      运行数据：配置、证书、日志、分析结果和抓包会话
.venv/                     本地 Python 环境
```

`data/` 和 `.venv/` 留在项目根目录：前者保存正在使用的 SQLite 会话与 CA，后者供 PyCharm 和启动命令使用。`main.py`、`mcp_server.py` 与配置文件也保留在根目录。其余代码和资源都在 `capture/`，其中不常改动的内容统一放在 `capture/support/`。

`data/settings.json` 是全局配置；`data/certificates/` 包含私钥，不要分享整个目录。每次会话保存在 `data/captures/<时间_ID>/`，其中包括 `capture.sqlite` 和 `bodies/`。

每个会话保存策略快照。数据库启用 WAL，运行中不要单独复制主数据库文件。停止后会进行 checkpoint；正文也要一起保存。

## 第一版边界

断点编辑、Map Local / Remote、差异对比、WebSocket 消息面板、导入、独立新建请求及 SQLite 整理管理后续逐步添加。
当前支持显式 HTTP 代理下的 HTTP/HTTPS，未实现 QUIC/HTTP3 抓包。
已知大响应和 SSE 流式转发，不保存完整正文。其他正文最多保存 2 MiB；截断请求不能重放或导出代码。
事件队列有界，满时丢弃并计数；首版不是零丢包归档工具。代理退出时仍在队列中的事件可能未落盘。

## 验证

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
```
