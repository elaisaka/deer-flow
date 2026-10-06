# 第 2 阶段：文件整理

本阶段沿用独立 Windows 服务和 Docker stdio MCP 桥接。整理引擎、SQLite 记录和原生文件句柄位于 `services/local_file_service/`；`backend/file_organization_extension/` 通过现有 Python 扩展、浏览器页面和路由接口提供最小确认入口。没有修改 Agent 核心、聊天组件或原有启动拓扑。

截至 2026-10-04，自动测试已验证重命名、分类、版本确认、执行恢复及撤销。真实聊天已完成分类预览 → 用户亲自在确认页批准版本 1 并执行 → 查询逐项真实路径 → 聊天撤销 → 服务重启后查询的闭环；三个合成文件的路径和 SHA-256 均独立核对通过。真实聊天重名预览拒绝冲突，实际停机后的 Docker MCP 返回未验证错误。开发栈的分类闭环已验收；symlink 实机、生产栈和全仓库门禁仍有下文列出的限制。

## 规则与范围

- 只处理配置授权根目录内、明确子目录中的普通文件。不递归，不移动目录，不读取文件内容进行分类。
- `files` 为显式文件名列表；省略时选择该目录中的普通文件。每个方案 1–100 个文件；目录列举最多 1000 项。文件最大 256 MiB，计算 SHA-256 用于状态核对，内容不返回模型。
- 重命名支持 `prefix`、`suffix`、`numbering: {start: 1, width: 2}`；编号按文件名不区分大小写排序。默认保留原名称主体，扩展名保留最后一个后缀，例如 `.tar.gz` 保留 `.gz`。`omit_stem: true` 可替换主体，遇到重复目标会拒绝。
- 分类必须指定扩展名映射，如 `{'.txt': 'Text', '.md': 'Notes', '.pdf': 'PDF'}`。映射键不区分大小写，子目录只能是一层合法名称；无扩展名可用空键。未匹配文件明确标为 `skipped/unmapped_extension`。
- 默认不覆盖任何文件。大小写更名、循环更名、被其他源文件占用的目标保守报告冲突；不支持跨磁盘移动、网络路径、硬链接或任何 reparse point（symlink/junction 包括目标仍在授权目录内的情况）。
- 路径穿越、UNC、盘符相对路径、绝对路径、设备名、ADS、非法字符、结尾点/空格都被执行端拒绝。方案不能整理 `.local-file-service` 或私有记录目录。

## 可信确认与隔离

检查现有框架后，发现已有登录/CSRF、扩展页面和可信路由，但没有可直接复用、绑定本地整理方案的用户确认凭证。本阶段使用侧栏“文件整理”页面，展示方案 ID、版本、摘要、授权目录和逐项源/目标路径；用户输入 Windows 确认密钥并点击“确认版本 N 并执行”。

当前部署使用 `LocalSandboxProvider`，Agent 的 Shell 可以访问 Gateway 容器。所以 **不能将确认密钥保存在容器环境变量、项目挂载、MCP 参数或 Gateway 凭据文件中**。初始化工具将密钥和服务运行配置保存在 Windows `%LOCALAPPDATA%\DeerFlow\local-files-phase2`，位于本次授权根 `E:\111` 及项目挂载之外。Docker 只挂载操作令牌；确认密钥由用户在浏览器中临时输入，不存入聊天、页面存储或计划日志。

确认路由要求真实管理员登录会话、明确允许的 Origin 和现有宿主 CSRF 校验；拒绝 PAT、内部运行身份、未登录以及 `AUTH_DISABLED`。服务器还校验单独的确认密钥、具体 `plan_id/version/digest` 和当前源文件状态。主体 actor 取宿主认证结果，不能由客户端指定。MCP 无确认工具；执行工具传入 `confirmed=true` 会作为非法参数拒绝。

按钮在一次请求中确认并执行；如果确认成功而执行网络响应丢失，**先查询记录**，不要重新预览或通过 Shell 补做。聊天文字“确认”不授予权限。修改方案会增加版本、改变摘要并清除原确认。只有持有确认密钥的用户可确认，勿把该密钥交给聊天助手。

