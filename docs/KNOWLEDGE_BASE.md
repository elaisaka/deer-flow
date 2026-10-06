# 第 3 阶段：个人知识库资料管理

第 6 阶段笔记复用本模块的 import_file、解析、去重、版本和删除流程，不建立另一套
知识库。真人确认具体草稿修订后，文档 source 标注 user_note 或 assistant_confirmed_note；
入库与索引分别记录。删除笔记默认保留知识库副本、原资料和不可变历史，删除库副本
仍使用本指南的独立浏览器确认。详见 [NOTES_AND_REVIEW.md](NOTES_AND_REVIEW.md)。

第 4 阶段已在**同一资料库、同一 SQLite 与卷**上增加索引、范围检索、问答工具与
受权限控制的原文引用。下文保留第 3 阶段管理设计和实际验收历史；当前 RAG 配置、
索引状态、更新/删除与聊天历史策略、24 问评测及未验证项见 [RAG.md](RAG.md)。
解析 ready 不代表索引 ready；本机已配置真实 Ollama bge-m3 与回答模型授权，
单片段、跨片段、拒答、真人更新/删除及重启的逐项结果见 RAG 指南，完整人工评分仍待完成。

## 方案与复用边界

选择轻量扩展（B），只建设一套个人资料库。实际检查的 RAGFlow 客户端
`community/ragflow/client.py` 只有 dataset/document 列表与 retrieval，现有
`knowledge_scope` 负责检索范围，均没有本阶段的写入、版本、去重、删除管理。
聊天上传 `app/gateway/routers/uploads.py` 属于 thread/sandbox 生命周期，不能
直接当作稳定文档版本库。通用 BlobStore 默认关闭，也不负责版本和删除引用计数。
使用 RAGFlow（A）需要另部署服务、配置凭据并补齐管理适配，当前没有启用该服务；
本阶段无需其索引能力，因此不额外部署。后续是否选它作为索引后端另行决策。

复用 `PluginContribution`、`BrowserAssets`、`BackendAction`、`ModelTool`、现有身份
投影与宿主 CSRF；独立模块 `backend/knowledge_base_extension/` 提供页面和服务。
Windows 导入复用第 1/2 阶段服务、操作令牌及原生句柄路径检查。
知识库更新/删除使用浏览器按钮确认，不使用文件整理的真人私有密钥。
第 3 阶段没有修改核心或启用 RAGFlow `knowledge_search`。第 4 阶段仅修复宿主
非流式模型调用的聊天投影（保留 token 统计，先校验输出），原因及回归见 RAG 指南。
第 4 阶段的 personal.knowledge-base 工具使用独立扩展命名空间，通过现有贡献合同接入。

## 数据与解析

### 管理页面布局

页面沿用 DeerFlow 工作区主题，宽屏左侧选择与管理知识库，右侧展示资料列表、
上传入口、Windows 授权目录入口及检索测试；窄屏改为上下排列。
进入页面默认选中第一个可用知识库。每个知识库下的“管理”可展开改名与删除预览。
点击资料行打开详情，包含索引重试/重建、名称、全部版本及元数据、当前/历史解析
内容、文件更新与删除预览。原有按钮确认、修订校验、去重与副本清理语义保持不变。
“导入资料”展开上传表单；“使用说明与数据保留”保留格式限制和聊天历史保留说明。
切换库时清除旧详情，并屏蔽迟到的旧资料响应。页面关闭时停止索引轮询。

开发栈 Compose 卷 `deer-flow-dev_knowledge-base-data` 挂在
`/var/lib/deerflow-knowledge`；生产项目名不同，卷名前缀随项目名变化。
不把 Windows 授权根挂到 Docker，也不把确认密钥挂进去。

- `knowledge.sqlite3`：所有权知识库、稳定文档 ID、不可变版本 ID；展示名称与来源
  分开保存。更改名称不改变文档 ID，来源记录是导入时的路径快照，不自动追踪之后的主机改名。
- `objects/<version_id>/original`：知识库自身保存的原件副本。
- `objects/<version_id>/parsed.json`：纯文本及 PDF 真实页码；MD/TXT 页码为 `null`。
- 每版本记录 SHA-256、大小、格式、来源、时间、解析器版本、状态、错误和存储定位。
- `current_version` 只指向可用版本。更新失败仍保留上一可用版本，资料列表展示最新
  尝试状态，详情同时展示全部版本及当前可用版本，避免把失败更新说成成功。
  列表 `version_id` 对应最新尝试及其状态，`current_version_id` 单独指向可用版本。
