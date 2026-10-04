# Windows 本地文件服务（第一阶段）

## 能力与执行位置

仅提供获取授权根目录、列出目录、创建一个文件夹。授权目录由操作者配置，桌面、下载、文档目录不会自动开放。

```text
DeerFlow 原有聊天界面 → Gateway 内的 Agent
  → 现有 MCP 注册/工具调用（stdio）
  → Docker 内的固定工具桥接程序
  → Bearer 认证 HTTP / host.docker.internal:8765
  → Windows 独立服务 → Windows 授权目录
```

Agent、MCP 客户端和桥接程序在 Gateway 容器中运行。文件创建在 Windows Python 进程中执行。原有沙箱工具操作的是配置的沙箱，不能据此推断 Windows 桌面能力。本阶段不改 Agent 核心、沙箱或聊天 UI，不新增 Docker 对外端口，不把授权目录挂载到容器。

接入检查：现有 `deerflow/mcp/client.py`、`tools.py`、`session_pool.py` 已支持 stdio 与 HTTP MCP；`extensions_config.json` 支持私有 env、工具名前缀和路由提示；现有 Compose 已提供 `host.docker.internal`，并将其加入代理排除名单。Python 插件也能贡献工具，但这里使用现有 MCP 即可表达全部能力，不需要新的核心扩展接口。

选择固定 stdio 桥接的原因：直接注册远程 HTTP MCP 时，远程服务停机可能导致工具发现阶段跳过服务器。桥接程序使用 SDK 的固定三工具目录，发现过程不访问 Windows；实际调用时才连接本地 HTTP 服务，因此停机也能返回明确的结构化错误。桥接 HTTP 客户端不读取代理环境变量。

模块：

| 文件 | 职责 |
| --- | --- |
| `services/local_file_service/policy.py` | Windows 路径、重解析点、目录句柄、创建及实际路径验证 |
| `server.py` | 三个有限 HTTP 端点，认证、输入限制、线程执行文件 I/O |
| `bridge.py`、`run_bridge.py` | 固定 stdio MCP 工具与连接错误结果 |
| `configure.py` | 生成私有配置和随机令牌，拒绝覆盖已有配置 |
| `register.py` | 复用 Gateway 配置锁和原子写入器，只合并本服务器 |
| `verify.py` | 在运行中的 Gateway 使用 DeerFlow 真实 MCP 加载器/调用器验证 |
| `docker/docker-compose.local-files.yaml` | 为现有 Gateway 增加只读代码挂载 |
| `backend/tests/test_local_file_service.py` | 临时目录、安全边界、HTTP、离线工具发现、连接拒绝测试 |

## Windows 配置与启动

前提：Windows、本机 Python 3.12+、Docker Desktop；原有 DeerFlow Docker 栈能够启动。以下 PowerShell 命令从仓库根目录运行，不输出 `.env` 或令牌。

```powershell
Set-Location E:\111\deer-flow
python -m venv .local-file-service\venv
& .\.local-file-service\venv\Scripts\python.exe -m pip install -r services\local_file_service\requirements.txt
```

依赖使用已验证的 MCP SDK 1.28.1，HTTP/ASGI 依赖随 SDK 安装；桥接使用现有 Gateway 环境，不修改后端依赖锁文件。本次先在已有 Windows Python 环境测试，再在上述独立虚拟环境安装依赖并重复专项测试，两轮均通过。当前后台服务和 Gateway 桥接均使用 MCP SDK 1.28.1。

选择**已存在**的授权目录，再生成配置。例如本次用户明确授权 `E:\111`：

```powershell
& .\.local-file-service\venv\Scripts\python.exe -m services.local_file_service.configure --root E:\111 --root-id study
```

生成 `.local-file-service/config.json`（Windows 服务）和 `.local-file-service/mcp-server.json`（私有 MCP 注册条目）。如果文件已存在，命令拒绝覆盖；修改已有配置或选择新的 `--output` 目录。该目录由 `.gitignore` 排除，不提交配置、令牌、进程信息、日志或截图。