这是单用户本地功能，管理员共用同一授权范围。当前 HTTP 仅用于 localhost/Docker Desktop，不部署到公网。本边界不抵御恶意管理员、已被攻破的 Gateway/Windows 用户账户，或同账户本机进程窃取凭据；如启用其他沙箱，确认密钥仍须留在 Windows 授权范围外。

## 配置与启动

从仓库根目录执行。保留第 1 阶段原配置与 MCP 注册，先按 [LOCAL_FILE_SERVICE.md](LOCAL_FILE_SERVICE.md) 建好 Windows 虚拟环境和授权目录。不要输出 `.env`、运行配置或令牌。

```powershell
cd E:\111\deer-flow
$taskPython = "$PWD\.local-file-service\venv\Scripts\python.exe"
& $taskPython -m services.local_file_service.configure_organization
```

默认读取 `.local-file-service/config.json`，生成新运行配置、操作客户端文件、MCP 示例和 `approval-code.txt`；原文件不覆盖，重复初始化明确拒绝。可用 `--service-config` 和 `--output` 指定路径，输出必须在项目和所有授权目录之外。授权目录沿用原配置，不硬编码桌面。运行配置无秘密示例见 `services/local_file_service/organization-config.example.json`。

配置授权目录时，在私有运行配置的 `roots` 对象中填写“工具目录 ID → 已存在的 Windows 完整路径”，例如 `{"study": "D:\\AuthorizedStudy"}`。初始化前编辑第 1 阶段配置；初始化后编辑 `%LOCALAPPDATA%\DeerFlow\local-files-phase2\service-config.json`，然后重启 Windows 服务。保持令牌、确认凭据和 `state_dir` 不变，不把私有目录加入授权范围。删除或替换授权根后，旧计划会因根目录身份/路径不匹配而拒绝执行，需要重新预览；桌面只有显式加入 `roots` 才可操作。

给私有 `config.yaml` 的 **现有** `plugins` 列表追加以下条目；已有其他插件必须保留，不能重复创建顶层键。也可参考 `backend/file_organization_extension/config.example.yaml`：

```yaml
- use: file_organization_extension:install
  enabled: true
  required: true
  config:
    enabled: true
    credentials_path: /run/deerflow-local-files/approval-client.json
    origins: [http://localhost:2026, http://127.0.0.1:2026]
```

MCP 仍使用 `windows-local-files`，操作令牌和桥接参数保持原值。将该服务器 `tool_call_timeout` 设为 `180` 秒；可在原 `routing.keywords` 中追加“文件整理、分类、重命名、预览、撤销”，保留其他字段和服务器。不要把确认密钥加入 MCP `env`。新生成的私有 `mcp-server.json` 供核对配置，已有注册不应运行第 1 阶段注册脚本来覆盖。

先停止旧 Windows 服务，再启动新运行配置（前台可用 Ctrl+C 停止）：

```powershell
$taskPrivate = Join-Path $env:LOCALAPPDATA 'DeerFlow\local-files-phase2'
& $taskPython -m services.local_file_service.server --config "$taskPrivate\service-config.json"
```

保留原 Docker Compose 文件，追加两份 overlay；以下为本机现有开发栈（项目名保持原值）。生产栈应使用原生产 Compose 和项目名替换开发参数，不创建第二个栈：

```powershell
$env:DEER_FLOW_ROOT = $PWD.Path.Replace('\', '/')
$env:FILE_ORGANIZATION_PRIVATE_DIR = $taskPrivate.Replace('\', '/')
docker compose -p deer-flow-dev --env-file .env `
  -f docker/docker-compose-dev.yaml `
  -f docker/docker-compose.local-files.yaml `
  -f docker/docker-compose.file-organization.yaml `
  up -d --no-deps --no-build gateway
```

