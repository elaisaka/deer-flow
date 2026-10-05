// Taste redesign-preserve: DESIGN_VARIANCE 4 / MOTION_INTENSITY 2 / VISUAL_DENSITY 5.
// Native product surface: inherit the host theme and preserve learning contracts.
function mount(surface, context) {
  const errors = {
    index_not_ready: "所选库有资料尚未索引或索引失败，请先在知识库页面处理。",
    embedding_not_configured: "检索服务尚未配置，请按 RAG 指南检查配置。",
    learning_source_changed:
      "本次依据的资料已变化，未保存新内容。请刷新并重新开始章节。",
    learning_revision_conflict: "计划或进度已变化，请刷新后重新编辑。",
    learning_evidence_insufficient:
      "没有取得足够资料，暂时无法讲解本章节。请补充资料并建立索引。",
    learning_paused_or_change_pending:
      "计划已暂停，或章节变化尚待核对。请先继续计划或核对变化。",
    learning_timeout: "生成超时，没有保存新记录；可重试。",
    learning_feedback_not_grounded:
      "评价未能准确核对你的原句或资料证据，本次未保存答题；请重试并核对引用。",
    learning_model_failed_or_timeout:
      "模型调用失败或超时，没有保存新记录；请检查服务后重试。",
    answer_model_not_granted:
      "学习模型尚未授权，请按学习指南配置后重启 Gateway。",
    learning_invalid_structure:
      "输入或模型返回的内容不符合要求，未保存，请检查必填条件后重试。",
    chapter_exceeds_daily_budget_split_required:
      "章节超过每天可用时间，请拆分章节或增加每天分钟数。",
    learning_lesson_revision_changed:
      "章节内容已经修改，请重新开始章节后再作答。",
    learning_request_in_progress: "同一请求正在处理，请稍后刷新或重试。",
    not_found: "记录不存在，或当前账号无权访问。",
  };
  const el = (tag, text = "", attrs = {}) => {
    const node = document.createElement(tag);
    node.textContent = String(text);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    return node;
  };
  const css = el("link", "", {
    rel: "stylesheet",
    href: new URL("./learning.css", import.meta.url).href,
    crossorigin: "use-credentials",
  });
  const root = el("div", "", { class: "learning" });
  const header = el("header", "", { class: "intro" }),
    layout = el("div", "", { class: "layout" });
  const sidebar = el("aside", "", { "aria-label": "学习计划" }),
    main = el("main", "", { "aria-label": "学习工作区" });
  const notice = el("p", "", { role: "alert", class: "notice", hidden: "" });
  const requests = new Map();
  let active = true,
    selected = null,
    sequence = 0,
    listSequence = 0,
    historySequence = 0,
    planRefreshSequence = 0,
    lessonSequence = 0,
    initialSelectionDone = false;
  const valid = (id, turn) => active && selected === id && sequence === turn;
  const call = async (action, payload = {}) => {
    const result = await context.callBackend(action, payload);
    if (!result?.ok)
      throw Error(
        errors[result?.error?.code] ||
          `请求未完成，请检查后重试（${result?.error?.code || "未知错误"}）。`,
      );
    return result;
  };
  const mutation = async (action, payload) => {
    const signature = action + JSON.stringify(payload);
    if (!requests.has(signature))
      requests.set(signature, crypto.randomUUID().replaceAll("-", ""));
    const result = await call(action, {
      ...payload,
      request_id: requests.get(signature),
    });
    requests.delete(signature);
    return result;
  };
  const run = async (fn, node) => {
    if (!active || node?.disabled) return;
    if (node) {
      node.disabled = true;
      node.setAttribute("aria-busy", "true");
    }
    notice.hidden = true;
    try {
      await fn();
    } catch (error) {
      if (active) {
        notice.textContent = error.message;
        notice.hidden = false;
        for (const pane of [sidebar, main]) {
          if (pane.querySelector(".loading"))
            pane.replaceChildren(
              el("p", "加载未完成，请点击重新加载后重试。", { class: "muted" }),
            );
        }
      }
    } finally {
      if (node && active) {
        node.disabled = false;
        node.removeAttribute("aria-busy");
      }
    }
  };
  const button = (text, fn, attrs = {}) => {
    const node = el("button", text, { type: "button", ...attrs });
    node.addEventListener("click", () => void run(() => fn(node), node));
    return node;
  };
  const field = (
    parent,
    label,
    value = "",
    { tag = "input", ...attrs } = {},
  ) => {
    const wrap = el("label", label),
      input = el(tag, "", attrs);
    input.value = value;
    wrap.append(input);
    parent.append(wrap);
    return input;
  };
  const loading = (parent, label) => {
    const state = el("div", "", {
      class: "loading",
      role: "status",
      "aria-label": label,
    });
    state.append(el("span", label, { class: "sr-only" }));
    for (let i = 0; i < 3; i++)
      state.append(el("div", "", { class: "skeleton" }));
    parent.replaceChildren(state);
  };
  const viewHeading = (title, description) => {
    const heading = el("div", "", { class: "view-heading" });
    heading.append(el("h2", title), el("p", description, { class: "muted" }));
    return heading;
  };
  const sourceLinks = (parent, sources, ids = null) => {
    for (const source of sources.filter(
      (s) => !ids || ids.includes(s.citation_id),
    )) {
      const line = el("p", "", { class: "source" });
      if (source.status === "valid") {
        const link = el("a", `${source.document_name} · 原文`, {
          href: source.citation_url,
          target: "_blank",
          rel: "noopener noreferrer",
        });
        line.append(
          link,
          el(
            "span",
            ` 版本 ${source.version_id} / ${source.location.page == null ? "文本" : `第 ${source.location.page} 页`}`,
          ),
        );
      } else
        line.append(
          el(
            "strong",
            `来源${source.status === "updated" ? "已更新" : "不可用"}，历史内容可能过时。`,
          ),
        );
      parent.append(line);
    }
  };
  async function refreshPlans() {
    const turn = ++listSequence;
    const result = await call("list");
    if (!active || turn !== listSequence) return;
    const heading = el("div", "", { class: "sidebar-heading" });
    heading.append(
      el("h2", "我的计划"),
      el("span", String(result.plans.length), { class: "count" }),
    );
    sidebar.replaceChildren(heading);
    if (!result.plans.length)
      sidebar.append(
        el("p", "还没有计划。输入目标与基础，创建第一份计划。", {
          class: "sidebar-empty muted",
        }),
      );
    const plans = el("nav", "", {
      class: "plan-list",
      "aria-label": "选择学习计划",
    });
    for (const plan of result.plans) {
      const node = button("", () => openPlan(plan.plan_id), {
        "aria-current": plan.plan_id === selected ? "true" : "false",
        class: "plan-button",
        title: plan.topic,
      });
      node.append(
        el("span", plan.topic, { class: "plan-topic" }),
        el("span", ` · ${plan.status === "paused" ? "已暂停" : "学习中"}`, {
          class: "plan-status",
        }),
      );
      plans.append(node);
    }
    sidebar.append(plans);
    const footnote = el("div", "", { class: "sidebar-footnote" });
    footnote.append(
      el("p", "按自己的节奏，逐章学习。"),
      el("p", "学习记录会随计划保留。"),
    );
    sidebar.append(footnote);
    if (result.truncated)
      sidebar.append(
        el("p", "当前显示最近 50 个计划；可用计划 ID 在聊天查询更早记录。"),
      );
    if (!initialSelectionDone) {
      initialSelectionDone = true;
      if (selected === null && result.plans.length)
        await openPlan(result.plans[0].plan_id);
    }
  }
  async function createForm() {
    initialSelectionDone = true;
    selected = null;
    for (const node of sidebar.querySelectorAll(".plan-button"))
      node.setAttribute("aria-current", "false");
    const turn = ++sequence;
    main.replaceChildren(
      viewHeading(
        "你想学会什么？",
        "计划结合选定资料与可用时间。预计时长包含练习，不能保证按时掌握。",
      ),
      el(
        "p",
        "学习历史会保留生成讲解、题目、答案和反馈，可能含原资料摘录或转述。删除知识库或聊天不会擦除学习历史；原文引用会失效。",
        { class: "retention" },
      ),
    );
    const form = el("form", "", { class: "form create-form" });
    const topic = field(form, "学习主题", "", {
      required: "",
      maxlength: "200",
      placeholder: "例如 Redis",
    });
    const goal = field(form, "具体目标", "", {
      tag: "textarea",
      required: "",
      maxlength: "1500",
      placeholder: "例如理解持久化并完成恢复演练",
    });
    const foundation = field(form, "已有基础（没有基础也请明确填写）", "", {
      tag: "textarea",
      required: "",
      maxlength: "1500",
      placeholder: "例如会 Java，了解基本数据库操作",
    });
    const days = field(form, "总天数（与目标日期二选一）", "", {
      type: "number",
      min: "1",
      max: "365",
      placeholder: "14",
    });
    const target = field(form, "目标日期（按下方时区，含当天）", "", {
      type: "date",
    });
    const minutes = field(form, "每天可用分钟数", "", {
      type: "number",
      required: "",
      min: "5",
      max: "480",
      placeholder: "30",
    });
    const timezone = field(form, "时区", "Asia/Shanghai", { required: "" });
    const constraints = field(form, "其他约束（每行一项，可留空）", "", {
      tag: "textarea",
      maxlength: "2000",
    });
    const choices = el("fieldset");
    choices.append(
      el("legend", "选择知识库"),
      el("p", "请先在知识库页面完成资料索引；任何未就绪资料都会阻止检索。"),
    );
    const checks = [];
    form.append(choices);
    const submit = el("button", "生成计划", {
      type: "submit",
      class: "primary",
    });
    submit.disabled = true;
    form.append(submit);
    main.append(form);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(async () => {
        if (!!days.value === !!target.value)
          throw Error("请填写总天数或目标日期，两者只填一项。");
        const bases = checks.filter((c) => c.checked).map((c) => c.value);
        if (!bases.length) throw Error("请明确选择知识库。");
        const input = {
          topic: topic.value,
          goal: goal.value,
          foundation: foundation.value,
          daily_minutes: Number(minutes.value),
          timezone: timezone.value,
          knowledge_base_ids: bases,
          constraints: constraints.value
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
        };
        if (days.value) input.days = Number(days.value);
        else input.target_date = target.value;
        const result = await mutation("create", { input });
        if (valid(null, turn)) await openPlan(result.plan.plan_id);
        await refreshPlans();
      }, submit);
    });
    const result = await call("bases");
    if (!valid(null, turn)) return;
    for (const base of result.knowledge_bases) {
      const label = el("label", base.name),
        checkbox = el("input", "", {
          type: "checkbox",
          value: base.knowledge_base_id,
        });
      label.prepend(checkbox);
      choices.append(label);
      checks.push(checkbox);
    }
    submit.disabled = false;
  }
  async function openPlan(id) {
    selected = id;
    const turn = ++sequence;
    loading(main, "正在读取学习计划");
    const result = await call("get", { plan_id: id });
    if (!valid(id, turn)) return;
    renderPlan(result.plan);
    await refreshPlans();
    return valid(id, turn) ? turn : null;
  }
  function renderPlan(plan) {
    main.dataset.planStatus = plan.status;
    const turn = sequence,
      id = plan.plan_id;
    const action = async (name, extra = {}) => {
      await mutation(name, {
        plan_id: id,
        expected_revision: plan.revision,
        ...extra,
      });
      if (valid(id, turn)) await openPlan(id);
    };
    const planHeading = el("div", "", { class: "plan-heading" });
    const title = el("div");
    title.append(
      el("p", "当前学习计划", { class: "eyebrow" }),
      el("h2", plan.input.topic),
      el("p", plan.input.goal, { class: "plan-goal" }),
    );
    planHeading.append(
      title,
      el("span", plan.status === "paused" ? "已暂停" : "学习中", {
        class: `status-label ${plan.status}`,
      }),
    );
    main.replaceChildren(planHeading);
    const background = el("details", "", { class: "background-info" });
    background.append(
      el("summary", "已有基础"),
      el("p", `基础：${plan.input.foundation}`),
    );
    const tools = el("div", "", { class: "actions" });
    tools.append(
      button("刷新进度", () => openPlan(id)),
      button("编辑计划", () => editForm(plan)),
      button(plan.status === "paused" ? "继续计划" : "暂停计划", () =>
        action(plan.status === "paused" ? "resume" : "pause"),
      ),
    );
    const completed = plan.chapters.filter(
      (chapter) => plan.progress[chapter.chapter_id].status === "completed",
    ).length;
    const overview = el("dl", "", {
      class: "plan-overview",
      "aria-label": "学习进度与时间安排",
    });
    for (const [label, value, note] of [
      [
        "章节进度",
        `${completed} / ${plan.chapters.length}`,
        "你已标记完成的章节",
      ],
      ["每天学习", `${plan.input.daily_minutes} 分钟`, "包含讲解与练习"],
      [
        "时间安排",
        `${plan.budget.scheduled_days} / ${plan.input.days} 天`,
        "已安排 / 计划总天数",
      ],
    ]) {
      const item = el("div");
      item.append(el("dt", label), el("dd", value), el("p", note));
      overview.append(item);
    }
    main.append(
      tools,
      overview,
      background,
      el(
        "p",
        `预计 ${plan.budget.estimated_minutes} / 可用 ${plan.budget.available_minutes} 分钟，安排 ${plan.budget.scheduled_days} 天，每天 ${plan.input.daily_minutes} 分钟。`,
        { class: "budget-description muted" },
      ),
    );
    if (plan.budget.over_budget)
      main.append(
        el("p", "安排超出期限，请减少章节或调整预计时长。", {
          class: "warning",
        }),
      );
    main.append(el("p", plan.coverage_notice, { class: "muted" }));
    if (plan.gaps.length)
      main.append(
        el("p", `资料缺口：${plan.gaps.join("；")}`, { class: "warning" }),
      );
    if (plan.possibly_outdated)
      main.append(
        el(
          "p",
          "计划依据已变化。历史进度保留，开始章节时会重新检索；请检查失效来源。",
          { class: "warning" },
        ),
      );
    const chapters = el("ol", "", { class: "chapters" });
    const routeHeading = el("div", "", { class: "section-heading" });
    routeHeading.append(
      el("h3", "章节路线"),
      el("span", `${plan.chapters.length} 个章节`, { class: "muted" }),
    );
    main.append(routeHeading);
    const nextChapter =
      plan.chapters.find(
        (chapter) =>
          plan.progress[chapter.chapter_id].status === "in_progress" &&
          !plan.progress[chapter.chapter_id].needs_confirmation,
      ) ||
      plan.chapters.find(
        (chapter) =>
          plan.progress[chapter.chapter_id].status === "pending" &&
          !plan.progress[chapter.chapter_id].needs_confirmation,
      );
    let chapterIndex = 0;
    for (const chapter of plan.chapters) {
      const state = plan.progress[chapter.chapter_id],
        row = el("li", "", {
          "data-status": state.status,
          "data-confirmation": String(Boolean(state.needs_confirmation)),
        });
      row.append(
        el("span", String(++chapterIndex).padStart(2, "0"), {
          class: "chapter-number",
          "aria-hidden": "true",
        }),
      );
      const chapterHeading = el("div", "", { class: "chapter-heading" });
      chapterHeading.append(
        el("h3", chapter.title),
        el(
          "span",
          state.needs_confirmation
            ? "待核对"
            : state.status === "completed"
              ? "用户已完成"
              : state.status === "in_progress"
                ? "进行中"
                : "待开始",
          { class: "chapter-state" },
        ),
      );
      row.append(
        chapterHeading,
        el(
          "p",
          `第 ${chapter.day} 天 · ${chapter.minutes} 分钟 · ${state.status === "completed" ? "用户已标记完成" : state.status === "in_progress" ? "进行中" : "待开始"}`,
          { class: "chapter-meta" },
        ),
        el("p", chapter.objective, { class: "chapter-objective" }),
      );
      row.append(
        el(
          "p",
          `知识点：${chapter.knowledge_points.join("、")}；${chapter.supplemental ? "建议补充（当前未覆盖）" : "有检索资料支持"}`,
          { class: "chapter-knowledge muted" },
        ),
      );
      sourceLinks(row, plan.sources, chapter.citation_ids);
      const controls = el("div", "", { class: "actions" });
      const start = button(
        "开始章节",
        async () => {
          const result = await mutation("start", {
            plan_id: id,
            chapter_id: chapter.chapter_id,
          });
          if (!valid(id, turn)) return;
          const fresh = await openPlan(id);
          if (fresh != null && valid(id, fresh))
            renderLesson(result.lesson, id);
        },
        {
          class:
            chapter.chapter_id === nextChapter?.chapter_id ? "primary" : "",
        },
      );
      start.disabled = plan.status === "paused" || state.needs_confirmation;
      controls.append(start);
      if (state.latest_lesson_id)
        controls.append(
          button("查看上次讲解", () => showLesson(id, state.latest_lesson_id)),
        );
      if (state.needs_confirmation) {
        row.append(
          el(
            "p",
            "该章节内容有实质变化，旧完成记录保留。请确认新内容后再开始。",
            { class: "warning" },
          ),
        );
        const change = (plan.changes || []).find(
          (c) => c.chapter_id === chapter.chapter_id,
        );
        if (change) {
          for (const [key, label] of [
            ["title", "名称"],
            ["objective", "目标"],
            ["knowledge_points", "知识点"],
          ]) {
            if (
              JSON.stringify(change.before[key]) !==
              JSON.stringify(change.after[key])
            ) {
              const display = (value) =>
                Array.isArray(value) ? value.join("、") : value;
              row.append(
                el(
                  "p",
                  `原${label}：${display(change.before[key])}\n新${label}：${display(change.after[key])}`,
                ),
              );
            }
          }
          if (change.before.supplemental !== change.after.supplemental)
            row.append(
              el(
                "p",
                `资料支持：${change.before.supplemental ? "建议补充" : "有引用"} → ${change.after.supplemental ? "建议补充" : "有引用"}`,
              ),
            );
          if (
            JSON.stringify(change.before.citation_ids) !==
            JSON.stringify(change.after.citation_ids)
          )
            row.append(
              el(
                "p",
                "资料关联已变化，请逐条核对新引用；旧讲解保留在学习记录中。",
              ),
            );
        }
        controls.append(
          button("我已核对章节变化", () =>
            action("confirm_change", { chapter_id: chapter.chapter_id }),
          ),
        );
      }
      if (
        state.status === "in_progress" &&
        !state.needs_confirmation &&
        plan.status === "active"
      )
        controls.append(
          button("我已完成本章节", () =>
            action("complete", { chapter_id: chapter.chapter_id }),
          ),
        );
      row.append(controls);
      chapters.append(row);
    }
    main.append(
      chapters,
      el("p", "完成标记表示你确认学完，不代表系统判定已经掌握。", {
        class: "muted",
      }),
    );
    if (plan.archived_chapters.length)
      main.append(
        el(
          "p",
          `已移出的章节（历史仍保留）：${plan.archived_chapters.map((c) => c.title).join("、")}`,
        ),
      );
    main.append(
      button("查看学习记录", () => history(id)),
      el("p", plan.retention_notice, { class: "retention" }),
    );
  }
  function editForm(plan) {
    ++sequence;
    main.replaceChildren(
      viewHeading(
        "编辑章节",
        "可以调整顺序、时间和内容。历史讲解与答题仍保留；进行中或已完成内容发生实质变化时需要重新核对。",
      ),
    );
    const form = el("form", "", { class: "edit-form" }),
      rows = el("div", "", { class: "edit-chapters" }),
      drafts = plan.chapters.map(({ day, ...chapter }) => ({ ...chapter }));
    const days = field(form, "总天数（从原计划起始日计算）", plan.input.days, {
      type: "number",
      min: "1",
      max: "365",
      required: "",
    });
    const minutes = field(form, "每天可用分钟数", plan.input.daily_minutes, {
      type: "number",
      min: "5",
      max: "480",
      required: "",
    });
    const draw = () => {
      rows.replaceChildren();
      drafts.forEach((c, index) => {
        const row = el("fieldset");
        row.append(el("legend", `章节 ${index + 1}`));
        const bind = (label, key, opts = {}) => {
          const node = field(
            row,
            label,
            Array.isArray(c[key]) ? c[key].join("\n") : c[key],
            opts,
          );
          node.addEventListener(
            "input",
            () =>
              (c[key] =
                key === "minutes"
                  ? Number(node.value)
                  : key === "knowledge_points"
                    ? node.value
                        .split("\n")
                        .map((s) => s.trim())
                        .filter(Boolean)
                    : node.value),
          );
        };
        bind("名称", "title", { required: "", maxlength: "200" });
        bind("目标", "objective", {
          tag: "textarea",
          required: "",
          maxlength: "1500",
        });
        bind("知识点（每行一项）", "knowledge_points", {
          tag: "textarea",
          required: "",
        });
        bind("预计分钟（含练习）", "minutes", {
          type: "number",
          min: "1",
          max: "480",
          required: "",
        });
        row.append(
          el(
            "p",
            c.supplemental
              ? "建议补充：开始时重新寻找证据。"
              : "保留原资料关联，讲解会重新检索。",
          ),
        );
        row.append(
          button("上移", () => {
            if (index > 0)
              [drafts[index - 1], drafts[index]] = [
                drafts[index],
                drafts[index - 1],
              ];
            draw();
          }),
          button("下移", () => {
            if (index < drafts.length - 1)
              [drafts[index], drafts[index + 1]] = [
                drafts[index + 1],
                drafts[index],
              ];
            draw();
          }),
          button("移出计划", () => {
            drafts.splice(index, 1);
            draw();
          }),
        );
        rows.append(row);
      });
    };
    draw();
    const turn = sequence;
    form.append(
      rows,
      button("添加补充章节", () => {
        drafts.push({
          chapter_id: null,
          title: "",
          objective: "",
          knowledge_points: [],
          minutes: plan.input.daily_minutes,
          citation_ids: [],
          supplemental: true,
        });
        draw();
      }),
    );
    const save = el("button", "保存修订", { type: "submit", class: "primary" });
    form.append(
      save,
      button("取消", () => openPlan(plan.plan_id)),
    );
    main.append(form);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(async () => {
        await mutation("edit", {
          plan_id: plan.plan_id,
          expected_revision: plan.revision,
          chapters: drafts,
          time_budget: {
            days: Number(days.value),
            daily_minutes: Number(minutes.value),
          },
        });
        if (valid(plan.plan_id, turn)) await openPlan(plan.plan_id);
      }, save);
    });
  }
  async function refreshAfterAnswer(id, turn) {
    const request = ++planRefreshSequence;
    try {
      const fresh = await call("get", { plan_id: id });
      if (!valid(id, turn) || request !== planRefreshSequence) return;
      // Refresh the full plan, rather than advancing a stale edit's revision.
      // Preserve live quiz nodes so another answer draft is not discarded.
      const lesson = main.querySelector(".lesson"),
        focus = document.activeElement;
      renderPlan(fresh.plan);
      if (lesson) main.append(lesson);
      if (lesson?.contains(focus)) focus.focus({ preventScroll: true });
      await refreshPlans();
    } catch {
      if (valid(id, turn)) {
        notice.textContent =
          "答题已保存，进度刷新失败；请刷新进度后再调整或暂停计划。";
        notice.hidden = false;
      }
    }
  }
  async function showLesson(id, lessonId) {
    const turn = sequence,
      request = ++lessonSequence;
    const result = await call("lesson", { plan_id: id, lesson_id: lessonId });
    if (valid(id, turn) && lessonSequence === request)
      renderLesson(result.lesson, id);
  }
  function renderLesson(lesson, id) {
    lessonSequence++;
    const turn = sequence;
    main.querySelector(".lesson")?.remove();
    const section = el("section", "", { class: "lesson" });
    section.append(
      el("h2", `章节讲解：${lesson.chapter_snapshot.title}`),
      el(
        "p",
        `生成于 ${new Date(lesson.generated * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（Asia/Shanghai），依据计划修订 ${lesson.plan_revision}`,
      ),
    );
    if (lesson.possibly_outdated)
      section.append(
        el(
          "p",
          "来源已更新或不可用。这是历史生成文本，请核对；旧原文无法通过引用读取。",
          { class: "warning" },
        ),
      );
    const names = {
      goal: "本课目标",
      concept: "核心概念",
      example: "例子",
      check: "简短检查",
    };
    for (const item of lesson.sections) {
      section.append(el("h3", names[item.kind]), el("p", item.text));
      sourceLinks(section, lesson.sources, item.citation_ids);
    }
    const questionForm = el("form", "", { class: "followup-form" });
    const question = field(questionForm, "没看懂？描述希望补充的地方", "", {
      tag: "textarea",
      required: "",
      maxlength: "1000",
    });
    const explain = el("button", "根据资料补充讲解", {
      type: "submit",
      class: "primary",
    });
    questionForm.append(explain);
    section.append(questionForm);
    questionForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void run(async () => {
        const result = await mutation("explain", {
          plan_id: id,
          chapter_id: lesson.chapter_id,
          question: question.value,
        });
        if (valid(id, turn)) {
          const fresh = await openPlan(id);
          if (fresh != null && valid(id, fresh))
            renderLesson(result.lesson, id);
        }
      }, explain);
    });
    section.append(
      el("h3", "练习"),
      el("p", "请亲自作答。简答反馈是参考评价，提交后不会自动标记完成。"),
    );
    for (const exercise of lesson.exercises) {
      const form = el("form", "", { class: "exercise" });
      form.append(
        el("h4", exercise.question),
        el("p", `知识点：${exercise.knowledge_points.join("、")}`),
      );
      let answer;
      if (exercise.kind === "objective") {
        answer = field(form, "选择答案", "", { tag: "select", required: "" });
        answer.append(el("option", "请选择", { value: "" }));
        for (const option of exercise.options)
          answer.append(el("option", option, { value: option }));
      } else
        answer = field(form, "你的答案", "", {
          tag: "textarea",
          required: "",
          maxlength: "2000",
        });
      const submit = el("button", "提交答案", {
          type: "submit",
          class: "primary",
        }),
        feedback = el("div", "", { role: "status" });
      if (lesson.possibly_outdated || main.dataset.planStatus === "paused") {
        answer.disabled = true;
        submit.disabled = true;
        feedback.append(
          el(
            "p",
            lesson.possibly_outdated
              ? "该题来源已变化，请重新开始章节取得当前资料的练习。"
              : "计划已暂停，请继续计划后再作答。",
          ),
        );
      }
      form.append(submit, feedback);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        void run(async () => {
          const result = await mutation("submit", {
            plan_id: id,
            lesson_id: lesson.lesson_id,
            exercise_id: exercise.exercise_id,
            answer: answer.value,
          });
          if (!valid(id, turn)) return;
          feedback.replaceChildren(
            el(
              "p",
              `${result.attempt.reference_evaluation ? "参考评价" : result.attempt.score === 1 ? "正确" : "需要再检查"}：${result.attempt.feedback}`,
            ),
          );
          if (result.attempt.evaluation_notice)
            feedback.append(
              el("p", result.attempt.evaluation_notice, { class: "retention" }),
            );
          sourceLinks(feedback, result.attempt.sources);
          answer.disabled = true;
          submit.hidden = true;
          await refreshAfterAnswer(id, turn);
        }, submit);
      });
      section.append(form);
    }
    section.append(el("p", lesson.retention_notice, { class: "retention" }));
    main.append(section);
    section.scrollIntoView?.({ block: "start" });
  }
  async function history(id, kind = "lessons", offset = 0) {
    const turn = sequence,
      request = ++historySequence;
    const result = await call("history", { plan_id: id, kind, offset });
    if (!valid(id, turn) || historySequence !== request) return;
    main.querySelector(".history")?.remove();
    const box = el("section", "", { class: "history" });
    const tabs = el("div", "", {
      class: "actions history-tabs",
      "aria-label": "记录类型",
    });
    tabs.append(
      button("讲解记录", () => history(id, "lessons"), {
        "aria-pressed": String(kind === "lessons"),
      }),
      button("答题记录", () => history(id, "attempts"), {
        "aria-pressed": String(kind === "attempts"),
      }),
    );
    box.append(el("h2", "学习记录"), tabs);
    const records = el("div", "", { class: "history-records" });
    if (!result.records.length) box.append(el("p", "当前没有记录。"));
    for (const record of result.records) {
      const when = new Date(
        (record.generated || record.created) * 1000,
      ).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
      records.append(
        button(
          `${when} · ${kind === "lessons" ? "查看讲解" : "查看答题与反馈"}`,
          async () => {
            if (kind === "lessons") return showLesson(id, record.lesson_id);
            const detail = await call("attempt", {
              plan_id: id,
              attempt_id: record.attempt_id,
            });
            if (!valid(id, turn)) return;
            box.querySelector(".attempt-detail")?.remove();
            const node = el("div", "", { class: "attempt-detail" });
            node.append(
              el("p", `你的答案：${detail.attempt.answer}`),
              el(
                "p",
                `${detail.attempt.reference_evaluation ? "参考评价" : "判分反馈"}：${detail.attempt.feedback}`,
              ),
            );
            if (detail.attempt.evaluation_notice)
              node.append(
                el("p", detail.attempt.evaluation_notice, {
                  class: "retention",
                }),
              );
            sourceLinks(node, detail.attempt.sources);
            box.append(node);
          },
        ),
      );
    }
    box.append(records);
    const pages = el("div", "", { class: "actions history-pagination" });
    if (offset > 0)
      pages.append(
        button("上一页", () => history(id, kind, Math.max(0, offset - 20))),
      );
    if (result.next_offset != null)
      pages.append(
        button("下一页", () => history(id, kind, result.next_offset)),
      );
    box.append(pages);
    main.append(box);
    box.scrollIntoView?.({ block: "start" });
  }
  const intro = el("div", "", { class: "intro-text" });
  intro.append(
    el("p", "沿着资料，按自己的节奏学习。", { class: "intro-title" }),
    el("p", "把目标拆成章节，在讲解与练习中稳步前进。", { class: "muted" }),
  );
  const headerActions = el("div", "", { class: "actions" });
  headerActions.append(
    button(
      "重新加载",
      async () => {
        await refreshPlans();
        if (selected) await openPlan(selected);
      },
      { class: "quiet" },
    ),
    button("创建计划", createForm, { class: "primary" }),
  );
  header.append(intro, headerActions);
  const welcome = el("div", "", { class: "welcome" });
  welcome.append(
    el("span", "学习", { class: "welcome-mark", "aria-hidden": "true" }),
    el("h2", "从目标开始"),
    el("p", "选择已有计划继续学习，或使用上方的创建计划开始。", {
      class: "muted",
    }),
  );
  main.append(welcome);
  layout.append(sidebar, main);
  root.append(header, notice, layout);
  surface.append(css, root);
  loading(sidebar, "正在加载学习计划");
  void run(refreshPlans);
  return {
    dispose() {
      active = false;
      sequence++;
      listSequence++;
      requests.clear();
      root.remove();
      css.remove();
    },
  };
}
export default {
  apiVersion: 1,
  module: "learning.v1",
  icon: "book-open",
  surfaces: [
    {
      id: "study",
      slot: "page",
      title: "学习计划与辅导",
      navigation: { label: "Learning", labelZh: "学习计划", icon: "book-open" },
      mount,
    },
  ],
};