配置示例见 `services/local_file_service/config.example.json`。`roots` 可以配置多个 ID → 本机绝对路径，例如 `study`、`notes`。根目录必须已存在，不能是网络共享，所有祖先和根目录均不能为 symlink、junction 或其他重解析点。根目录 ID 只允许字母、数字、下划线和连字符。不要将配置文件分享给其他账户；使用当前 Windows 账户受控目录保存它。

前台启动（推荐，方便停止）：

```powershell
& .\.local-file-service\venv\Scripts\python.exe -m services.local_file_service.server --config .local-file-service\config.json
```

保持这个 PowerShell 窗口运行。默认绑定 `127.0.0.1:8765`；本次 Docker Desktop 已实测能够连接该监听地址。需要后台运行时：

```powershell
$taskPython = (Resolve-Path .local-file-service\venv\Scripts\python.exe).Path
$taskService = Start-Process -FilePath $taskPython -ArgumentList '-m','services.local_file_service.server','--config','.local-file-service/config.json' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -RedirectStandardOutput "$PWD\.local-file-service\server.stdout.log" -RedirectStandardError "$PWD\.local-file-service\server.stderr.log" -PassThru
$taskService.Id | Set-Content .local-file-service\service.pid
```

服务配置更改后必须重启。添加授权根目录不需要修改 MCP 配置；改端口或令牌时需同步私有 MCP 条目中的 URL/令牌。若当前机器的 Docker Desktop 不能连接 loopback，可显式把 `host` 改为 `0.0.0.0` 并配置 Windows 防火墙只允许 Docker 所需连接；不要开放公网。本次没有扩大监听范围或修改防火墙。

## 接入现有 Docker 栈

维持原有开发/生产 Compose 栈；增加这个可选 overlay。它只挂载服务代码，不启动 Windows 服务。Docker 重建/重建 Gateway 时需继续带上 overlay，普通 `restart` 会保留现有挂载。

现有**开发环境**：

```powershell
$env:DEER_FLOW_ROOT = (Get-Location).Path.Replace('\','/')
docker compose -p deer-flow-dev --env-file .env -f docker/docker-compose-dev.yaml -f docker/docker-compose.local-files.yaml up -d --no-deps --no-build gateway
```

这会重建 Gateway 容器以添加挂载，现有前端、Redis、Nginx 和运行数据保持原拓扑。先完成原仓库的启动/镜像构建再用 `--no-build`；如果原环境启用了 DooD 或 CLI auth 等额外 overlay，继续保留它们。

现有**生产环境**：先按原来的 `make up`/部署步骤启动，然后沿用原来的 Compose 环境变量和 overlay，仅追加 `-f docker/docker-compose.local-files.yaml`。直接从 PowerShell执行时需设置原生产 Compose 的路径变量：

```powershell
$env:DEER_FLOW_CONFIG_PATH = (Resolve-Path config.yaml).Path
$env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = (Resolve-Path extensions_config.json).Path
# 设置为原部署实际使用的数据目录，不新建另一个数据目录：
$env:DEER_FLOW_HOME = (Resolve-Path backend/.deer-flow).Path
docker compose -p deer-flow --env-file .env -f docker/docker-compose.yaml -f docker/docker-compose.local-files.yaml up -d --no-deps --no-build gateway
```

如数据目录不在上述示例位置，使用原部署的真实路径。不要运行 `docker compose config` 输出完整渲染配置；需要语法检查时用 `config --quiet`。

注册三个工具（Windows 上另开 PowerShell，在仓库根目录运行）：

```powershell
& .\.local-file-service\venv\Scripts\python.exe -m services.local_file_service.register
```

