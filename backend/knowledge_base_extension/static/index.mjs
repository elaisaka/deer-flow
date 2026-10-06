// Redesign-preserve: DESIGN_VARIANCE 4 / MOTION_INTENSITY 2 / VISUAL_DENSITY 5.
// Keep the native extension surface and host theme; prioritize legible product UI.
function mount(surface, context) {
  const el = (tag, text = "", attrs = {}) => {
    const node = document.createElement(tag);
    node.textContent = String(text);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    return node;
  };
  const stylesheet = el("link", "", {
    rel: "stylesheet",
    href: new URL("./styles.css", import.meta.url).href,
    crossorigin: "use-credentials",
  });
  const root = el("div", "", { class: "knowledge" });
  surface.append(stylesheet, root);
  let active = true,
    base = null,
    documentId = null,
    sequence = 0,
    listSequence = 0,
    baseSequence = 0,
    polling = null,
    lastImport = null;
  let catalog = [];
  const currentBase = () =>
    catalog.find((item) => item.knowledge_base_id === base);
  const valid = (chosen, turn) =>
    active && base === chosen && sequence === turn;
  const alert = el("div", "", {
    role: "alert",
    class: "notice error",
    hidden: "",
  });
  const feedback = el("div", "", {
    role: "status",
    class: "notice feedback",
    hidden: "",
  });
  const announce = (text) => {
    if (active) {
      feedback.textContent = text;
      feedback.hidden = !text;
    }
  };
  const check = (result) => {
    if (!result?.ok) throw Error(result?.error?.code || "操作未验证");
    return result;
  };
  const call = async (action, payload = {}) =>
    check(await context.callBackend(action, payload));
  const run = async (fn, node) => {
    if (!active) return;
    if (node) {
      node.disabled = true;
      node.setAttribute("aria-busy", "true");
    }
    alert.hidden = true;
    try {
      await fn();
    } catch (error) {
      if (active) {
        alert.textContent = error.message || "操作失败，请重试";
        alert.hidden = false;
        if (bases.querySelector(".loading")) {
          bases.replaceChildren(
            el("h3", "知识库加载失败"),
            button("重新加载知识库", refresh),
          );
          docs.replaceChildren(
            empty(
              "暂时无法打开知识库",
              "请重新加载，资料仍保存在原有知识库中。",
            ),
          );
        }
        if (details.querySelector(".loading"))
          details.replaceChildren(
            el("p", "详情加载失败，请重试。", { class: "muted" }),
            button("重试详情", () =>
              documentId ? showDocument(documentId) : localPicker(),
            ),
          );
        if (docs.querySelector(".loading"))
          docs.replaceChildren(
            el("p", "资料列表加载失败，请重试。", { class: "muted" }),
            button("刷新资料", loadDocuments),
          );
      }
    } finally {
      if (active && node?.isConnected) {
        node.disabled = false;
        node.removeAttribute("aria-busy");
      }
    }
  };
  const button = (text, fn, className = "button") => {
    const node = el("button", text, { type: "button", class: className });
    node.addEventListener("click", () => void run(fn, node));
    return node;
  };
  const input = (label, attrs = {}) =>
    el("input", "", { "aria-label": label, placeholder: label, ...attrs });
  const form = (children, fn) => {
    const node = el("form", "", { class: "form-row" });
    node.append(...children);
    node.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(fn);
    });
    return node;
  };
  const disclosure = (label, open = false) => {
    const node = el("details", "", { class: "disclosure" });
    node.open = open;
    node.append(el("summary", label));
    return node;
  };
  const metadata = (label, value, parent) =>
    parent.append(
      el("dt", label),
      el("dd", value ?? "无", { class: "metadata-value" }),
    );
  const json = (value) =>
    el("pre", JSON.stringify(value, null, 2), { class: "raw-data" });
  const statusLabel = {
    ready: "就绪",
    processing: "处理中",
    failed: "失败",
    unindexed: "未索引",
    stale: "需重建",
    invalidated: "已失效",
  };
  const badge = (kind, status) =>
    el("span", `${kind} ${statusLabel[status] || status || "未知"}`, {
      class: `badge ${status === "ready" ? "ready" : status === "failed" ? "failed" : ""}`,
      title: `${kind}: ${status || "unknown"}`,
    });
  const empty = (title, description) => {
    const node = el("div", "", { class: "empty" });
    node.append(
      el("div", "资料", { class: "empty-mark", "aria-hidden": "true" }),
      el("h3", title),
      el("p", description),
    );
    return node;
  };
  const loading = (parent, label) => {
    const node = el("div", "", {
      class: "loading",
      role: "status",
      "aria-label": label,
    });
    node.append(el("span", label, { class: "sr-only" }));
    for (let i = 0; i < 3; i++)
      node.append(el("div", "", { class: "skeleton" }));
    parent.replaceChildren(node);
  };
  const reportImport = (result) => {
    lastImport = {
      base,
      documentId: result.document_id,
      versionId: result.version_id,
      message:
        result.status === "duplicate"
          ? "最近一次导入结果：重复导入，复用已有版本，未新增文档。"
          : `最近一次导入结果：${statusLabel[result.status] || result.status}${result.error ? ` · 错误：${result.error}` : ""}`,
    };
    announce(
      `${result.status === "duplicate" ? "重复导入，复用已有版本" : `导入结果：${result.status}`} · document_id: ${result.document_id} · version_id: ${result.version_id}${result.error ? ` · 错误：${result.error}` : ""}`,
    );
  };
  const header = el("header", "", { class: "intro" });
  const introText = el("div");
  introText.append(
    el("p", "资料有序，知识可用。", { class: "intro-title" }),
    el("p", "保存原件、来源与版本，在自己的资料中找到答案。", {
      class: "muted",
    }),
  );
  const formats = el("div", "", {
    class: "formats",
    "aria-label": "支持的文件格式和大小",
  });
  formats.append(
    el("span", "PDF"),
    el("span", "MD"),
    el("span", "TXT"),
    el("span", "≤ 10 MiB", { class: "muted" }),
  );
  header.append(introText, formats);
  const layout = el("div", "", { class: "library-layout" });
  const bases = el("aside", "", {
    class: "base-panel",
    "aria-label": "知识库管理",
  });
  const workspace = el("div", "", { class: "workspace" });
  const docs = el("section", "", {
    class: "documents",
    "aria-label": "资料工作区",
  });
  const details = el("section", "", {
    class: "detail-panel",
    "aria-label": "资料详情与操作",
    hidden: "",
  });
  workspace.append(docs, details);
  layout.append(bases, workspace);
  const help = disclosure("使用说明与数据保留");
  help.classList.add("page-help");
  help.append(
    el(
      "p",
      "支持 PDF、UTF-8 Markdown/TXT，单文件最多 10 MiB。保存原件、来源、解析版本和向量索引。解析 ready 不等于可检索；更新后必须索引新版本。",
    ),
    el(
      "p",
      "聊天选择不会自动同步，请在聊天中明确指定知识库 ID 或名称。删除只影响知识库副本，不删除 Windows 原文件，也不会清除既有聊天中的片段与备份。",
    ),
  );
  root.append(header, alert, feedback, layout, help);

  function stopPolling() {
    if (polling) clearInterval(polling);
    polling = null;
  }
  function resetDetails() {
    documentId = null;
    sequence++;
    details.hidden = true;
    details.replaceChildren();
    docs
      .querySelectorAll("[data-document-id]")
      .forEach((node) => node.setAttribute("aria-pressed", "false"));
  }
  function revealDetails() {
    details.hidden = false;
  }
  function detailHeader(title) {
    const node = el("div", "", { class: "section-heading" });
    const heading = el("h3", title, { tabindex: "-1" });
    node.append(heading, button("关闭详情", resetDetails, "button quiet"));
    details.append(node);
    heading.focus();
  }
  function selectBase(id) {
    base = id;
    stopPolling();
    resetDetails();
    renderBases();
    return loadDocuments();
  }
  const indexDescription = (states, state) =>
    `索引：${statusLabel[state?.index_status] || state?.index_status || "未索引"} · ${state?.completed || 0}/${state?.total || 0}${state?.error ? ` · ${state.error}` : ""} · embedding 配置：${states.embedding_configured ? "已配置，真实效果需验收" : "缺失"} · 问答模型授权：${states.answer_model_granted ? "已授予" : "缺失"}`;
  async function indexDocument(id, rebuild) {
    const chosen = base;
    const result = await call("index", { document_id: id, rebuild });
    if (!active || base !== chosen) return;
    announce(
      `索引 ${result.index_status} · version_id: ${result.version_id}。仅选中资料的片段发送至管理员配置的 embedding 服务；远程服务可能收费。`,
    );
    stopPolling();
    let busy = false;
    polling = setInterval(
      () =>
        void run(async () => {
          if (!active || base !== chosen) {
            stopPolling();
            return;
          }
          if (busy) return;
          busy = true;
          try {
            const states = await call("index_status", {
              knowledge_base_id: chosen,
            });
            if (!active || base !== chosen) return;
            const item = states.documents.find((d) => d.document_id === id);
            announce(
              item
                ? `索引 ${statusLabel[item.index_status] || item.index_status} · ${item.completed}/${item.total} ${item.error || ""}`
                : "来源不可用",
            );
            const info = details.querySelector("[data-index-status]");
            if (documentId === id && info)
              info.textContent = item
                ? indexDescription(states, item)
                : "来源不可用";
            if (!item || item.index_status !== "processing") {
              stopPolling();
              await loadDocuments();
            }
          } catch (error) {
            stopPolling();
            throw error;
          } finally {
            busy = false;
          }
        }),
      1500,
    );
    await loadDocuments();
  }
  async function post(path, body, multipart = false) {
    const token = document.cookie
      .split("; ")
      .find((v) => v.startsWith("csrf_token="))
      ?.slice(11);
    if (!token) throw Error("缺少 CSRF 凭据，请重新登录");
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      signal: context.signal,
      headers: {
        "X-CSRF-Token": decodeURIComponent(token),
        ...(!multipart ? { "Content-Type": "application/json" } : {}),
      },
      body: multipart ? body : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok)
      throw Error(
        typeof result.detail === "string" ? result.detail : "请求被拒绝",
      );
    return check(result);
  }
  function uploadControls(parent, selected = null) {
    const chosen = base;
    const panel = disclosure(
      selected ? "更新资料版本" : "上传文件",
      Boolean(selected),
    );
    panel.classList.add("upload-panel");
    const body = el("div", "", { class: "disclosure-body" });
    const file = input("选择 PDF / Markdown / TXT", {
      type: "file",
      accept: ".pdf,.md,.txt",
    });
    const name = input("可选资料展示名称（同名时改名后新建）");
    body.append(
      el(
        "p",
        selected
          ? `更新 ${selected.name} · 文档 ${selected.document_id} · 当前修订 ${selected.revision}。保留旧版本；请核对选定文件后确认，无需 Windows 密钥。`
          : "上传到当前知识库。相同内容去重，同名不同内容拒绝覆盖。",
        { class: "muted" },
      ),
      file,
    );
    if (!selected) body.append(name);
    const selection = el(
      "p",
      "PDF / UTF-8 Markdown / TXT · 单文件最多 10 MiB",
      { class: "file-selection muted" },
    );
    file.addEventListener("change", () => {
      const f = file.files[0];
      selection.textContent = f
        ? `${f.name} · ${(f.size / 1024).toFixed(1)} KiB`
        : "PDF / UTF-8 Markdown / TXT · 单文件最多 10 MiB";
    });
    body.append(
      selection,
      button(
        selected ? "确认选定文件并更新版本" : "导入上传文件",
        async () => {
          if (base !== chosen) throw Error("知识库已切换，请重新选择文件");
          const f = file.files[0];
          if (!f) throw Error("请选择文件");
          if (f.size > 10 * 1024 * 1024) throw Error("文件超过 10 MiB");
          const data = new FormData();
          data.set("file", f);
          data.set("knowledge_base_id", chosen);
          if (!selected && name.value.trim())
            data.set("display_name", name.value.trim());
          if (selected) {
            data.set("document_id", selected.document_id);
            data.set("expected_revision", selected.revision);
          }
          const result = await post(
            "/api/personal-knowledge/import",
            data,
            true,
          );
          if (!active || base !== chosen) return;
          reportImport(result);
          await loadDocuments();
          await showDocument(result.document_id);
        },
        "button primary",
      ),
    );
    panel.append(body);
    parent.append(panel);
    return panel;
  }
  async function appendContent(id, versionId, parent, turn, chosen) {
    const result = await call("content", {
      document_id: id,
      ...(versionId ? { version_id: versionId } : {}),
    });
    if (!valid(chosen, turn) || documentId !== id) return;
    const text = el("div", "", { class: "parsed-content" });
    for (const page of result.pages)
      text.append(
        el(
          "h4",
          page.page === null ? "文本（无页码）" : `PDF 第 ${page.page} 页`,
        ),
        el("pre", page.text, { class: "content-text" }),
      );
    parent.replaceChildren(text);
  }
  async function showDocument(id) {
    const chosen = base,
      turn = ++sequence;
    documentId = id;
    revealDetails();
    loading(details, "正在加载资料详情");
    docs
      .querySelectorAll("[data-document-id]")
      .forEach((node) =>
        node.setAttribute(
          "aria-pressed",
          String(node.dataset.documentId === id),
        ),
      );
    const result = await call("document", { document_id: id });
    if (!valid(chosen, turn)) return;
    details.replaceChildren();
    detailHeader(result.name);
    if (
      lastImport?.base === chosen &&
      lastImport.documentId === id &&
      lastImport.versionId === result.current_version_id
    )
      details.append(
        el("p", lastImport.message, {
          class: "notice feedback import-result",
          role: "status",
          "aria-live": "polite",
        }),
      );
    const info = el("dl", "", { class: "metadata" });
    metadata("文档 ID", id, info);
    metadata("当前修订", result.revision, info);
    metadata("当前可用版本", result.current_version_id, info);
    details.append(info);
    const states = await call("index_status", {
      knowledge_base_id: result.knowledge_base_id,
    });
    if (!valid(chosen, turn)) return;
    const state = states.documents.find((d) => d.document_id === id);
    const indexPanel = el("div", "", { class: "index-panel" });
    const actions = el("div", "", { class: "actions" });
    actions.append(
      button(
        "索引当前版本 / 重试失败",
        () => indexDocument(id, false),
        "button primary",
      ),
      button("重建当前版本索引", () => indexDocument(id, true)),
    );
    indexPanel.append(
      el("h4", "检索索引"),
      el("p", indexDescription(states, state), { "data-index-status": id }),
      el(
        "p",
        "建立索引会把这篇资料的解析片段发送至配置的 embedding 端点；本地 Ollama 在本机处理，远程服务可能按量收费。",
        { class: "muted" },
      ),
      actions,
    );
    details.append(indexPanel);
    const renamePanel = disclosure("资料名称");
    const renamed = input("资料展示名称", { value: result.name });
    const rename = async () => {
      await call("rename_document", { document_id: id, name: renamed.value });
      if (!valid(chosen, turn)) return;
      await loadDocuments();
      await showDocument(id);
    };
    renamePanel.append(form([renamed, button("更改展示名称", rename)], rename));
    details.append(renamePanel);
    const versions = disclosure(`版本记录 · ${result.versions.length}`, true);
    for (const version of result.versions) {
      const entry = el("article", "", { class: "version-entry" });
      const line = el("div", "", { class: "version-heading" });
      line.append(el("code", version.id), badge("解析", version.status));
      if (version.id === result.current_version_id)
        line.append(el("span", "当前可用", { class: "muted" }));
      const raw = disclosure("查看完整版本元数据");
      raw.append(json(version));
      entry.append(line, raw);
      if (version.status === "ready") {
        const content = el("div");
        entry.append(
          button(
            `查看版本 ${version.id} 的解析内容`,
            () => appendContent(id, version.id, content, turn, chosen),
            "button quiet",
          ),
          content,
        );
      }
      versions.append(entry);
    }
    details.append(versions);
    const content = el("div");
    details.append(
      button("查看当前可用版本的解析内容", () =>
        appendContent(id, null, content, turn, chosen),
      ),
      content,
    );
    uploadControls(details, result);
    const danger = el("div", "", { class: "danger-zone" });
    danger.append(
      el("p", "仅删除知识库副本，Windows 原文件保留。", { class: "muted" }),
      button("预览删除资料", () => deletion("document", id), "button danger"),
    );
    details.append(danger);
  }
  async function deletion(kind, target) {
    const chosen = base,
      previousDocument = documentId,
      turn = ++sequence;
    const preview = await call("delete_preview", { kind, target });
    if (!valid(chosen, turn)) return;
    revealDetails();
    details.replaceChildren();
    detailHeader(`确认删除：${preview.name}`);
    const warning = el("div", "", { class: "delete-warning" });
    warning.append(
      el("h4", "请核对删除范围"),
      el(
        "p",
        "仅删除知识库保存的原件副本、解析内容、索引及资料记录，不删除 Windows 原文件。引用入口立即失效。既有聊天已收到的片段仍属于聊天历史；若需清除这些副本，请另行删除相应聊天及备份。学习历史的讲解、题目、答案及反馈也会保留，可能含原资料摘录或转述；资料删除不会擦除这些文本。此操作无法撤销，无需 Windows 密钥。目标变化后本次确认失效。",
      ),
    );
    details.append(warning, json(preview));
    const actions = el("div", "", { class: "actions" });
    actions.append(
      button(
        "确认上述范围并删除",
        async () => {
          const result = await post("/api/personal-knowledge/delete", {
            kind,
            target,
            digest: preview.digest,
          });
          if (!valid(chosen, turn)) return;
          stopPolling();
          resetDetails();
          if (kind === "knowledge_base" && base === target) base = null;
          announce(
            `已删除。副本待清理: ${result.cleanup_pending}；索引清理待重试: ${result.index_cleanup_pending ? "是" : "否"}；Windows 原文件保留。`,
          );
          await refresh();
        },
        "button danger",
      ),
      button("取消", async () => {
        resetDetails();
        if (previousDocument && kind === "document")
          await showDocument(previousDocument);
      }),
    );
    details.append(actions);
    actions.lastChild.focus({ preventScroll: true });
  }
  async function localPicker() {
    const chosen = base;
    documentId = null;
    const turn = ++sequence;
    revealDetails();
    loading(details, "正在读取 Windows 授权目录");
    const roots = await call("local_roots");
    if (!valid(chosen, turn)) return;
    details.replaceChildren();
    detailHeader("选择 Windows 授权文件");
    details.append(
      el("p", "只读取你明确选定的文件，原文件保留在 Windows 中。", {
        class: "muted",
      }),
    );
    const rootSelect = el("select", "", { "aria-label": "授权目录" });
    for (const item of roots.roots)
      rootSelect.append(
        el("option", `${item.root_id} · ${item.actual_path}`, {
          value: item.root_id,
        }),
      );
    const path = input("授权目录内相对子目录（空表示根）");
    const entries = el("div", "", { class: "local-entries" });
    let request = 0;
    async function list() {
      const requestId = ++request,
        rootId = rootSelect.value,
        relativePath = path.value;
      const result = await call("local_list", {
        root_id: rootId,
        relative_path: relativePath,
      });
      if (!valid(chosen, turn) || requestId !== request) return;
      entries.replaceChildren(
        el("p", result.actual_path, { class: "path-label" }),
      );
      for (const entry of result.entries) {
        const row = el("div", "", { class: "local-entry" });
        if (entry.kind === "directory")
          row.append(
            el("span", "目录", { class: "file-kind" }),
            button(
              `打开 ${entry.name}`,
              async () => {
                path.value = [relativePath, entry.name]
                  .filter(Boolean)
                  .join("/");
                await list();
              },
              "button quiet",
            ),
          );
        else if (entry.kind === "file" && /\.(pdf|md|txt)$/i.test(entry.name))
          row.append(
            el("span", entry.name.split(".").pop().toUpperCase(), {
              class: "file-kind",
            }),
            button(
              `导入 ${entry.name}`,
              async () => {
                const relative_path = [relativePath, entry.name]
                  .filter(Boolean)
                  .join("/");
                const imported = await call("import_local", {
                  knowledge_base_id: chosen,
                  root_id: rootId,
                  relative_path,
                });
                if (!valid(chosen, turn)) return;
                reportImport(imported);
                await loadDocuments();
                await showDocument(imported.document_id);
              },
              "button quiet",
            ),
          );
        else
          row.append(
            el("span", entry.name),
            el("span", `${entry.kind}（不可导入）`, { class: "muted" }),
          );
        entries.append(row);
      }
      if (!result.entries.length)
        entries.append(el("p", "目录为空", { class: "muted" }));
    }
    details.append(
      rootSelect,
      form([path, button("列出所选目录", list)], list),
      entries,
    );
    if (!roots.roots.length) {
      entries.append(
        el("p", "没有可用授权目录，请先配置 Windows 本地文件服务。", {
          class: "muted",
        }),
      );
      return;
    }
    await list();
  }
  async function loadDocuments() {
    const chosen = base,
      turn = ++listSequence;
    if (!chosen) {
      docs.replaceChildren(
        empty(
          "创建第一个知识库",
          "按主题归集资料，从左侧创建知识库后开始导入。",
        ),
      );
      return;
    }
    loading(docs, "正在加载资料列表");
    const result = await call("documents", { knowledge_base_id: chosen });
    if (!active || chosen !== base || turn !== listSequence) return;
    const heading = el("div", "", { class: "workspace-heading" });
    const title = el("div");
    title.append(
      el("p", "当前知识库", { class: "eyebrow" }),
      el("h2", currentBase()?.name || "资料列表"),
    );
    const uploadAction = button(
      "导入资料",
      () => {
        upload.open = true;
        upload.scrollIntoView({ behavior: "auto", block: "nearest" });
        upload.querySelector("input").focus({ preventScroll: true });
      },
      "button primary",
    );
    heading.append(title, uploadAction);
    docs.replaceChildren(heading);
    const summary = el("div", "", { class: "list-summary" });
    summary.append(
      el("h3", "资料列表"),
      el("span", `${result.documents.length} 篇资料`, { class: "muted" }),
      button("刷新资料", loadDocuments, "button quiet"),
    );
    docs.append(summary);
    const list = el("div", "", { class: "document-list" });
    for (const doc of result.documents) {
      const row = button(
        "",
        () => showDocument(doc.document_id),
        "document-row",
      );
      row.setAttribute("data-document-id", doc.document_id);
      row.setAttribute("aria-pressed", String(documentId === doc.document_id));
      const extension =
        /\.(pdf|md|txt)$/i.exec(doc.name)?.[1]?.toUpperCase() || "DOC";
      const content = el("div", "", { class: "document-text" });
      content.append(
        el("span", doc.name, { class: "document-name" }),
        el("span", `修订 ${doc.revision} · ${doc.document_id}`, {
          class: "document-meta",
          title: doc.document_id,
        }),
      );
      const states = el("div", "", { class: "document-states" });
      states.append(badge("解析", doc.status), badge("索引", doc.index_status));
      if (doc.error || doc.index_error)
        content.append(
          el("span", [doc.error, doc.index_error].filter(Boolean).join(" · "), {
            class: "inline-error",
          }),
        );
      row.append(
        el("span", extension, { class: "file-kind", "aria-hidden": "true" }),
        content,
        states,
        el("span", "查看", { class: "row-action", "aria-hidden": "true" }),
      );
      list.append(row);
    }
    if (!result.documents.length)
      list.append(
        empty(
          "还没有资料",
          "上传 PDF、Markdown 或 TXT，也可以从 Windows 授权目录中选取。",
        ),
      );
    docs.append(list);
    const hint = disclosure("知识库 ID 与聊天使用");
    hint.append(
      el(
        "p",
        `当前选定知识库 ID：${chosen}。聊天选择不会自动同步，请在聊天中明确指定这个 ID 或知识库名称。`,
        { class: "muted" },
      ),
    );
    docs.append(hint);
    const importArea = el("div", "", { class: "import-area" });
    const upload = uploadControls(importArea);
    importArea.append(
      button(
        "从 Windows 授权目录选取文件",
        localPicker,
        "button quiet windows-import",
      ),
    );
    docs.append(importArea);
    const search = el("section", "", {
      class: "search-panel",
      "aria-label": "知识库检索测试",
    });
    const searchHeading = el("div", "", { class: "section-heading" });
    searchHeading.append(
      el("h3", "检索测试"),
      el("span", "仅检索当前知识库", { class: "muted" }),
    );
    search.append(
      searchHeading,
      el(
        "p",
        "用一个问题验证资料是否可检索。问题会发送至配置的 embedding 服务。",
        { class: "muted" },
      ),
    );
    const query = input("在当前知识库检索测试（问题会发送至 embedding 服务）", {
      placeholder: "例如：Redis 的持久化方式有哪些？",
    });
    const hits = el("div", "", { class: "search-results", role: "status" });
    let searchSequence = 0;
    async function retrieve() {
      if (!query.value.trim()) throw Error("请输入检索问题");
      const searchTurn = ++searchSequence;
      loading(hits, "正在检索资料");
      try {
        const found = await call("search", {
          knowledge_base_ids: [chosen],
          query: query.value,
        });
        if (
          !active ||
          chosen !== base ||
          turn !== listSequence ||
          searchTurn !== searchSequence
        )
          return;
        hits.replaceChildren(
          el(
            "p",
            `检索 ${found.elapsed_ms} ms · ${found.evidence.length} 个片段；分数为相似度，不是正确概率。${found.evidence_insufficient ? "资料不足：没有达到阈值的证据。" : "回答是否正确仍需检查证据。"}`,
            { class: "muted" },
          ),
        );
        for (const evidence of found.evidence) {
          const location =
            evidence.location.page === null
              ? "文本，无页码"
              : `PDF 第 ${evidence.location.page} 页`;
          const entry = el("article", "", { class: "evidence" });
          entry.append(
            el("h4", evidence.document_name),
            el(
              "p",
              `版本 ${evidence.version_id} · 片段 ${evidence.chunk_id} · ${location} · 相似度 ${evidence.score} · ${{ user_note: "用户笔记（非独立证据）", assistant_confirmed_note: "助手生成、用户确认的笔记（非独立证据）" }[evidence.source_type] || "原始资料"}${evidence.possibly_outdated ? " · 原依据可能过时" : ""}`,
              { class: "muted" },
            ),
            el("pre", evidence.text, { class: "content-text" }),
          );
          const citation = new URL(
            evidence.citation_url,
            document.location.origin,
          );
          if (
            citation.origin === document.location.origin &&
            citation.pathname.startsWith("/api/personal-knowledge/citations/")
          )
            entry.append(
              el("a", "查看受权限控制的原文片段", {
                href: citation.href,
                target: "_blank",
                rel: "noopener noreferrer",
              }),
            );
          hits.append(entry);
        }
      } catch (error) {
        if (active && chosen === base && searchTurn === searchSequence)
          hits.replaceChildren(
            el("p", "检索未完成，请检查错误信息后重试。", { class: "muted" }),
          );
        throw error;
      }
    }
    search.append(
      form([query, button("检索当前选定知识库", retrieve)], retrieve),
      hits,
    );
    docs.append(search);
  }
  function renderBases() {
    const heading = el("div", "", { class: "base-heading" });
    heading.append(
      el("h3", "知识库"),
      el("span", String(catalog.length), { class: "base-count" }),
    );
    bases.replaceChildren(heading);
    const name = input("知识库名称");
    const create = async () => {
      if (!name.value.trim()) throw Error("请输入知识库名称");
      const result = await call("create", { name: name.value });
      if (!active) return;
      if (result.knowledge_base_id) {
        base = result.knowledge_base_id;
        resetDetails();
        stopPolling();
      }
      announce("知识库已创建");
      await refresh();
    };
    bases.append(
      form([name, button("创建知识库", create, "button create-base")], create),
    );
    const nav = el("nav", "", {
      class: "base-list",
      "aria-label": "选择知识库",
    });
    for (const item of catalog) {
      const row = el("div", "", { class: "base-item" });
      const select = button(
        item.name,
        () => selectBase(item.knowledge_base_id),
        "base-select",
      );
      select.setAttribute(
        "aria-pressed",
        String(item.knowledge_base_id === base),
      );
      select.title = item.name;
      row.append(select);
      const manage = disclosure("管理");
      manage.classList.add("base-management");
      const rename = input("知识库新名称", { value: item.name });
      const renameBase = async () => {
        await call("rename", {
          knowledge_base_id: item.knowledge_base_id,
          name: rename.value,
        });
        if (!active) return;
        announce("知识库已重命名");
        await refresh();
      };
      manage.append(
        form([rename, button("重命名", renameBase)], renameBase),
        button(
          "预览删除知识库",
          () => deletion("knowledge_base", item.knowledge_base_id),
          "button danger",
        ),
      );
      row.append(manage);
      nav.append(row);
    }
    bases.append(nav);
    const maintenance = el("div", "", { class: "maintenance" });
    maintenance.append(
      button(
        "查询并重试知识库副本清理",
        async () => {
          const turn = ++sequence,
            chosen = base;
          documentId = null;
          const result = await call("cleanup");
          if (!valid(chosen, turn)) return;
          revealDetails();
          details.replaceChildren();
          detailHeader("副本清理结果");
          details.append(
            el("p", "查询并重试知识库副本与索引清理。", { class: "muted" }),
            json(result),
          );
        },
        "button quiet cleanup-button",
      ),
      el("p", "资料与版本独立保存", { class: "muted" }),
    );
    bases.append(maintenance);
  }
  async function refresh() {
    const turn = ++baseSequence;
    const result = await call("bases");
    if (!active || turn !== baseSequence) return;
    catalog = result.knowledge_bases;
    if (!catalog.some((item) => item.knowledge_base_id === base)) {
      base = catalog[0]?.knowledge_base_id || null;
      resetDetails();
      stopPolling();
    }
    renderBases();
    await loadDocuments();
  }
  loading(bases, "正在加载知识库");
  docs.append(empty("正在打开资料库", "正在读取知识库与资料状态。"));
  header.append(button("重新加载", refresh, "button quiet"));
  void run(refresh);
  return {
    dispose() {
      active = false;
      sequence++;
      listSequence++;
      baseSequence++;
      stopPolling();
      root.remove();
      stylesheet.remove();
    },
  };
}
export default {
  apiVersion: 1,
  module: "knowledge-base.v1",
  icon: "book-open",
  surfaces: [
    {
      id: "library",
      slot: "page",
      title: "个人知识库",
      navigation: {
        label: "Knowledge library",
        labelZh: "个人知识库",
        icon: "book-open",
      },
      mount,
    },
  ],
};
