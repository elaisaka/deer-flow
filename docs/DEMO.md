# 第 7 阶段：合成端到端演示与运行手册

本手册使用独立合成文件和独立库。故意冲突、注入文本仅用于测试，不作为真实 Redis
知识传播。产品中的作答、入库批准、自评和资料更新/删除仍使用显式确认流程。
用户明确授权时，代理可在独立合成库通过正常登录页面/接口执行这些测试动作；
标记为代理验收，不代替真人评分，不绕过权限或 CSRF。最新结果见
[CLOSEOUT.md](CLOSEOUT.md)，此前分轮记录见 [ACCEPTANCE.md](ACCEPTANCE.md)。

## 干净环境启动

从自己的仓库检出公开代码，保留 LICENSE。准备 Docker Desktop（Compose ≥2.24）、
Windows Python 3.12、uv 0.11.1、Node.js 24 和 pnpm 10.26.2。依赖与锁文件是
公开的；API 凭据、真实文件、运行数据库不应从他人的环境复制。

1. 在仓库根复制 `config.example.yaml`、`extensions_config.example.json` 为私有
   `config.yaml`、`extensions_config.json`，按上游配置模型和本地个人管理员登录。
   配置无工具的宿主模型调用授权，遵循 [RAG](RAG.md) 和 [学习](LEARNING.md)
   的示例。不能通过 AUTH_DISABLED 绕过真人确认。
2. 按 [本地服务](LOCAL_FILE_SERVICE.md) 创建独立服务 venv，配置**已存在且明确授权**
   的测试根。按 [文件整理](FILE_ORGANIZATION.md) 初始化独立私有目录与整理账本；
   确认密钥放在授权根和项目之外，只由本人在页面输入。不重复初始化已有配置。
   注册固定 stdio MCP；不得把授权根或确认密钥挂到容器。
3. 在 `config.yaml` 的现有 plugins 列表中合并 file-organization 和 knowledge-base
   两个 `config.example.yaml` 条目，不追加第二个顶层 plugins。学习/笔记/复习由
   同一知识扩展注册。模型 host_access 授权按 LEARNING 指南配置，来源与写入确认
   分开。私有操作令牌路径只供既有有限桥接，不输出或提交文件。
4. 安装并启动 Ollama，执行 `ollama pull bge-m3`，核对模型及 1024 维响应。
   插件 embedding 指向 `http://host.docker.internal:11434/api/embed`，明确设置
   allow_send=true。只在本人同意发送范围后索引/生成；远程模型可能收费。
5. 先启动 Windows 服务，再启动 Docker 四服务。PowerShell 示例：

```powershell
Set-Location E:\111\deer-flow
& .local-file-service/venv/Scripts/python.exe -m services.local_file_service.server `
  --config "$env:LOCALAPPDATA/DeerFlow/local-files-phase2/service-config.json"