注册命令通过 stdin 传递凭据，调用 Gateway 已有的跨进程锁与原子写入器，保留全部其他配置。不会自动替换已有 `windows-local-files` 条目。首次注册后开一个新聊天即可；MCP 配置变更由原有缓存检测加载。已有条目要更新时在私有配置中明确修改并重置 MCP 缓存/重启 Gateway。

最终配置使用容器内 `/app/backend/.venv/bin/python`，入口 `/opt/deerflow-local-files/services/local_file_service/run_bridge.py`，类型 `stdio`。这个绝对 Python 路径避免 Docker 系统 Python 缺少依赖；原有 Agent 调用仍通过 DeerFlow MCP 池执行。文件注册是操作者级配置，不能用只允许 `npx/uvx` 的普通 API stdio 注册路径替代。

## 工具与结果

| MCP 原始工具名 | 参数 |
| --- | --- |
| `local_files_get_roots` | 无 |
| `local_files_list_directory` | `root_id`，可选 `relative_path`，空字符串表示根目录 |
| `local_files_create_folder` | `root_id`，`relative_path`，父目录必须存在 |

DeerFlow 工具名前缀为 `windows-local-files_`。Windows HTTP 接口只有 `GET /v1/roots`、`POST /v1/list-directory`、`POST /v1/create-folder`；每个请求都必须携带 Bearer 令牌，浏览器 Origin 请求被拒绝。没有 Shell、文件内容读取、写文件、删除或移动端点。

成功示例（路径来自 Windows 句柄查询）：

```json
{
  "ok": true,
  "status": "created",
  "root_id": "study",
  "actual_path": "E:\\111\\test-folder",
  "verified": true,
  "execution_host": "windows"
}
```

同名目录返回 `already_exists` 并重新验证；同名普通文件返回 `file_conflict`，原文件保持不变。错误结果统一为 `ok: false`、`verified: false`、`error.code`。工具调用记录/结果复用 DeerFlow 原有 thread/run 与聊天持久化，不建立第二套任务运行状态。

| 错误码 | 说明与处理 |
| --- | --- |
| `unauthorized_root` | 未配置这个根目录 ID，包括未授权的桌面 |
| `absolute_path_denied`、`invalid_path` | 绝对路径、驱动器相对路径、穿越、ADS、设备名或不安全路径写法 |
| `reparse_point` | 根、祖先或目标包含 symlink/junction；拒绝跟随 |
| `root_identity_changed` | 授权目录被替换；操作者核查后重启服务重新绑定 |
| `not_found` | 根/父目录不存在；不会自动创建整条路径 |
| `directory_busy`、`access_denied` | Windows 句柄共享冲突或账户权限不足，关闭冲突进程或选择其他目录 |
| `file_conflict` | 同名普通文件存在，拒绝覆盖 |
| `authentication_failed`、`authentication_not_configured` | 两侧令牌不匹配或未配置 |
| `service_unavailable` | 服务未启动、无法连接或请求超时；结果未核实，恢复连接后重试相同目录 |
| `listing_limit_exceeded` | 单个目录超过 1000 项，拒绝返回不完整列表 |
| `verification_failed`、`invalid_service_response` | 不能证明实际成功，不能按成功展示 |

超时可能发生在服务器已创建之后，所以不能断言未执行，也不能自动换名字重试；恢复后重试同名目录能够安全核对 `already_exists`。

## 验证与聊天输入

不调用模型的 Docker → Windows 实际连通验证：

```powershell
& .\.local-file-service\venv\Scripts\python.exe -m services.local_file_service.verify
# 以下命令会真实创建目录，只有明确授权后使用：
& .\.local-file-service\venv\Scripts\python.exe -m services.local_file_service.verify --create test-folder
Get-Item -LiteralPath E:\111\test-folder
```