- `processing → ready/failed`；同步解析限 15 秒，进程失败、超时、缺依赖均明确记录。
  在跨进程锁内持久化 processing 后启动解析；重启取得锁后把遗留 processing 标为
  `failed/processing_interrupted`，不会永久假装处理中。

第一版单文件最多 **10 MiB**，只支持 `.pdf/.md/.txt`（大小写不敏感）；
Markdown/TXT 必须 UTF-8（支持 BOM），解析文本上限 4 MiB，PDF 最多 200 页。
PDF 使用固定依赖 `pypdf==6.19.0`，严格读取，不执行脚本、宏、链接或文档指令，
不做 OCR。扫描件/空白件无有效文本时返回 `no_effective_text`，损坏件返回
`damaged_document`，加密 PDF 返回 `encrypted_pdf`，文本编码错误为 `invalid_utf8`。
解析在独立子进程中进行，不传入服务凭据环境；Docker/Linux 工作者另外限制
512 MiB 地址空间、12 秒 CPU 与输出文件大小。Windows 主机只读取，不解析 PDF。
PDF 提取顺序和字符完整性受文件文本层影响，无法承诺复现排版；见
[pypdf 文本提取说明](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)。

页面用 DOM `textContent` 展示名称、元数据及原始解析文本，不渲染 Markdown HTML，
不自动打开链接。资料内容作为不可信数据，不触发工具、命令或模型调用。

## 去重、更新和删除

同一知识库按原件 SHA-256 去重（包括已记录的解析失败），返回 `duplicate` 和已有
文档/版本 ID；同名不同内容返回 `name_conflict`。用户可选择新的展示名称后另建资料，
或在资料详情选择文件更新。不同知识库独立保存副本、独立去重，不共享删除引用。
同一知识库若更新内容属于另一文档，返回 `content_belongs_to_other_document`。
事务、唯一索引及 OS 文件锁保证并发重试不会新建重复记录。

更新只通过页面 multipart 入口，用户选择文件并点击确认更新，提交 document_id、
当前 revision 与选定文件字节，无需 Windows 密钥，也不依赖 Windows 服务在线。
事务内再次核对 revision；旧修订请求失败，Agent 没有更新工具。
删除先生成范围预览和摘要，再由用户在页面点击“确认上述范围并删除”，无需 Windows
私有密钥，也不依赖 Windows 服务在线。此确认只授权删除知识库自己的副本及记录。
浏览器必须为管理员登录会话、显式 Origin、宿主 CSRF 验证通过；PAT、内部调用、
AUTH_DISABLED、模型传 `confirmed=true` 均不能构成确认。
文件整理的版本确认方式保持原样，知识库的浏览器确认不授予 Windows 文件整理权限。
执行事务再次核对摘要，资料/名称/版本变化使旧删除预览失效。

删除先写 tombstone，使普通列表、详情、内容不可访问，再清理知识库保存的副本。
**从不删除 Windows 原文件。** 清理失败记录 `cleanup_pending/cleanup_failed`，页面可
查询和重试；重启也会重试。没有撤销删除功能，请核对页面范围。
持久化 `invalidations(document_id,version_id,reason)` 记录可用版本被替代或删除；
第 4 阶段已用稳定版本 ID 关联片段/索引并幂等消费失效记录。第 3 阶段导入结果
`indexed=false` 表示新当前版本尚未索引；现在查询动作另返回明确 index_status，
document 结果在当前配置索引 ready 时 indexed=true。不得仅看 parse status。

## 工具和接口