新增 overlay 仅挂载扩展源码和只含操作令牌的 `approval-client.json`。不挂载 Windows 授权目录、运行配置、SQLite 或确认密钥。Windows 服务继续独立运行，地址 `host.docker.internal:8765`。代码/浏览器资产/插件配置更新后需重启 Gateway；授权范围或服务代码更新后需重启 Windows 服务。仅改 MCP 配置后新建聊天以刷新工具。

验证原通路：

```powershell
& $taskPython -m services.local_file_service.verify
```

打开 [文件整理确认页](http://localhost:2026/workspace/extensions/personal.file-organization/plans)，选择方案。**用户自己**可运行以下命令把确认密钥复制到剪贴板，然后粘贴密码框，核对路径并点击按钮；不要把密钥发到聊天，也不要将私有文件提交 Git：

```powershell
Get-Content -Raw "$env:LOCALAPPDATA\DeerFlow\local-files-phase2\approval-code.txt" | Set-Clipboard
```

停止后台 Windows 服务前，按本地服务指南核对 PID 对应命令；停止不会删除记录。停机期间 MCP 仍能发现工具，调用返回 `service_unavailable` 和 `verified=false`。关闭功能时禁用私有插件配置并重启 Gateway，同时禁用 MCP 服务器；不删除其他扩展或运行数据。

## 工具与接口

第 1 阶段三个 MCP 工具保持兼容，另加六个固定工具（宿主默认添加 `windows-local-files_` 前缀）：

| MCP 工具 | 参数与作用 |
| --- | --- |
| `local_files_preview_organization` | `root_id, rule, directory='', files?`；编辑时加 `plan_id, expected_version`，不修改用户文件 |
| `local_files_get_plan` | `plan_id`；查询记录、核对实际文件、处理中断证据 |
| `local_files_list_plans` | 无参数；最近 100 个方案摘要 |
| `local_files_execute_plan` | `plan_id, version`；必须已获该版本可信确认 |
| `local_files_cancel_plan` | `plan_id, version`；取消未执行方案并清除确认 |
| `local_files_undo_plan` | `plan_id`；按执行证据有条件恢复 |

整理的 Windows 固定 HTTP 接口为 `POST /v1/organization/{preview,get,list,execute,cancel,undo,confirm}`。全部要求原 Bearer 操作令牌；只有 `confirm` 额外要求 `X-File-Approval-Token`。不提供任意 Shell、任意下载或原文件删除接口。请求正文最大 8192 字节。

第 3 阶段知识库更新/删除目前使用管理员浏览器 session/Origin/CSRF 按钮确认，
无需 Windows 确认密钥；文件整理仍由本人输入独立密钥批准版本和摘要。
`/v1/import/read` 只传输选定格式；旧 `/v1/knowledge/confirm` 是兼容端点，当前库页面不调用。
知识库删除仅清理 Docker 资料副本，Windows 原文件不删除；不影响整理计划版本与撤销。
新接口需重启 Windows 服务，原私有配置与桥接 9 工具不变，见 [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)。

Gateway 扩展只暴露 `list/get` 两个 BackendAction，无 ModelTool。浏览器 `POST /api/file-organization/confirm` 必须带会话 cookie、Origin、`X-CSRF-Token`，正文仅允许 `plan_id/version/digest/approval_code`；最后一项只转为 Windows 确认请求头，不返回或持久化。

## 状态、记录与恢复

SQLite 位于私有 `organization/plans.sqlite3`，每步事务使用 `synchronous=FULL`；逐方案文件锁跨线程/进程生效。方案保存授权根路径与身份、目录范围、规则、源/目标、源身份和内容摘要、版本与创建时间。执行前再次锁定源文件并检查身份、大小、修改/创建时间、SHA-256；整批预检查通过后才开始操作。

| 状态 | 含义与下一步 |
| --- | --- |
| `awaiting_confirmation` | 预览完成 (`preview_completed=true`)，等待用户；冲突项使整方案不能确认 |
| `confirmed` | 特定版本和摘要已批准，可执行；变更文件会阻止执行 |
| `cancelled` | 不能执行；重新编辑产生新版本并重新确认 |
| `executing` | 已写意图，正在逐项操作；并发请求返回 `plan_busy` |
| `completed` | 执行记录全部成功；实际状态需同时看 `verified` 和逐项 `actual_verified` |
| `partial` / `failed` | 部分成功 / 没有成功；保留 succeeded/failed/skipped/uncertain 项，绝不宣称原子性 |
| `undoing` | 正按记录逆序恢复成功项 |
| `undone` / `partial_undo` | 有证据的成功项全部恢复 / 部分被修改或冲突等阻止 |

每次原生移动前持久化 `applying` 意图，之后保存实际路径、身份及 `result_state`。移动使用不替换目标的句柄相对 `NtSetInformationFile`；目标目录变成 junction 后不会跟随出去。第 1 阶段全路径操作保留原严格共享锁；第 2 阶段只在原生句柄移动期间允许目录写共享，始终禁止目录 DELETE 共享。

服务/进程中断后，查询先核对原路径不存在、目标文件身份与摘要一致，才能把意图恢复为成功；证据不够标为 `uncertain`。未开始项目标为中断跳过，不自动重做。终态重复执行只查询核对，不重复移动；历史 `completed` 不代表后来被修改的文件仍验证通过。重新规划仅剩未处理文件，必须重新确认。

撤销只处理有证据的 `succeeded` 项，重新检查范围、身份/内容及原位置空闲，文件被修改、替换、挪走或原位置被占用时返回 `partial_undo`，不覆盖。分类目录只有记录为本次新建、身份未变且为空时才删除；已有目录、含外部文件的目录及中断期间所有权无法确认的目录都保留，清理原因写入记录。

## 可复制的聊天验收

本机在 `E:\111\file-organization-test-e53aaf24` 中准备了三个合成文件，另有 `conflict-case` 子目录用于同名冲突测试。三个主文件已撤销恢复原位。以下输入可直接粘贴到聊天；不要要求整理整个 `E:\111` 工作区。

```text
请调用 Windows 本地文件工具，只处理 study 授权根目录下 file-organization-test-e53aaf24 中 sample.txt、note.md、paper.pdf。按扩展名分类：.txt 到 Text，.md 到 Notes，.pdf 到 PDF。先生成预览，展示逐项路径、冲突、plan_id、版本和确认页面，等待我在页面确认；不要用 Shell。
```

重新验收时，在确认页选择这次预览返回的 **新 plan_id 和版本**，输入密钥并亲自点击。下面两条指令中的 ID 为已经执行并撤销的历史验收方案；可直接查询记录，重复撤销只返回 `undone`。要验证新方案的执行和撤销，应把 ID 换成新预览的实际值：

```text
请查询整理方案 9a77d16d43e5408a84d5058e0bbc070f 的真实记录，逐项说明实际路径、验证状态和能否撤销。不要仅凭我的文字判断执行成功。
```

```text
请调用本地文件工具撤销方案 9a77d16d43e5408a84d5058e0bbc070f，并查询逐项撤销结果；有冲突请说明，不能覆盖文件。
```

重命名预览示例（分类撤销成功后再生成，不复用旧确认）：

```text
只对 study/file-organization-test-e53aaf24 中 sample.txt、note.md、paper.pdf 生成重命名预览：加前缀 study-、从 1 开始两位编号、名称主体后加 -note，保留扩展名。先展示完整方案，不执行。
```

重名验收可直接使用已准备的合成材料：

```text
只对 study/file-organization-test-e53aaf24/conflict-case 中 sample.txt 生成分类预览，.txt 映射到 Text。展示冲突，不执行、不覆盖目标文件，也不要用 Shell。
```

预期为 `target_conflict`，确认入口不提供执行按钮，源文件和已有 `Text/sample.txt` 内容不变。历史冲突方案为 `a5894e3023b24efb9ef7aa124d6734de`，可查询。测试材料保留供复核，不清理用户资料。

## 自动测试与实际验收记录

Windows 测试只使用临时目录，使用独立服务环境、`--noconftest` 避免加载完整后端非本机依赖。Docker 用原后端环境和正常 conftest，Linux 的跳过不代表 Windows 安全验证。

```powershell
& $taskPython -m pytest --noconftest backend/tests/test_local_file_service.py backend/tests/test_file_organization.py -q
docker exec -w /app/project/backend deer-flow-gateway /app/backend/.venv/bin/python `
  -m pytest tests/test_file_organization_extension.py -q
```

| 项目 | 预期 | 实际结果 |
| --- | --- | --- |
| 预览不改文件 | 只有私有 SQLite 新记录 | Windows 临时测试、真实模型聊天及三个文件 SHA-256/路径独立核对通过 |
| 前缀/后缀/编号、保留扩展名 | 确认后按预览更名、可恢复 | Windows 测试通过 |
| 扩展名分类 | 只建必要子目录并移动选中普通文件 | Windows 测试、真人按钮执行及 Windows 实际路径/摘要核对通过 |
| 重名/大小写/非法名称/源占目标 | 拒绝，不覆盖 | Windows 测试通过；真实聊天重名预览返回 `target_conflict`，两个文件内容独立核对未变 |
| 确认前后文件变化、缺失、恢复时间戳后改内容 | 拒绝旧方案 | Windows 测试通过 |
| 未确认/取消/修改版本 | 拒绝执行或旧确认 | Windows 测试通过 |
| 越界、junction、执行中插入 junction | 拒绝，外部目录不变 | Windows 测试通过；实际 symlink 创建因权限跳过 |
| 部分失败、移动后异常、进程中断恢复 | 有证据项记录成功，余项明确失败/跳过 | Windows 测试通过 |
| 重复执行、跨实例持久化、并发同方案 | 核对记录、不重复、忙时拒绝 | Windows 测试通过 |
| 正常/重复撤销、原位冲突、文件改动/替换/挪走 | 有条件恢复、不覆盖 | Windows 测试通过；真实聊天撤销三个文件成功，原路径及摘要一致，本次空分类目录已移除 |
| 目录清理 | 仅本次拥有且为空的目录 | Windows 测试通过，预先存在和含外部文件的目录保留 |
| 身份与确认隔离 | 操作令牌不构成确认 | Windows HTTP 与 Docker 扩展测试通过，含 PAT/internal/auth-disabled/Origin 拒绝，以及真实宿主 CSRF 中间件的缺失/错误/正确令牌检查；真人独立输入密钥并点击完成 |
| 服务断开 | 未验证错误，不能重做猜测结果 | Windows 实际停机后，通过 DeerFlow 的 Docker MCP 调用 `local_files_get_plan` 返回 `service_unavailable/verified=false`；随后恢复服务 |
| Windows 服务重启后查询真实记录 | 同 plan/version/items 可查 | 实际重启后 Windows HTTP 与真实聊天查询通过，撤销记录和冲突预览均保留 |

最终 Windows 两模块合计 **64 passed, 2 skipped**：本地服务 **25 passed, 1 skipped**，整理专项 **39 passed, 1 skipped**；两项跳过均为实际 symlink 创建权限不足。Docker 扩展专项 **14 passed**；最终相关回归 **97 passed, 62 skipped**，包含这 14 项及 MCP、Compose、确认权限、插件贡献和开发指南检查。Linux 跳过包含 40 项 Windows 整理测试及其他 Windows/环境限定测试，不能替代 Windows 实测。所改 Python 模块 Ruff 检查和格式检查、浏览器模块 Node 语法检查均通过。

真实聊天记录：[分类、撤销、冲突与重启查询](http://localhost:2026/workspace/chats/aa88ae8b-dd29-41a3-8807-5524cf7c23b4)。主方案 `9a77d16d43e5408a84d5058e0bbc070f` 的最终状态为 `undone`，三个项目 `actual_verified=true`；冲突方案 `a5894e3023b24efb9ef7aa124d6734de` 为 `awaiting_confirmation`，冲突不能批准。操作记录、测试摘要和截图保存在 Git 忽略的私有运行目录，未提交凭据或用户数据。

阶段开始的门禁基线：相关 MCP/Compose/plugin **69 passed**；`make test-blocking-io` 为 **208 passed, 2 failed**。`make test` 限时 150 秒未完成，已观察 AGENTS 形状和 AIO sandbox 两模块共 9 项失败。AGENTS 形状随后在单独复跑、本阶段相关回归和最终全套运行均通过，未将该项笼统归为旧问题。

最终 `make test` 限时 300 秒，进度约 8% 后终止，未取得完整汇总；已观察同样 8 项 AIO 失败。对两个 AIO 模块专门复跑为 **190 passed, 3 skipped, 8 failed**。检查发现六项路径断言继承了 Windows 宿主映射，两项代理断言继承了 Docker 沙箱地址（一次地址解析增加了被测试捕获的子进程调用）。仅在隔离测试进程移除三个环境变量后，两模块 **198 passed, 3 skipped**，未改运行配置或 AIO 代码：

```powershell
docker exec -w /app/project/backend deer-flow-gateway env `
  -u DEER_FLOW_HOST_BASE_DIR -u DEER_FLOW_SANDBOX_HOST -u DEER_FLOW_SANDBOX_BIND_HOST `
  /app/backend/.venv/bin/python -m pytest `
  tests/test_aio_sandbox_local_backend.py tests/test_aio_sandbox_provider.py -q
```

严格阻塞检查修改后仍为 **208 passed, 2 failed**：`test_gateway_agent_factory_runs_off_the_event_loop` 的事件等待断言、`test_abefore_agent_records_checkpointed_memory_on_timeout` 的 `MemoryReadError`。二者与阶段开始基线一致，但仍是未解决的门禁失败。**全仓库门禁未通过，不能将隔离的 AIO 复跑当作全套通过。** 原始日志位于忽略目录 `logs/organization-baseline-{full,blocking,related}.log`、`organization-final-{full,blocking}.log`、`organization-aio-{final,isolated}.log`、`organization-related-final.log`。没有修改无关核心模块来隐藏失败。

## 故障处理和已知限制

- `service_unavailable`：检查 Windows PID、配置路径和 Docker host 连接；重启后先 get_plan。若响应超时，操作可能仍在后台完成，不能据此断言失败。
- `trusted_confirmation_required`：密钥错误/缺失；确认页要求真人输入，不能向模型索要它。修改规则后应刷新页面核对新版本。
- `source_changed` / `stale_plan_version` / `plan_root_changed`：生成新预览，重新确认；不要强行覆盖或编辑 SQLite 来绕过校验。
- `plan_busy`：同方案另一个请求尚未完成；稍后刷新。执行失败不会自动重试未处理项；撤销受阻原因解决后可再次请求。
- `uncertain`：缺少可证明成功的现场，不盲目恢复。保留记录和文件，用户先核对。
- 服务必须使用受保护的 Windows 私有目录；若以后把授权根扩大到该目录或将它挂到 Docker，应先移走/轮换密钥和运行数据。不承诺抵抗同账户恶意进程、管理员/内核篡改。
- 浏览器页面含来源和目标文件名，均以 textContent 显示，不执行文件名中的 HTML/指令。只认证管理员，尚无多用户文件权限隔离。
- SQLite 有完整逐步写入但没有整体事务文件操作；不支持断点自动继续、循环更名或跨磁盘。中断新建目录所有权不明时保留。
- 生产栈独立验收、全仓库门禁、symlink 实机仍未完成。重命名已通过 Windows 临时目录自动测试，未另做真人确认后的聊天重命名验收；分类聊天闭环已通过。第 1 阶段实际创建文件夹能力保留；RAG、知识库、学习计划不在本阶段。

原生接口参考：[NtSetInformationFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntsetinformationfile)、[FILE_RENAME_INFORMATION](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)。