# 此进程前台运行；另开终端执行下面的 Docker 命令。
```

```powershell
Set-Location E:\111\deer-flow
$env:DEER_FLOW_ROOT = $PWD.Path.Replace('\','/')
$env:FILE_ORGANIZATION_PRIVATE_DIR = (Join-Path $env:LOCALAPPDATA 'DeerFlow/local-files-phase2').Replace('\','/')
docker compose -p deer-flow-dev --env-file .env `
  -f docker/docker-compose-dev.yaml -f docker/docker-compose.local-files.yaml `
  -f docker/docker-compose.file-organization.yaml -f docker/docker-compose.knowledge-base.yaml `
  up -d --build
docker exec deer-flow-gateway uv pip install --target /var/lib/deerflow-knowledge/python `
  -r /app/backend/knowledge_base_extension/requirements.txt
# 首次安装解析依赖后需重启 Gateway；先确认没有正在执行的任务。
docker restart deer-flow-gateway
docker exec deer-flow-gateway /app/backend/.venv/bin/python -c `
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health/ready').status)"
```

`.env` 等私有文件应按启动指南创建；不要打印 `docker compose config` 的完整内容。
`config --quiet` 可以检查语法。打开 http://localhost:2026 并登录。ready=200 只说明
服务就绪，还需核对库和索引状态。当前开发镜像 Python 环境可能缺独立 PDF 解析依赖，
安装在知识卷专用 python 目录，不修改共享 venv。若已安装，不必重复安装或重启。

停止时先结束聊天/索引任务，Windows 前台 Ctrl+C；Docker 使用同样 Compose 文件和
项目名执行 `stop`。不执行 `down -v`。下次使用相同配置、项目名和卷 `up -d`。
代码/插件、资源 manifest 或模型授权变化需重启 Gateway；授权根或 Windows 代码
变化需重启 Windows 服务。仅重新索引不要求清空数据或重启。

## 准备固定合成资料

在 backend 中使用已安装公开后端依赖的环境；输出必须是新的绝对空目录：

```powershell
Set-Location E:\111\deer-flow\backend
uv sync --locked --group dev
uv run python -m knowledge_base_extension.demo --output E:/111/phase7-demo-new
```

工具不调用服务，拒绝覆盖，生成 SHA-256 manifest、文本型两页 PDF 及固定
`synthetic-redis-v1` 资料。initial_import 列表是普通演示资料；conflict.md 和
injection.md 标为故意不可信测试，放在**单独评测库**；versioned-update.txt 只用于
更新，不作为第二篇同名证据初始导入。questions.json 是问题集，不能当知识资料导入。
本轮初始已准备 `E:\111\phase7-demo-20261006` 的 11 份资料；当前工具的新目录
还包含 learning-cases.json（12 份，不含 manifest），仅供评分评测，不导入学习库。
旧目录/manifest 保留不覆盖；重复演示请换目录、库和计划名称。

## 真人操作脚本与核对点

| 步骤 | 入口和操作 | 通过证据 |
| --- | --- | --- |
| 1 列表/预览 | 聊天限定授权 root_id 和专用目录，只对 aof.md、rdb.md、redis.pdf 分类；不要扫描整个授权根 | 实际列出 3 文件，md→markdown、pdf→pdf；预览不移动，记录 ID/版本 |
| 2 执行/撤销 | [文件整理页面](http://localhost:2026/workspace/extensions/personal.file-organization/plans) 本人核对并输入本机密钥确认；聊天明确要求撤销该 ID/版本 | succeeded 和 actual_verified；撤销 undone，原路径与 manifest 摘要一致；已有冲突不得覆盖 |
| 3 导入 | [知识库页面](http://localhost:2026/workspace/extensions/personal.knowledge-base/library) 创建“Redis 第7阶段 Demo（合成）”，导入 initial_import 7 篇，再重复其中一篇 | parse ready、PDF 实际两页；duplicate 返回相同 ID，不增加副本 |
| 4 索引 | 用户逐篇点击建立索引并等待 ready | 真实 bge-m3/1024 维；不是假向量；记录配置、版本、chunk 数 |
| 5 问答 | 新聊天明确选本轮库，对 RDB 定义、RDB/AOF 比较、这台机器实测吞吐提问 | 实际 knowledge_answer 调用、正确原文链接；无测量证据不猜数字 |
| 6 计划 | [学习页](http://localhost:2026/workspace/extensions/personal.learning/study) Java 基础、Redis、14 天、每天 30 分钟，选本轮库；本人修改一个章节后开始 | 预算、缺口明确；修订递增、实际 RAG、讲解引用当前资料 |
| 7 答题 | 本人提交至少客观题和简答题，客观题故意答错一题；不在答前显示参考答案 | 程序判分/参考评价分开，新记录持久化，阅读不自动完成/掌握 |
| 8 笔记 | 选章节生成草稿，本人编辑、保存、预览具体修订并批准入库，再建立索引 | approved_revision 与保存修订一致；imported 与 indexed 分开；状态 ready |
| 9 检索笔记 | 聊天明确该库和笔记标题提问，打开引用 | source_type=assistant_confirmed_note/user_note、independent_evidence=false；原文可读 |
| 10 错题/复习 | 本人收录客观错题和一条简答待复核题，亲自重练；安排笔记复习，本人选择自评并提交 | 原 attempt 不变，新 attempt 增加；简答不是可靠判错；日期按时区规则、同日不重复推进 |
| 11 重启 | 本人暂停计划；确认没有其他活动任务后再重启 Gateway，本人继续 | 库、讲解、答题、笔记、错题、复习记录 ID/摘要/日期一致；卷保留 |
| 12 来源变化 | 本人更新 versioned.txt 或实际讲解依据的 types.md，重新索引；再本人删除选定合成文档 | 新旧 version 区分，未索引不使用旧版；旧引用失效，删除原文拒绝；相关历史不改写、旧题评分停止，Windows 原件保留 |

本轮分类方案 ID：`3dc62cbcac6341988b4228809a9a529d`，version=1，现已本人
页面批准/执行、代理通过真实服务 HTTP 撤销并核对位置及摘要。最终 undone；
本轮未通过聊天/MCP 撤销。重做生成新 ID，不复用历史批准。

可复制聊天指令（替换为**本轮实际**库/计划/章节 ID）：

```text
只列出 study 授权根下 phase7-demo-20261006 的文件。只对 aof.md、rdb.md、redis.pdf
预览分类：.md 到 markdown，.pdf 到 pdf，展示 plan_id 和版本，等待我在页面确认。

仅使用“Redis 第7阶段 Demo（合成）”知识库的 knowledge_answer：RDB 与 AOF 的区别是什么？
分别引用实际来源；再回答这台机器启用 AOF 的实测吞吐量，资料没有则说明不足。

我会 Java，想用两周学习 Redis，每天 30 分钟。根据上述合成知识库创建学习计划，
明确预算、资料覆盖和缺口，不承诺按时掌握。先查询本人计划，再开始我明确选择的第一章。

根据我明确选定的 plan_id 和 lesson_id 生成笔记草稿；只保存草稿，等我编辑并在页面批准。
```

答案使用学习页真实输入，或 LEARNING 指南的“学习作答”原始人类消息协议；本手册不
提供代答脚本。截图只在本人明确授权后对选定合成页面准备，不公开或发布。

## 效果评测和证据

固定资料/24 问见 `docs/rag-fixtures`。`evaluate` 默认离线假 embedding，仅检查
管线；真实 embedding 需显式 allow-service-calls 和私有配置。运行命令、参数和
旧结果见 [RAG](RAG.md)。回答和引用还要逐条对照原文，不能以 Recall 代替正确性。

学习评分对照固定在 `docs/rag-fixtures/learning-cases.json`：明确正确、错误、概念
颠倒、部分正确、无实测证据及来源失效。它是开发者的合成输入/预期标准，不是
真人答案、普通客户端练习响应或已测准确率；真人核对字段保持 null。无答案样例
的问题也切换为性能问题，不能把与原题不相关的回答直接当证据不足。来源失效
仅作用于临时 store，要求不调用模型、不产生答题记录。实际结果见 ACCEPTANCE。

评分记录示例（实际有答案后填写，不能给缺失回答打通过）：

```json
{"dataset":"synthetic-redis-v1","reviewer_kind":"developer_preliminary","reviewer":"开发者初评",
 "cases":[{"case_id":"q01","answer_correct":true,"citation_supported":true,
 "no_answer_handled":null,"conflict_handled":null,"reason":"逐条对照 RDB-1 原文；需用户另行核对"}]}
```

上述仅是字段格式示例，不是实测成绩。真人核对用独立文件、reviewer_kind=human，
由本人提供评分；代理不能把自己的初评改成真人。汇总命令：

```powershell
uv run python -m knowledge_base_extension.evaluation_review `
  --results E:/Acceptance/results.json --review E:/Acceptance/developer-review.json `
  --output E:/Acceptance/developer-summary-new.json
```

汇总分别记录各指标分母、未评分和缺失/失败回答，不自动打分，不覆盖结果文件。
保留耗时、实际宿主 model_usage；不可获得的账单和用量标 null，不估算提升幅度。

## 数据、备份和独立恢复

| 位置 | 内容和备份范围 |
| --- | --- |
| Compose knowledge-base-data 卷（本机通常 deer-flow-dev_knowledge-base-data） | knowledge.sqlite3、WAL/SHM 若有、objects 原件/解析、索引/失效/学习/笔记/答题/复习；整体保存 ID |
| Windows 私有目录 `%LOCALAPPDATA%\DeerFlow\local-files-phase2` | 文件整理账本、服务配置及凭据；由本人单独加密备份，禁止上传 Git/聊天 |
| Windows 授权测试根 | 原文件独立备份；不包含在知识库副本删除范围 |
| `backend/.deer-flow` 及实际宿主配置的持久目录 | 上游数据库/checkpoints、聊天和 thread 数据；与知识库的历史保留不同 |
| repo 私有 config.yaml、extensions_config.json、.env | 由本人单独安全管理；恢复权限/模型/端点，不放入公开测试环境 |

备份必须先停写入者：通知并结束活动 run、索引、发布和清理任务，停止 Gateway，
Windows 整理账本也要停其服务。确认停止后**整体复制**对应卷/目录；不要只复制
正在写入的 SQLite 文件。知识库 SQLite backup API 单独使用也不保证 objects 与
版本一致，因此本版采用停写入者的目录快照。记录时间、代码 SHA、配置版本（不含
密钥）及文件 manifest；在副本执行 PRAGMA integrity_check，并核对版本对象摘要。
备份含个人历史内容，按敏感数据保护。恢复后仍应以数据库所有权/版本规则读取。

恢复验证必须使用新空目录/新卷和独立端口的测试栈，不覆盖生产卷；保留 ID、原件、
解析及索引配置，核对各类记录计数/摘要、引用和新检索。embedding 配置不一致时要
标配置变化并重新索引，不能伪装旧向量可用。验证后再决定正式迁移，不能宣称生产
恢复已通过。本轮自动恢复测试只用关闭事务的临时合成 store，无生产卷或个人资料。

停写入后卷快照示例（**本人核对实际卷名和没有活动任务后手动执行**，不是本轮
已对生产执行的记录）。备份目录在 Git 之外；固定文件名存在时拒绝覆盖：

```powershell
# 使用上文同一项目/Compose 文件的 stop 命令先停止 Gateway。
$backupDir = 'E:/DeerFlowBackup/phase7-cold-new'
$backupImage = docker inspect --format '{{.Image}}' deer-flow-gateway
New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop
docker run --rm --network none --entrypoint /bin/sh `
  --mount 'type=volume,source=deer-flow-dev_knowledge-base-data,target=/source,readonly' `
  --mount "type=bind,source=$backupDir,target=/backup" `
  $backupImage -c 'test ! -e /backup/knowledge.tar && tar -cf /backup/knowledge.tar -C /source .'
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backupDir 'knowledge.tar')
# 保存摘要于私人备份记录。可按上文原配置启动 Gateway，卷名保持不变。
```

以上从本人实际 Gateway 读取镜像 ID，不打印完整环境配置。恢复示例
只使用全新专用卷；不要将目标改为现有生产卷：

```powershell
# 先核对该专用卷名不存在；若已存在，换新名字，不能复用或清空。
docker volume inspect deer-flow-phase7-restore-new
# 上条应返回“不存在”，之后才创建。
docker volume create deer-flow-phase7-restore-new
docker run --rm --network none --entrypoint /bin/sh `
  --mount 'type=volume,source=deer-flow-phase7-restore-new,target=/restore' `
  --mount "type=bind,source=$backupDir,target=/backup,readonly" `
  $backupImage -c 'test -z "$(ls -A /restore)" && tar -xf /backup/knowledge.tar -C /restore'
```

恢复卷先挂到独立验证容器，只读执行 SQLite integrity_check、核对 records/objects
摘要，再在独立端口/私有测试配置的栈验证引用、检索、学习与复习。不要连接生产
用户目录、私有令牌或 Windows 原资料。上述是操作说明；本轮实测是 pytest 的
关闭事务合成目录快照及恢复，不是生产卷 tar 或独立生产部署验收。

本轮实际独立库名为“Redis 第7阶段 Demo”，ID `2245f3d626bf4f1f800986427d5a66d5`；
本人导入/重复提示复验及补 PDF 后，7 篇均解析、索引 ready（2026-10-06）。
本轮聊天 RDB 保存内容和 RDB/AOF 比较已核对核心回答及原文引用；本机 AOF
吞吐量首次并行调用失败，随后本人单问明确返回资料不足且不编造数值。两次
结果分别保留，未宣称并发缺陷修复。本人打开引用仍待确认；接下来执行表中
步骤 6 的新计划创建、本人编辑和开始章节，不能复用旧阶段历史冒充本轮验收。

本轮后续状态以 ACCEPTANCE 的追加记录及矩阵为准：计划创建/编辑、答题、
章节笔记实际修改后重新批准/新版本索引、聊天检索、两类错题重练及本人
暂停→真实重启→继续已有记录；笔记和简答复习按本人自行验收报告通过。
当前来源变化测试用独立 `E:\111\phase7-source-update-20261006\types.md`，
只含公开合成内容及测试版本标记。本人在本轮库选择原 types.md，以“更新资料
版本”入口选此文件，确认更新后建立新版本索引；不要作为第二篇资料导入。
准备文件没有自动写入知识库。本人实际更新后只读核对已通过：原 types.md
修订 2/两个版本、新索引 ready，旧引用失效，相关讲解与笔记标记过时、旧题
停止评分，历史摘要不变。本人随后删除原文，后台核对修订 3/版本清理，原文
新旧版本读取均拒绝、无检索片段，历史保留且旧题停止评分；本机原件及更新
文件摘要未变。此次来源更新和删除验收闭环完成，详见 ACCEPTANCE。
该 Demo 库当前故意缺少 types.md，重新完整演示应准备新的独立测试库，不能
把旧讲解、笔记或练习中的失效来源当作当前有效资料。
为避免两个 types.md 混淆，更新测试另提供同内容的
`E:\111\phase7-source-update-20261006\types-phase7-v2.md`。
在原 types.md 详情中向下找到“更新资料版本”，选此文件并点击
“确认选定文件并更新版本”。这是更新现有文档，不是再次点击普通导入；
重复导入提示属于独立验收项，不能代替修订 2 的来源变化验收。