页面：[个人知识库](http://localhost:2026/workspace/extensions/personal.knowledge-base/library)。
宿主扩展动作接口统一为
`POST /api/plugins/personal.knowledge-base/actions/<action>`，沿用登录及 viewer 身份检查。

| 动作 | 参数 | 含义 |
| --- | --- | --- |
| bases / create / rename | 无 / name / knowledge_base_id,name | 知识库列表、创建、改名 |
| documents / document | knowledge_base_id / document_id | 资料列表、版本和解析状态 |
| content | document_id, 可选 version_id | 可用版本文本与真实页码 |
| rename_document | document_id,name | 改展示名称，保留稳定身份 |
| local_roots / local_list | 无 / root_id,relative_path | 有限 Windows 目录选择 |
| import_local | knowledge_base_id,root_id,relative_path | 导入一个明确选定的文件 |
| delete_preview | kind,target | kind=document/knowledge_base；只预览 |
| cleanup | 无 | 查询并重试副本清理 |

第 3 阶段注册 `knowledge_bases`、`knowledge_documents`、`knowledge_document`、
`knowledge_import_local`，宿主可添加扩展命名空间前缀。第 4 阶段再添加
knowledge_index_status / knowledge_search / knowledge_answer，详见 RAG 指南；仍无更新、
删除、确认或模型索引工具。
内容转移字节不会返回给模型；导入结果只返回实际记录、ID、解析状态及错误。

真人入口：`POST /api/personal-knowledge/import`（最多 1 个 multipart 文件；有
document_id 时必须提供 expected_revision，无密钥）；
`POST /api/personal-knowledge/delete`（kind,target,digest；仅浏览器按钮确认，无密钥）。
Windows 新接口 `POST /v1/import/read` 只接受 root_id 与 relative_path，支持格式和
传输均有上限。原文件句柄拒绝同时写入和删除，读取前后检查身份/大小/时间/摘要，
拒绝穿越、绝对路径、reparse、hardlink、项目代码目录、服务配置私有目录及数据库目录。
旧版兼容端点 `POST /v1/knowledge/confirm` 仍只验证独立真人密钥及摘要，无主机删除
行为；当前知识库页面不再调用它。
这两个端点不注册为 MCP 原始文件下载/确认工具；固定桥接的原有 9 项目录/整理工具保留。

## 配置、启动与停止

沿用 [本地服务](LOCAL_FILE_SERVICE.md)、[文件整理](FILE_ORGANIZATION.md) 已配置的
授权根与私有配置。在私有 `config.yaml` 的**现有 plugins 列表**追加
`backend/knowledge_base_extension/config.example.yaml` 的条目，保留其他插件。
不要复制第二个顶层 plugins；如果原 YAML 使用顶格 `- use`，追加时保持相同缩进。

```powershell
Set-Location E:\111\deer-flow
$env:DEER_FLOW_ROOT = $PWD.Path.Replace('\','/')
$env:FILE_ORGANIZATION_PRIVATE_DIR = (Join-Path $env:LOCALAPPDATA 'DeerFlow/local-files-phase2').Replace('\','/')
docker compose -p deer-flow-dev --env-file .env `
  -f docker/docker-compose-dev.yaml -f docker/docker-compose.local-files.yaml `
  -f docker/docker-compose.file-organization.yaml -f docker/docker-compose.knowledge-base.yaml `
  up -d --no-deps --no-build gateway
docker exec deer-flow-gateway uv pip install --target /var/lib/deerflow-knowledge/python `
  -r /app/backend/knowledge_base_extension/requirements.txt
docker restart deer-flow-gateway
```

解析依赖只装入知识库卷里的专用 python 目录，不同步或改动服务共用虚拟环境。
首次装依赖前 PDF 导入返回依赖缺失；成功装完后再验收。代码/插件/浏览器资源更新
需重启 Gateway；新增 Windows 端点需按本地服务指南核对旧 PID 后停止并重新启动：

```powershell
& .local-file-service/venv/Scripts/python.exe -m services.local_file_service.server `
  --config "$env:LOCALAPPDATA/DeerFlow/local-files-phase2/service-config.json"
```

前台 Ctrl+C 停 Windows 服务；Docker 可用 `docker stop deer-flow-gateway` 停 Gateway，
重开沿用原 Compose 参数。不要执行 `down -v` 删除资料卷。
关闭功能：在私有插件配置禁用 knowledge-base 条目后重启，不删除其他扩展/卷。
备份或迁移时先停 Gateway，一起备份 SQLite、objects 和清理/失效记录，保持 ID 不变。
任何备份、用户文件、私有配置和验收截图都不得进入 Git。

## 可逐项执行的用户验收

本机专用合成目录为 `E:\111\knowledge-base-test-20261004`，5 个文件不含真实资料。
另一次验收可生成新的空目录（脚本拒绝覆盖）：

```powershell
& .local-file-service/venv/Scripts/python.exe backend/knowledge_base_extension/fixtures.py `
  --output E:/111/knowledge-base-test-new
```

1. 打开管理页，创建 `Redis`（同名已存在时选择现有库）。
2. 选择该库，上传 `redis.pdf`；从 Windows 选择器打开 `study` 根下
   `knowledge-base-test-20261004`，分别导入 `redis.md`、`redis.txt`。
3. 每篇应为 ready；PDF 文本显示实际第 1/2 页，MD 的 script 字面内容可见且不执行，
   详情显示 windows/upload 来源及 SHA-256、解析器版本和三个稳定 ID。
4. 重复选同一文件导入，应为 duplicate，document_id/version_id 不变，列表不增副本。
5. 查看 `redis.txt` 详情，选 `redis-update.txt`，核对文件后点击确认更新，无需密钥；
   document_id 保持，新 version_id 不同、revision 增加；旧版本可查。
6. 导入 `broken.pdf`，应为 failed/damaged_document。更新损坏件的自动测试验证旧可用
   版本保留；不要把失败新版本称为已索引。
7. 重启 Gateway 与 Windows 服务，再打开库，文档/版本/失败状态应保留。
8. 对测试资料点击预览删除，核对名称、ID、修订和删除范围，点击确认即可，无需密钥。
   列表和内容不再可读；在 Windows 核对原文件仍存在。删除知识库同样要先预览确认。

知识库更新和删除都不需要复制 Windows 密钥；文件整理仍按第 2 阶段指南确认。
**Agent 不替你点击确认，不把聊天文字或模型 confirmed 参数视为授权。**

可复制的聊天测试输入：

```text
请使用个人知识库扩展工具列出我的知识库和 Redis 库的资料，返回真实 document_id、version_id 和解析状态。当前没有 RAG 索引，不要调用 knowledge_search。
```

```text
请把 study 授权根下 knowledge-base-test-20261004/redis.txt 导入我指定的 Redis 知识库（先查询得到实际 knowledge_base_id）。只导入这一篇，报告是否重复和实际解析状态，不使用 Shell 或扫描其他目录。
```

## 实际测试与未验收项

开发前基线：扩展确认与指南测试 **27 passed**。新增资料管理与浏览器权限测试
纳入下述相关回归，含实际图像型扫描 PDF、页数上限及真实 ToolNode 所有权。Windows 读取 **13 passed, 1 skipped**。阶段 1/2 与新读取合计
**77 passed, 3 skipped**（symlink 创建 WinError 1314）；junction 真实拒绝测试通过。
Docker 资料/确认/插件工具/Compose/指南相关测试 **78 passed**；随后补充所有者清理隔离
并修改版本列表关联，最新新增专项 **29 passed**，两项针对性版本/ToolNode 复测通过。
Ruff 与 guidance 检查通过；完整本机 make test / strict gate 本轮未运行，不宣称全套通过。

GitHub `fafcabd3` 的 unit 四分片、默认安装收集、blocking-io、前端、lint 与
agent-guidance 均成功，见 [该提交的 CI](https://github.com/elaisaka/deer-flow/actions/runs/37194498308)。
第 2 阶段本机两项 strict 失败、AIO 环境问题及限时全套未完成记录仍保留于整理指南，
本轮没有恢复用户已要求撤销的测试修复。GitHub 的成功不替代新增阶段代码 CI（未推送）。

真实管理页已创建 `Redis`（`3a20f6c6658646b5aaf986a845559f60`），上传 PDF 和通过
Windows 选择器导入 MD/TXT 都为 ready；PDF 实际存储页码 `[1,2]` 与合成文本独立核对。
页面重复导入 TXT 复用 `1c7de1cb7c6d41cd920893db97853d0c` / 版本
`20b7c4e041e54c73b4ea40986dbaf71a`；损坏 PDF 为 failed/damaged_document。
Markdown script 字面展示，浏览器 DOM 确认脚本未执行。Gateway 实际重启后原四篇
资料、状态和版本仍可查询；Windows 实际重启后有限读取可连通，旧整理方案仍为
undone 且三项 actual_verified=true。

真实[聊天工具验收](http://localhost:2026/workspace/chats/d332e830-046e-4e85-8370-33487ea728a5)
先发现模型工具误用管理员标记而被拒：宿主 ModelTool 仅投影受信任的 run user_id。
修正为模型查询/选定导入使用该 ID 的资源所有权，浏览器/真人入口的管理员限制保留；
新增实际 LangGraph ToolNode 回归验证跨所有者拒绝。重启后再聊天，三个工具真实调用
成功，返回四篇资料状态及 TXT duplicate，indexed=false。没有修改宿主权限或 Agent 核心。

用户已亲自输入密钥并确认用合成 `redis-update.txt` 更新 `redis.txt`。实际核对文档 ID
`1c7de1cb7c6d41cd920893db97853d0c` 保持不变，revision 从 1 增至 2，新版本为
`61bc291d694c41b7b3293ccae44129f4`；新旧版本均 ready，原件副本摘要匹配，解析内容
发生预期变化，旧版本失效记录存在。再次实际重启 Gateway 后上述记录仍可查询，
Windows 两个合成源文件均仍存在。真人更新 **已验收**。
用户随后亲自确认删除上述测试文档。真实页面返回已删除、待重试清理 0，列表只剩
PDF、Markdown 与损坏 PDF 三篇。独立核对删除 tombstone、两条已完成清理记录、
两个版本原件/解析目录均已移除，以及两个版本的删除失效记录；普通列表、详情及
当前/旧版本内容接口均拒绝访问该文档。Windows `redis.txt` 与 `redis-update.txt`
仍存在，SHA-256 与删除前一致。真人文档删除 **已验收**，上述八步开发栈验收完成。
整库删除、清理失败重试及失败更新保留旧版本已通过自动测试，未进行额外真人操作。
完整本机门禁、生产栈和实际 symlink 仍保留未验证状态。不会自动提交、推送或发布。

随后按用户要求，知识库文档/整库删除取消 Windows 密钥，只保留浏览器预览按钮确认、
真实管理员会话、Origin、CSRF、所有权和事务内摘要复核。模型没有删除工具；当时文档
更新及文件整理仍按原密钥机制确认。该调整相关知识库与文件整理确认回归 **44 passed**，
包含 Windows 服务离线也可删除副本、错身份/错 CSRF 拒绝、额外 confirmed 参数拒绝、
变更后旧摘要失效及重复删除。首次回归因命令漏传专用解析依赖路径出现 5 项 PDF 失败，
补上 `KNOWLEDGE_PARSER_DEPS=/var/lib/deerflow-knowledge/python` 后全部通过；未修改解析器。
Ruff、JavaScript 语法及 agent guidance（39 个文件、0 错误/警告）通过。
Gateway 已实际重启，管理页删除预览已核对无密钥输入框、保留确认/取消按钮；
本次仅核对界面，没有替用户删除真实资料。

用户随后要求更新也取消密钥。浏览器现在用选定文件、document_id 和 expected_revision
确认更新，不调用 Windows 确认服务；登录/Origin/CSRF、所有权和事务内修订检查保留。
知识库及文件整理确认相关回归 **47 passed**，覆盖免密更新、Windows 服务离线、
成功更新保持 ID/旧版本、失败更新保留可用内容、旧修订和重复更新拒绝、跨所有者拒绝、
PAT/错误 CSRF/跨源拒绝以及模型 confirmed 参数拒绝。Ruff、JavaScript 语法和
agent guidance 检查通过。新机制只改变知识库确认方式，文件整理仍要求原真人密钥。
Gateway 再次重启后，真实管理页更新区域已核对密钥框为 0，显示“无需 Windows 密钥”
并保留文件选择和确认更新按钮；未代用户修改现有资料。

已知限制：单用户管理员使用；写入/解析由全库 OS 锁串行化，适合个人少量资料；
无 OCR、目录批量扫描、自动同步主机改名、删除撤销或多用户共享。索引/检索/RAG 已由
第 4 阶段实现，真实 embedding/问答及最新门禁以 RAG 指南为准。
相同失败字节也去重，依赖恢复后暂需使用新版本内容或删除失败记录后重新导入；
尚无总磁盘配额。仅支持同源 localhost 开发入口，生产栈/实际 symlink 尚待补验。
Gateway 及其既有 LocalSandbox 同属可信运行进程，本阶段不承诺隔离同权限恶意程序；
文件整理的真人密钥仍在 Windows 私有目录保管，知识库页面无需该密钥。