可直接复制到 [DeerFlow 聊天](http://localhost:2026/workspace/chats/new)：

```text
在授权目录里新建一个名为 test-folder 的文件夹。
```

安全重试：

```text
请重新调用工具，在授权目录里新建 test-folder，说明是新建还是同名目录已存在，并返回经过验证的实际路径。
```

授权范围检查：

```text
请调用本地文件工具，列出授权根目录。如果桌面不在授权范围内，请说明不能操作桌面，不要创建任何目录。
```

只以实际工具结果和 Windows 文件状态验收，不能以模型自然语言声明、聊天成功图标或沙箱中的同名目录代替。工具提示已说明：先获取 roots；不猜测桌面路径或云端位置；创建必须同时满足 `ok=true` 和 `verified=true`。

## 自动测试与本次验收（2026-10-04）

Windows 自动测试只使用 pytest 临时目录，不改动用户真实文件：

```powershell
python -m pip install pytest
python -m pytest --noconftest backend/tests/test_local_file_service.py -q
```

`--noconftest` 仅用于独立 Windows 服务测试，避免加载完整后端的非本机依赖；Docker 内使用正常后端 conftest。按仓库格式/静态检查要求，用原后端 Ruff 配置检查新增 Python 文件。

| 验收项 | 预期 | 实际验证 |
| --- | --- | --- |
| 授权目录创建 | `created`、`verified=true`，Windows 实际目录存在 | Windows 临时目录测试通过；真实聊天创建 `E:\111\test-folder`，随后独立 `Get-Item` 核对通过 |
| 重复创建 | `already_exists`，不会覆盖或另建副本 | Windows 临时测试及真实 DeerFlow MCP 重试通过 |
| 同名普通文件 | `file_conflict`，内容保持 | Windows 临时测试通过 |
| 未授权/绝对路径/穿越 | 拒绝，授权范围外没有创建 | Windows 参数化测试通过，含 UNC、驱动器相对路径、设备名、ADS |
| junction 越界/根被替换 | 拒绝重解析点，不访问外部目标 | 使用实际 Windows junction 测试通过 |
| symlink 越界 | 拒绝重解析点 | 检查逻辑与 junction 共用；实际 symlink 测试因本机无创建权限跳过，未宣称此项实机通过 |
| 普通根替换/并发重命名 | 根身份改变拒绝，持有目录句柄时不能重命名 | Windows 临时测试通过 |
| 身份错误 | HTTP 401，未创建 | HTTP 端到端临时目录测试通过；额外验证浏览器 Origin 拒绝与无 Shell 路由 |
| 服务断开 | 工具目录仍存在，返回 `service_unavailable`、未核实 | 真实 TCP 拒绝测试通过；停止实际服务后 Docker MCP 与真实聊天均正确报告错误 |
| 服务恢复 | 可继续调用，同名重试结果明确 | 恢复 Windows 服务后 Docker roots 验证通过；真实聊天连续两次创建均返回 `already_exists`，目录列表只有一个 `test-folder` |

本次 Windows 新增套件：**24 passed，1 skipped**（实际 symlink 权限）。聊天使用现有 DeepSeek Flash 配置，没有新建或输出模型凭据。真实聊天记录：[本次验收对话](http://localhost:2026/workspace/chats/2ec0b476-7b99-46e8-afb2-d454b3df0f65)。截图保存在本机忽略目录 `.local-file-service/evidence/`，不提交。

仓库验证使用现有 Gateway Python 环境；全量复测从 `/app/project/backend`（完整仓库挂载）执行原 `make test` 对应的同一 pytest 选择参数，避免开发镜像中 `/app/backend` 的父目录缺少根脚本。未修改无关模块来消除既有环境/测试问题。

| 检查 | 实际结果 |
| --- | --- |
| Windows 新增专项 | 独立虚拟环境：24 passed，1 skipped（symlink 权限），两项依赖弃用/类型定义警告，无测试失败 |
| Docker 新增专项 + 相关 MCP/Compose 回归 | 66 passed，20 skipped（Windows 执行用例在 Linux 跳过） |
| Ruff（原后端配置） | 新增 11 个 Python 文件 `format --check`、`check` 通过 |
| Git 检查 | `diff --check` 通过；私有 config、MCP 条目、PID、截图均命中忽略规则；已有修改保留 |
| 修改前 `make test` | 当前开发镜像目录布局导致 13 项收集错误（缺 `/app/scripts` 与 `.git` 等），未通过 |
| 修改后全量离线 pytest | 已执行部分：3978 passed，10 failed，41 skipped，7 deselected；运行 13 分 31 秒，长期停滞后主动中断，**全量未完成** |
| `make test-blocking-io` 及对应参数复测 | 修改前、修改后均 208 passed、2 failed；未通过 |

上述 66 项回归使用：

```text
tests/test_local_file_service.py
tests/test_compose_default_bind_host.py
tests/test_compose_extensions_config_writable.py
tests/test_mcp_client_config.py
tests/test_mcp_tool_name_prefix.py
tests/test_mcp_cwd.py
tests/test_mcp_routing_prompt.py
```

全量离线已出现的失败位于原有 `test_aio_sandbox_local_backend.py`、`test_aio_sandbox_provider.py`（容器继承 Windows host 路径映射，与测试临时 POSIX 路径预期冲突等）和 `test_config_version.py`（配置升级子进程 120 秒超时）。未完成全量，不能推断其余用例通过。严格阻塞 I/O 的两项失败为 `test_gateway_agent_factory_runs_off_the_event_loop` 和 `test_abefore_agent_records_checkpointed_memory_on_timeout`，分别为事件等待断言与必需记忆读取超时，修改前后均复现；停止全量测试后再次单独运行这两项仍失败。

原始测试日志保留在忽略目录 `logs/local-file-baseline-tests.log`、`logs/local-file-baseline-blocking.log`、`logs/local-file-final-tests.log`、`logs/local-file-final-blocking.log`。专项结果和已验证业务能力已记录；全仓库门禁、symlink 实机验证仍未完成。

## 停止与撤除

前台服务按 `Ctrl+C`。后台服务可检查 PID 对应进程后停止：

```powershell
$taskServicePid = [int](Get-Content .local-file-service\service.pid)
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskServicePid"
if ($taskProcess.CommandLine -match 'services\.local_file_service\.server') {
    Stop-Process -Id $taskServicePid
}
```

只停止 Windows 服务时，Docker 工具仍存在，但调用返回明确连接错误。需要关闭功能时，在 DeerFlow MCP 设置中禁用 `windows-local-files`，或者在私有 `extensions_config.json` 将该服务器的 `enabled` 改为 `false`。不用删除其他 MCP 配置。需要移除代码挂载时，以原来的 Compose 文件重建 Gateway；原有 Docker 停止方式继续可用。

本次验收后 Windows 服务已恢复为隐藏后台进程，PID 记录在 `.local-file-service/service.pid`，原有 Docker 服务继续运行。`E:\111\test-folder` 保留用于用户检查，未执行提交、推送、发布或远程地址修改。

## 限制和后续

- 单用户、Windows 本机服务；不提供多账户权限划分、TLS、公网访问、系统服务安装或开机自动启动。
- 全部重解析点保守拒绝，即使链接目标仍在授权目录内。根目录及祖先需要当前账户的目录列举权限，共享冲突时拒绝操作。
- 只创建一个末级目录；父目录必须已存在。没有批量重命名、分类移动、撤销、RAG、知识库、学习计划或桌面自动化。
- 单目录列表最多 1000 项，单请求正文最多 8192 字节；不读文件内容。
- 普通文件执行以 Windows ACL 和句柄共享约束为边界，不承诺抵御管理员/内核权限的并发篡改。
- 生产 Compose 的配置结构已检查；本次实际验收使用现有 Docker **开发栈**。生产镜像重建及生产聊天未另行实测。
- 纯 Windows symlink 实机测试仍待获得开发者模式/权限后执行。已真实验证 junction 边界。
