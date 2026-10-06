// Same learning service, theme and identity. Only a browser button approves import.
export function mountStudy(surface, context, onNavigate = () => {}) {
  const el = (tag, text = "", attrs = {}) => {
    const n = document.createElement(tag);
    n.textContent = String(text);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const root = el("section", "", { class: "study-workspace", hidden: "" });
  const notice = el("p", "", { role: "alert", class: "notice", hidden: "" });
  const content = el("div");
  root.append(notice, content);
  surface.append(root);
  const panes = new Map(),
    requests = new Map(),
    drafts = new Map();
  let active = true,
    tab = null,
    seq = 0;
  const messages = {
    study_revision_conflict:
      "记录已变化。草稿保留；请重新加载并核对最新修订后再保存。",
    stale_confirmation: "确认预览已过期，请重新核对具体正文、来源和目标库。",
    study_request_in_progress: "同一请求正在处理，请稍后重试。",
    study_publish_pending_retry:
      "入库处理中断，确认快照保留。请重试同一确认，服务会恢复关联并避免重复文档。",
    study_note_not_imported:
      "当前修订尚未成功入库，或知识库副本已变化；请核对后重试。",
    study_note_target_fixed:
      "此笔记已绑定原知识库，不能改存到另一个库。请更新原库；若要加入其他库，请另建草稿并亲自确认。",
    learning_source_changed:
      "练习依据已更新或删除，已停止评分。请回到章节生成当前资料的练习。",
    study_actual_review_answer_required:
      "请先亲自完成本次复习练习，再确认复习结果。",
    study_incorrect_answer_shorten_or_restart:
      "本次客观题仍答错，请选择缩短间隔或重新开始。",
    study_feedback_insufficient:
      "评价依据不足，本次复习不能推进；请重新选择当前有效练习。",
    not_found: "记录不存在或当前账号无权访问。",
  };
  const states = {
    draft: "草稿",
    publishing: "入库恢复待重试",
    imported: "已入库",
    import_failed: "解析失败",
    suggested: "待收录",
    collected: "已收录",
    removed: "已移除",
  };
  const documentStates = {
    not_imported: "尚未入库",
    current: "当前版本",
    changed: "版本已变化",
    deleted_or_unavailable: "已删除或不可用",
  };
  const indexStates = {
    unindexed: "尚未建立索引",
    processing: "正在建立索引",
    ready: "索引已就绪",
    failed: "索引失败，可重试",
    unavailable: "索引不可用",
  };
  const notify = (text) => {
    notice.textContent = text;
    notice.hidden = false;
    notice.scrollIntoView?.({ block: "nearest" });
  };
  const check = (r) => {
    if (!r?.ok)
      throw Error(
        messages[r?.error?.code] ||
          `操作未完成（${r?.error?.code || "未知错误"}），保留输入后可重试。`,
      );
    return r;
  };
  const call = async (a, p = {}) => check(await context.callBackend(a, p));
  const mutate = async (a, p, transport = null) => {
    const signature = a + JSON.stringify(p);
    if (!requests.has(signature))
      requests.set(signature, crypto.randomUUID().replaceAll("-", ""));
    const payload = { ...p, request_id: requests.get(signature) };
    const result = transport
      ? check(await transport(payload))
      : await call(a, payload);
    requests.delete(signature);
    return result;
  };
  const valid = (turn, kind = tab) => active && seq === turn && tab === kind;
  async function run(fn, button) {
    if (!active || button?.disabled) return;
    const turn = seq;
    if (button) button.disabled = true;
    notice.hidden = true;
    try {
      await fn();
    } catch (e) {
      if (valid(turn)) {
        notice.textContent = e.message;
        notice.hidden = false;
      }
    } finally {
      if (active && button) button.disabled = false;
    }
  }
  const button = (label, fn, attrs = {}) => {
    const n = el("button", label, { type: "button", ...attrs });
    n.addEventListener("click", () => void run(() => fn(n), n));
    return n;
  };
  const field = (parent, label, value = "", tag = "input", attrs = {}) => {
    const wrap = el("label", label),
      input = el(tag, "", { "aria-label": label, ...attrs });
    input.value = value;
    wrap.append(input);
    parent.append(wrap);
    return input;
  };
  function sources(parent, items = []) {
    for (const s of items) {
      const row = el("p", "", { class: "source" });
      const label = `${s.document_name || "不可用来源"} · 版本 ${s.version_id} · ${s.source_type === "assistant_confirmed_note" ? "助手笔记（非独立证据）" : s.source_type === "user_note" ? "用户笔记（非独立证据）" : "原始资料"}`;
      if (
        s.status === "valid" &&
        /^\/api\/personal-knowledge\/citations\/[0-9a-f]{32}$/.test(
          s.citation_url || "",
        )
      )
        row.append(
          el("a", label, {
            href: s.citation_url,
            target: "_blank",
            rel: "noopener",
          }),
        );
      else
        row.textContent = `${label} · ${s.status === "updated" ? "依据已变化" : "来源不可用"}`;
      parent.append(row);
    }
  }
  function pane(kind) {
    if (!panes.has(kind)) {
      const n = el("div", "", { class: "layout" }),
        list = el("aside"),
        detail = el("main");
      n.append(list, detail);
      panes.set(kind, { node: n, list, detail, selected: null, detailSeq: 0 });
    }
    return panes.get(kind);
  }
  async function show(kind) {
    if (!active) return;
    seq++;
    tab = kind;
    root.hidden = false;
    onNavigate(kind);
    notice.hidden = true;
    const p = pane(kind);
    content.replaceChildren(p.node);
    await run(() => loadList(kind));
  }
  async function loadList(kind) {
    const turn = seq,
      p = pane(kind),
      response = await call(`${kind}_list`);
    if (!valid(turn, kind)) return;
    p.list.replaceChildren(
      el(
        "h2",
        { notes: "笔记", mistakes: "错题与复核", reviews: "应用内复习" }[kind],
      ),
      button("重新加载列表", () => loadList(kind), { class: "quiet" }),
    );
    if (kind === "notes")
      p.list.append(
        button("手写笔记", newNote, { class: "primary" }),
        el("p", "保存草稿与确认入库是两个动作。", { class: "muted" }),
      );
    const items = response[kind];
    if (!items.length) p.list.append(el("p", "暂无记录", { class: "muted" }));
    const dates = {
      today: "今日",
      overdue: "逾期",
      future: "后续",
      paused: "暂停",
    };
    for (const item of items) {
      const label =
        kind === "notes"
          ? item.title
          : kind === "mistakes"
            ? `${item.knowledge_points.join(" / ")} · ${item.classification === "needs_review" ? "建议复核" : "客观题答错"}`
            : `${item.title} · ${dates[item.due_status]} · ${item.due_date}`;
      p.list.append(
        button(
          label,
          () =>
            open(
              kind,
              item[
                `${kind === "notes" ? "note" : kind === "mistakes" ? "mistake" : "review"}_id`
              ],
            ),
          { class: "plan-row" },
        ),
        el(
          "p",
          `${states[item.status] || ""}${item.possibly_outdated ? " · 来源已变化" : ""}`,
          { class: "muted" },
        ),
      );
    }
    if (response.truncated)
      p.list.append(
        el("p", "仅展示最近 50 条；其余记录可在聊天按明确 ID 查询。", {
          class: "warning",
        }),
      );
    if (!p.selected && !p.detail.childNodes.length)
      p.detail.append(
        el(
          "p",
          kind === "reviews"
            ? "采用 1、3、7、14 天等规则间隔。应用内展示，不发送外部通知；请亲自确认复习结果。"
            : "选择记录查看内容与来源。",
          { class: "muted" },
        ),
      );
  }
  async function open(kind, id) {
    const p = pane(kind),
      turn = seq,
      request = ++p.detailSeq;
    p.selected = id;
    let r = await call(`${kind}_get`, {
      [kind === "notes"
        ? "note_id"
        : kind === "mistakes"
          ? "mistake_id"
          : "review_id"]: id,
    });
    if (kind === "reviews" && r.review.status !== "paused") {
      try {
        r = await call("reviews_start", { review_id: id });
      } catch (error) {
        r = { ...r, unavailable: error.message };
      }
    }
    if (!valid(turn, kind) || p.detailSeq !== request) return;
    p.detail.replaceChildren();
    if (kind === "notes") await renderNote(p.detail, r.note, turn);
    else if (kind === "mistakes") renderMistake(p.detail, r.mistake, turn);
    else renderReview(p.detail, r, turn);
  }
  function newNote() {
    const p = pane("notes");
    p.selected = "new";
    p.detailSeq++;
    p.detail.replaceChildren(el("h2", "手写笔记"));
    const form = el("form"),
      cached = drafts.get("new") || { title: "", body: "" };
    const title = field(form, "笔记标题", cached.title, "input", {
        required: "",
        maxlength: "120",
      }),
      body = field(form, "笔记正文", cached.body, "textarea", {
        required: "",
        maxlength: "6000",
      });
    const remember = () =>
      drafts.set("new", { title: title.value, body: body.value });
    title.addEventListener("input", remember);
    body.addEventListener("input", remember);
    const save = el("button", "保存草稿", { type: "submit", class: "primary" });
    form.append(save);
    p.detail.append(form);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const turn = seq,
        detailTurn = p.detailSeq,
        input = { title: title.value, body: body.value };
      void run(async () => {
        const r = await mutate("notes_write", input);
        if (title.value === input.title && body.value === input.body)
          drafts.delete("new");
        if (
          valid(turn, "notes") &&
          p.selected === "new" &&
          p.detailSeq === detailTurn
        ) {
          await loadList("notes");
          await open("notes", r.note.note_id);
        }
      }, save);
    });
  }
  const sourceLabel = (n) =>
    n.source_type === "user_note"
      ? "用户笔记（非独立证据）"
      : "助手生成、用户确认后入库的笔记（非独立证据）";
  async function renderNote(parent, n, turn) {
    parent.append(
      el("h2", n.title),
      el(
        "p",
        `修订 ${n.revision} · ${sourceLabel(n)} · ${states[n.status] || n.status}`,
      ),
      el(
        "p",
        `${n.document_id ? `已入库笔记修订 ${n.published_note_revision ?? "未知"} · 知识库 ${n.knowledge_base_name || "不可用"} · ` : ""}文档 ${documentStates[n.document_status] || n.document_status} · ${indexStates[n.index_status] || n.index_status}`,
      ),
    );
    if (n.document_id && n.status !== "imported")
      parent.append(
        el(
          "p",
          `当前草稿修订 ${n.revision} 尚未入库。文档和索引状态属于此前已入库的版本；请先在原知识库确认当前修订，再建立新版本索引。`,
          { class: "warning" },
        ),
      );
    if (n.possibly_outdated)
      parent.append(
        el(
          "p",
          "原依据已变化或不可用。这是历史学习内容，入库不代表原始资料仍有效。",
          { class: "warning" },
        ),
      );
    sources(parent, n.sources);
    parent.append(
      el(
        "p",
        n.retention_notice || "删除笔记保留知识库副本和不可变的历史记录。",
        { class: "retention" },
      ),
    );
    const form = el("form"),
      cached = drafts.get(n.note_id) || n;
    const title = field(form, "笔记标题", cached.title, "input", {
        required: "",
        maxlength: "120",
      }),
      body = field(form, "笔记正文", cached.body, "textarea", {
        required: "",
        maxlength: "6000",
      });
    let approval = null;
    const remember = () => {
      drafts.set(n.note_id, { title: title.value, body: body.value });
      if (approval) approval.disabled = true;
    };
    title.addEventListener("input", remember);
    body.addEventListener("input", remember);
    const save = el("button", "保存草稿", { type: "submit", class: "primary" });
    form.append(save);
    parent.append(form);
    save.disabled = n.status === "publishing";
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = { title: title.value, body: body.value };
      void run(async () => {
        const r = await mutate("notes_edit", {
          note_id: n.note_id,
          expected_revision: n.revision,
          ...input,
        });
        if (title.value === input.title && body.value === input.body)
          drafts.delete(n.note_id);
        if (valid(turn, "notes") && pane("notes").selected === n.note_id) {
          await loadList("notes");
          await open("notes", r.note.note_id);
        }
      }, save);
    });
    const actions = el("div", "", { class: "actions" });
    parent.append(actions);
    actions.append(
      button("安排复习", async () => {
        await schedule("note", n.note_id);
        if (valid(turn, "notes")) {
          notice.textContent = "复习对象已保存，可在复习页查看日期。";
          notice.hidden = false;
        }
      }),
      button("删除笔记（保留知识库副本）", async () => {
        const box = el("section", "", { class: "warning" });
        box.append(
          el(
            "p",
            "只删除这份笔记的可用入口并暂停复习；已入库文档、原资料和答题历史保留。知识库副本请到知识库页面另行确认删除。",
          ),
          button("确认仅删除此笔记", async () => {
            await mutate("notes_delete", {
              note_id: n.note_id,
              expected_revision: n.revision,
            });
            if (valid(turn, "notes") && pane("notes").selected === n.note_id) {
              pane("notes").selected = null;
              parent.replaceChildren(
                el("p", "笔记已删除；知识库副本和历史保留。"),
              );
              await loadList("notes");
            }
          }),
        );
        parent.append(box);
      }),
    );
    if (n.document_id) {
      const index = button("建立或重试索引", async () => {
        const result = await call("notes_index", { note_id: n.note_id });
        if (valid(turn, "notes") && pane("notes").selected === n.note_id) {
          await open("notes", n.note_id);
          if (valid(turn, "notes") && pane("notes").selected === n.note_id)
            notify(
              result.index_status === "ready"
                ? "索引已就绪，可以检索这份笔记。"
                : "索引任务已启动。完成后重新加载笔记查看结果。",
            );
        }
      });
      index.disabled =
        n.status !== "imported" ||
        n.approved_revision !== n.revision ||
        n.document_status !== "current";
      actions.append(index);
    }
    const targetWrap = el("div");
    parent.append(targetWrap);
    const bases = await context.callBackend("bases", {});
    if (
      !valid(turn, "notes") ||
      pane("notes").selected !== n.note_id ||
      !parent.isConnected
    )
      return;
    check(bases);
    const target = field(targetWrap, "目标知识库", "", "select", {
      required: "",
    });
    target.append(el("option", "请选择", { value: "" }));
    for (const b of bases.knowledge_bases)
      target.append(el("option", b.name, { value: b.knowledge_base_id }));
    target.value = n.knowledge_base_id || "";
    target.disabled = Boolean(n.document_id || n.knowledge_base_id);
    if (target.disabled)
      targetWrap.append(
        el(
          "p",
          n.knowledge_base_id
            ? "目标已绑定原知识库；确认当前修订将更新该库中的同一文档。加入另一个库需要另建草稿。"
            : "原知识库副本已删除或不可访问，请另建草稿再确认入库。",
          { class: "muted" },
        ),
      );
    targetWrap.append(
      button("预览具体修订并确认入库", async () => {
        if (title.value !== n.title || body.value !== n.body)
          throw Error("请先保存当前草稿，再预览具体修订。");
        const r = await call("notes_preview", {
          note_id: n.note_id,
          expected_revision: n.revision,
          knowledge_base_id: target.value,
        });
        if (!valid(turn, "notes") || pane("notes").selected !== n.note_id)
          return;
        const preview = r.preview,
          box = el("section", "", { class: "exercise note-preview" });
        box.append(
          el(
            "h3",
            `确认入库：${preview.knowledge_base_name} / ${preview.title}`,
          ),
          el(
            "p",
            `修订 ${preview.revision} · ${sourceLabel(preview)} · ${preview.possibly_outdated ? "原依据已变化" : "来源检查完成"}`,
          ),
          el("pre", preview.body),
        );
        if (preview.recovering_pending)
          box.append(
            el(
              "p",
              "这是此前本人批准的固定快照，用于恢复中断的文档关联；下面显示当前来源状态，恢复不会重复创建文档。",
              { class: "warning" },
            ),
          );
        sources(box, preview.current_sources || preview.sources);
        approval = button(
          "我已核对，确认此修订入库",
          async () => {
            const result = await mutate(
              "notes_approve",
              {
                note_id: n.note_id,
                expected_revision: preview.revision,
                knowledge_base_id: preview.knowledge_base_id,
                expected_document_revision: preview.document_revision,
                digest: preview.digest,
              },
              async (payload) => {
                const token = document.cookie
                  .split("; ")
                  .find((v) => v.startsWith("csrf_token="))
                  ?.slice(11);
                if (!token) throw Error("缺少登录确认凭据，请重新登录。");
                const response = await fetch(
                  "/api/personal-learning/notes/approve",
                  {
                    method: "POST",
                    credentials: "same-origin",
                    signal: context.signal,
                    headers: {
                      "Content-Type": "application/json",
                      "X-CSRF-Token": decodeURIComponent(token),
                    },
                    body: JSON.stringify(payload),
                  },
                );
                const data = await response.json();
                if (!response.ok)
                  throw Error("真人确认入口拒绝请求，请检查登录和权限。");
                return data;
              },
            );
            if (valid(turn, "notes") && pane("notes").selected === n.note_id) {
              await loadList("notes");
              if (!valid(turn, "notes") || pane("notes").selected !== n.note_id)
                return;
              await open("notes", result.note.note_id);
              if (valid(turn, "notes") && pane("notes").selected === n.note_id)
                notify(
                  result.note.status === "imported"
                    ? result.note.index_status === "unindexed"
                      ? "笔记已入库。尚未建立索引，请点击“建立或重试索引”后再检索。"
                      : `笔记已入库。${indexStates[result.note.index_status] || result.note.index_status}。`
                    : "笔记文件已保存，但解析失败；请编辑草稿后重新确认入库。尚未建立索引。",
                );
            }
          },
          { class: "primary" },
        );
        box.append(approval);
        parent.querySelector(".note-preview")?.remove();
        parent.append(box);
      }),
    );
  }
  const schedule = (kind, target_id) =>
    mutate("reviews_schedule", { kind, target_id, timezone: "Asia/Shanghai" });
  function feedback(parent, a) {
    parent.replaceChildren(
      el(
        "p",
        `${a.reference_evaluation ? "参考评价" : a.score === 1 ? "本次回答正确" : "本次仍需检查"}：${a.feedback}`,
      ),
    );
    if (a.evaluation_notice)
      parent.append(el("p", a.evaluation_notice, { class: "warning" }));
    sources(parent, a.sources);
    parent.append(el("p", "一次答对不等于已掌握。", { class: "muted" }));
  }
  function renderMistake(parent, m, turn) {
    parent.append(
      el("h2", m.exercise.question),
      el(
        "p",
        `${m.classification === "needs_review" ? "简答题：建议复核，模型评价不等于判错" : "客观题：程序判错"} · ${m.status}`,
      ),
      el("p", `原答案：${m.original_attempt.answer}`),
    );
    const original = el("div");
    feedback(original, m.original_attempt);
    parent.append(original);
    if (m.possibly_outdated)
      parent.append(
        el("p", "来源已变化，停止使用旧依据重练评分。请从章节生成当前练习。", {
          class: "warning",
        }),
      );
    const update = async (action) => {
      await mutate(action, {
        mistake_id: m.mistake_id,
        expected_revision: m.revision,
      });
      if (
        valid(turn, "mistakes") &&
        pane("mistakes").selected === m.mistake_id
      ) {
        await loadList("mistakes");
        await open("mistakes", m.mistake_id);
      }
    };
    parent.append(
      button(
        m.status === "collected"
          ? "移出错题本（保留答题历史）"
          : "我确认收录此题",
        () =>
          update(
            m.status === "collected" ? "mistakes_remove" : "mistakes_collect",
          ),
      ),
    );
    if (m.status === "collected") {
      parent.append(
        button("安排复习", () => schedule("mistake", m.mistake_id)),
      );
      if (!m.possibly_outdated)
        parent.append(
          button("开始重练", async () => {
            const r = await call("mistakes_start", {
              mistake_id: m.mistake_id,
            });
            if (
              valid(turn, "mistakes") &&
              pane("mistakes").selected === m.mistake_id
            )
              practice(
                parent,
                r,
                "mistakes_submit",
                { mistake_id: m.mistake_id },
                turn,
              );
          }),
        );
    }
    parent.append(el("h3", "重练历史"));
    for (const a of m.retry_attempts)
      parent.append(
        button(
          `${new Date(a.created * 1000).toLocaleString("zh-CN")} · ${a.evaluation}`,
          async () => {
            const r = await call("study_attempt", {
              mistake_id: m.mistake_id,
              attempt_id: a.attempt_id,
            });
            if (
              valid(turn, "mistakes") &&
              pane("mistakes").selected === m.mistake_id
            ) {
              const box = el("div");
              box.append(el("p", `当时答案：${r.attempt.answer}`));
              const content = el("div");
              feedback(content, r.attempt);
              box.append(content);
              parent.append(box);
            }
          },
        ),
      );
    parent.append(el("p", m.retention_notice, { class: "retention" }));
  }
  function practice(parent, r, action, payload, turn, onAnswer = () => {}) {
    parent.querySelector(".study-practice")?.remove();
    const form = el("form", "", { class: "exercise study-practice" });
    const e = r.exercise;
    form.append(
      el("h3", e.question),
      el("p", `知识点：${e.knowledge_points.join("、")} · 请亲自作答`),
    );
    const answer = field(
      form,
      "本次答案",
      "",
      e.kind === "objective" ? "select" : "textarea",
      { required: "", maxlength: "2000" },
    );
    if (e.kind === "objective") {
      answer.append(el("option", "请选择", { value: "" }));
      for (const option of e.options)
        answer.append(el("option", option, { value: option }));
    }
    const submit = el("button", "提交本次答案", {
        type: "submit",
        class: "primary",
      }),
      result = el("div", "", { role: "status" });
    form.append(submit, result);
    sources(form, r.sources);
    parent.append(form);
    const kind = tab,
      selected = pane(kind).selected;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(async () => {
        const data = await mutate(action, { ...payload, answer: answer.value });
        if (
          valid(turn, kind) &&
          pane(kind).selected === selected &&
          form.isConnected
        ) {
          feedback(result, data.attempt);
          answer.disabled = true;
          submit.hidden = true;
          onAnswer(data.attempt);
          if (kind === "mistakes") await loadList("mistakes");
        }
      }, submit);
    });
  }
  function renderReview(parent, r, turn) {
    const review = r.review;
    parent.append(
      el("h2", review.title),
      el(
        "p",
        `计划日期 ${review.due_date} · ${review.timezone} · 规则 ${review.rule_version} · 间隔 ${(review.intervals || []).join("、")} 天`,
      ),
      el("p", "是否继续间隔由你确认；简答模型评价只是参考。", {
        class: "muted",
      }),
    );
    if (review.status === "paused") {
      parent.append(
        el("p", "当前复习已暂停；恢复后仍保留原到期日期和历史。"),
        button("恢复此复习对象", async () => {
          await mutate("reviews_resume", {
            review_id: review.review_id,
            expected_revision: review.revision,
          });
          if (
            valid(turn, "reviews") &&
            pane("reviews").selected === review.review_id
          ) {
            await loadList("reviews");
            await open("reviews", review.review_id);
          }
        }),
      );
      return;
    }
    if (r.unavailable) {
      parent.append(
        el("p", r.unavailable, { class: "warning" }),
        button("重试加载复习内容", () => open("reviews", review.review_id)),
        button("暂停此复习对象", async () => {
          await mutate("reviews_pause", {
            review_id: review.review_id,
            expected_revision: review.revision,
          });
          if (
            valid(turn, "reviews") &&
            pane("reviews").selected === review.review_id
          ) {
            await loadList("reviews");
            await open("reviews", review.review_id);
          }
        }),
      );
      return;
    }
    let attemptId = null;
    if (r.note) {
      parent.append(el("h3", r.note.title), el("pre", r.note.body));
      sources(parent, r.note.sources);
      if (r.note.possibly_outdated)
        parent.append(
          el("p", "历史笔记依据已变化；请核对来源后自评。", {
            class: "warning",
          }),
        );
    } else
      practice(
        parent,
        r,
        "reviews_submit",
        { review_id: review.review_id },
        turn,
        (a) => {
          attemptId = a.attempt_id;
        },
      );
    const choice = field(parent, "本人确认的复习结果", "", "select");
    choice.append(
      el("option", "请选择", { value: "" }),
      el("option", "继续下一间隔", { value: "continue" }),
      el("option", "缩短间隔", { value: "shorten" }),
      el("option", "从第一间隔重新开始", { value: "restart" }),
    );
    parent.append(
      button(
        "确认本次复习结果",
        async () => {
          if (!choice.value || (!r.note && !attemptId))
            throw Error("请亲自完成练习或阅读笔记，并选择本次复习结果。");
          const result = await mutate("reviews_finish", {
            review_id: review.review_id,
            expected_revision: review.revision,
            target_revision: r.note ? r.note.revision : r.mistake_revision,
            result: choice.value,
            attempt_id: attemptId,
          });
          if (
            valid(turn, "reviews") &&
            pane("reviews").selected === review.review_id
          ) {
            parent.replaceChildren(
              el("h2", "本次复习已记录"),
              el(
                "p",
                `下次日期：${result.review.due_date}（${result.review.timezone}）；没有标记掌握。`,
              ),
            );
            await loadList("reviews");
          }
        },
        { class: "primary" },
      ),
      button("暂停此复习对象", async () => {
        await mutate("reviews_pause", {
          review_id: review.review_id,
          expected_revision: review.revision,
        });
        if (
          valid(turn, "reviews") &&
          pane("reviews").selected === review.review_id
        ) {
          parent.replaceChildren(el("p", "复习已暂停；到期日期和历史保留。"));
          await loadList("reviews");
        }
      }),
    );
    parent.append(el("h3", "复习历史"));
    for (const h of review.history || [])
      parent.append(
        el("p", `${h.local_date} · ${h.result} → ${h.next_due_date}`),
      );
  }
  return {
    show,
    hide() {
      seq++;
      tab = null;
      root.hidden = true;
    },
    async draftFromLesson(plan_id, lesson_id) {
      await show("notes");
      const turn = seq;
      await run(async () => {
        const r = await mutate("notes_draft", { plan_id, lesson_id });
        if (valid(turn, "notes")) {
          await loadList("notes");
          await open("notes", r.note.note_id);
        }
      });
    },
    async suggest(plan_id, attempt_id) {
      await show("mistakes");
      const turn = seq;
      await run(async () => {
        const r = await mutate("mistakes_suggest", { plan_id, attempt_id });
        if (valid(turn, "mistakes")) {
          await loadList("mistakes");
          await open("mistakes", r.mistake.mistake_id);
        }
      });
    },
    dispose() {
      active = false;
      seq++;
      requests.clear();
      drafts.clear();
      root.remove();
    },
  };
}
