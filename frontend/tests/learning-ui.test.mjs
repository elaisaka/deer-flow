// Synthetic DOM actions; never operates a user's real learning page.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { test } from "node:test";

const require = createRequire(resolve("package.json"));
const { Window } = require("happy-dom");
const { default: extension } =
  await import("../../backend/knowledge_base_extension/static/learning.mjs");
const tick = () => new Promise((resolve) => setTimeout(resolve, 15));
const plan = (id = "plan-a") => ({
  plan_id: id,
  revision: 2,
  status: "active",
  input: {
    topic: id,
    goal: "学习 RDB",
    foundation: "Java",
    days: 14,
    daily_minutes: 30,
  },
  budget: { estimated_minutes: 20, available_minutes: 420, scheduled_days: 1 },
  coverage_notice: "检索覆盖",
  gaps: [],
  chapters: [
    {
      chapter_id: "chapter-a",
      title: "RDB",
      objective: "理解快照",
      minutes: 20,
      day: 1,
      knowledge_points: ["RDB"],
      citation_ids: ["citation-a"],
      supplemental: false,
    },
  ],
  progress: { "chapter-a": { status: "pending", needs_confirmation: false } },
  sources: [],
  archived_chapters: [],
  retention_notice: "历史保留",
});
const lesson = () => ({
  lesson_id: "lesson-a",
  chapter_id: "chapter-a",
  generated: 1791158400,
  plan_revision: 2,
  chapter_snapshot: { title: "RDB" },
  sections: [
    {
      kind: "concept",
      text: "<script>attack()</script>",
      citation_ids: ["citation-a"],
    },
  ],
  sources: [
    {
      citation_id: "citation-a",
      citation_url: "/api/personal-knowledge/citations/citation-a",
      status: "valid",
      document_name: "合成资料",
      version_id: "v1",
      location: { page: null },
    },
  ],
  exercises: [
    {
      exercise_id: "ex-a",
      question: "选择快照",
      knowledge_points: ["RDB"],
      kind: "objective",
      options: ["A", "B"],
    },
    {
      exercise_id: "ex-b",
      question: "说明 RDB",
      knowledge_points: ["RDB"],
      kind: "short_answer",
      options: [],
    },
  ],
  retention_notice: "历史保留",
});

async function fixture(overrides = {}) {
  const window = new Window({ url: "http://localhost:2026" });
  globalThis.document = window.document;
  const calls = [];
  const responses = {
    list: () => ({
      plans: [plan(), plan("plan-b")].map((p) => ({
        ...p,
        topic: p.input.topic,
      })),
    }),
    get: ({ plan_id }) => ({ plan: plan(plan_id) }),
    bases: () => ({
      knowledge_bases: [{ knowledge_base_id: "base-a", name: "Redis 合成" }],
    }),
    start: () => ({ lesson: lesson() }),
    lesson: () => ({ lesson: lesson() }),
    submit: () => ({
      attempt: {
        score: 1,
        reference_evaluation: false,
        feedback: "合成反馈",
        sources: [],
      },
    }),
    edit: () => ({ plan: plan() }),
    create: () => ({ plan: plan() }),
    ...overrides,
  };
  const surface = document.createElement("div");
  document.body.append(surface);
  const controller = extension.surfaces[0].mount(surface, {
    async callBackend(action, payload) {
      calls.push({ action, payload });
      return { ok: true, ...(await responses[action](payload)) };
    },
  });
  await tick();
  const click = async (text) => {
    const node = [...surface.querySelectorAll("button")].find(
      (b) => b.textContent === text,
    );
    assert.ok(node, text);
    node.click();
    await tick();
  };
  return {
    window,
    surface,
    controller,
    calls,
    click,
    dispose() {
      controller.dispose();
      window.happyDOM.abort();
    },
  };
}

test("start, literal teaching and human answer submit never auto-complete", async () => {
  const f = await fixture();
  try {
    await f.click("plan-a · 学习中");
    await f.click("开始章节");
    assert.ok(f.surface.textContent.includes("<script>attack()</script>"));
    assert.equal(f.surface.querySelector("script"), null);
    assert.equal(
      f.surface.querySelector(".source a").getAttribute("href"),
      "/api/personal-knowledge/citations/citation-a",
    );
    assert.equal(f.calls.filter((c) => c.action === "submit").length, 0);
    const form = f.surface.querySelector(".exercise"),
      answer = form.querySelector("select");
    answer.value = "B";
    form.dispatchEvent(
      new f.window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    const call = f.calls.find((c) => c.action === "submit");
    assert.equal(call.payload.answer, "B");
    assert.match(call.payload.request_id, /^[a-f0-9]{32}$/);
    assert.ok(form.textContent.includes("合成反馈"));
    assert.equal(f.calls.filter((c) => c.action === "complete").length, 0);
  } finally {
    f.dispose();
  }
});

test("answer updates displayed revision before pause and preserves other draft answers", async () => {
  let revision = 2;
  const f = await fixture({
    get: () => ({ plan: { ...plan(), revision } }),
    submit: () => ({
      revision: ++revision,
      attempt: { score: 1, feedback: "已保存", sources: [] },
    }),
    pause: () => ({
      plan: { ...plan(), revision: ++revision, status: "paused" },
    }),
  });
  try {
    await f.click("plan-a · 学习中");
    await f.click("开始章节");
    const forms = f.surface.querySelectorAll(".exercise");
    const draft = forms[1].querySelector("textarea");
    draft.value = "另一题尚未提交的草稿";
    forms[0].querySelector("select").value = "A";
    forms[0].dispatchEvent(
      new f.window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(draft.value, "另一题尚未提交的草稿");
    assert.ok(f.surface.contains(draft));
    await f.click("暂停计划");
    assert.equal(
      f.calls.find((c) => c.action === "pause").payload.expected_revision,
      3,
    );
  } finally {
    f.dispose();
  }
});

test("failed submit retry keeps the same idempotency key and answer", async () => {
  let tries = 0;
  const f = await fixture({
    submit: () =>
      ++tries === 1
        ? { ok: false, error: { code: "temporary_failure" } }
        : {
            attempt: {
              score: null,
              reference_evaluation: true,
              feedback: "参考反馈",
              evaluation_notice: "旧版参考评价可能误判，请复核。",
              sources: [],
            },
          },
  });
  try {
    await f.click("plan-a · 学习中");
    await f.click("开始章节");
    const form = f.surface.querySelectorAll(".exercise")[1];
    form.querySelector("textarea").value = "合成用户显式答案";
    const submit = () =>
      form.dispatchEvent(
        new f.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    submit();
    await tick();
    submit();
    await tick();
    const calls = f.calls.filter((c) => c.action === "submit");
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0].payload, calls[1].payload);
    assert.ok(form.textContent.includes("旧版参考评价可能误判，请复核。"));
  } finally {
    f.dispose();
  }
});

test("late lesson from another plan cannot overwrite current view; dispose removes UI", async () => {
  let finish;
  const f = await fixture({
    start: () => new Promise((resolve) => (finish = resolve)),
  });
  try {
    await f.click("plan-a · 学习中");
    await f.click("开始章节");
    await f.click("plan-b · 学习中");
    finish({ lesson: lesson() });
    await tick();
    assert.equal(f.surface.querySelector("main h2").textContent, "plan-b");
    assert.equal(f.surface.querySelector(".lesson"), null);
    f.controller.dispose();
    assert.equal(f.surface.children.length, 0);
  } finally {
    f.dispose();
  }
});

test("edit sends the displayed revision and public chapter fields only", async () => {
  const f = await fixture();
  try {
    await f.click("plan-a · 学习中");
    await f.click("编辑计划");
    const input = f.surface.querySelector("fieldset input");
    input.value = "新标题";
    input.dispatchEvent(new f.window.Event("input"));
    f.surface
      .querySelector("main form")
      .dispatchEvent(
        new f.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    const call = f.calls.find((c) => c.action === "edit");
    assert.equal(call.payload.expected_revision, 2);
    assert.equal(call.payload.chapters[0].title, "新标题");
    assert.equal("day" in call.payload.chapters[0], false);
  } finally {
    f.dispose();
  }
});

test("create requires explicit scope and preserves user foundation", async () => {
  const f = await fixture();
  try {
    await f.click("创建计划");
    assert.equal(
      f.surface.querySelector('.plan-button[aria-current="true"]'),
      null,
    );
    const form = f.surface.querySelector("main form"),
      inputs = form.querySelectorAll("input"),
      texts = form.querySelectorAll("textarea");
    inputs[0].value = "Redis";
    texts[0].value = "学习 RDB";
    texts[1].value = "用户填写的 Java 基础";
    inputs[1].value = "14";
    inputs[3].value = "30";
    const submit = () =>
      form.dispatchEvent(
        new f.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    submit();
    await tick();
    assert.equal(f.calls.filter((c) => c.action === "create").length, 0);
    form.querySelector("input[type=checkbox]").checked = true;
    submit();
    await tick();
    const call = f.calls.find((c) => c.action === "create");
    assert.equal(call.payload.input.foundation, "用户填写的 Java 基础");
    assert.equal(call.payload.input.days, 14);
    assert.deepEqual(call.payload.input.knowledge_base_ids, ["base-a"]);
  } finally {
    f.dispose();
  }
});

test("initial view opens the first plan, derives chapter progress and has one create entry", async () => {
  const p = plan();
  p.progress["chapter-a"].status = "completed";
  const f = await fixture({ get: () => ({ plan: p }) });
  try {
    assert.equal(f.surface.querySelector("main h2").textContent, "plan-a");
    assert.match(
      f.surface.querySelector(".plan-overview").textContent,
      /1 \/ 1/,
    );
    assert.equal(
      [...f.surface.querySelectorAll("button")].filter(
        (node) => node.textContent === "创建计划",
      ).length,
      1,
    );
    assert.equal(
      f.calls.filter((call) =>
        ["start", "complete", "create"].includes(call.action),
      ).length,
      0,
    );
  } finally {
    f.dispose();
  }
});

test("pause, changed-chapter confirmation and completion keep revision-bound explicit actions", async () => {
  const p = plan();
  p.progress["chapter-a"] = {
    status: "in_progress",
    needs_confirmation: true,
    latest_lesson_id: "lesson-a",
  };
  const f = await fixture({
    get: () => ({ plan: p }),
    confirm_change: () => {
      p.progress["chapter-a"].needs_confirmation = false;
      p.revision++;
      return {};
    },
    complete: () => {
      p.progress["chapter-a"].status = "completed";
      return {};
    },
    pause: () => {
      p.status = "paused";
      p.revision++;
      return {};
    },
    resume: () => {
      p.status = "active";
      p.revision++;
      return {};
    },
  });
  try {
    const start = () =>
      [...f.surface.querySelectorAll("button")].find(
        (node) => node.textContent === "开始章节",
      );
    assert.equal(start().disabled, true);
    await f.click("我已核对章节变化");
    assert.equal(
      f.calls.find((call) => call.action === "confirm_change").payload
        .expected_revision,
      2,
    );
    assert.equal(start().disabled, false);
    await f.click("暂停计划");
    assert.equal(start().disabled, true);
    await f.click("继续计划");
    await f.click("我已完成本章节");
    assert.equal(
      f.calls.find((call) => call.action === "complete").payload.chapter_id,
      "chapter-a",
    );
    assert.match(
      f.surface.querySelector(".plan-overview").textContent,
      /1 \/ 1/,
    );
  } finally {
    f.dispose();
  }
});

test("lesson follow-up, history tabs, pagination and answer feedback remain available", async () => {
  const f = await fixture({
    explain: () => ({ lesson: lesson() }),
    history: ({ kind, offset }) => ({
      records:
        kind === "lessons"
          ? [{ generated: 1791158400, lesson_id: "lesson-a" }]
          : [{ created: 1791158400, attempt_id: "attempt-a" }],
      next_offset: offset === 0 ? 20 : null,
    }),
    attempt: () => ({
      attempt: {
        answer: "合成历史答案",
        feedback: "合成历史反馈",
        reference_evaluation: true,
        sources: [],
      },
    }),
  });
  try {
    await f.click("开始章节");
    const questionForm = f.surface.querySelector(".lesson form");
    questionForm.querySelector("textarea").value = "请解释快照";
    questionForm.dispatchEvent(
      new f.window.Event("submit", { cancelable: true, bubbles: true }),
    );
    await tick();
    assert.equal(
      f.calls.find((call) => call.action === "explain").payload.question,
      "请解释快照",
    );
    await f.click("查看学习记录");
    await f.click("下一页");
    assert.ok(
      f.calls.some(
        (call) => call.action === "history" && call.payload.offset === 20,
      ),
    );
    await f.click("上一页");
    await f.click("答题记录");
    assert.equal(
      f.surface.querySelector('.history-tabs button[aria-pressed="true"]')
        .textContent,
      "答题记录",
    );
    const detail = [...f.surface.querySelectorAll(".history button")].find(
      (node) => node.textContent.includes("查看答题与反馈"),
    );
    detail.click();
    await tick();
    assert.match(f.surface.textContent, /合成历史答案/);
    assert.match(f.surface.textContent, /合成历史反馈/);
  } finally {
    f.dispose();
  }
});

test("empty and failed plan lists provide a visible recovery path without duplicate creation", async () => {
  const f = await fixture({ list: () => ({ plans: [] }) });
  try {
    assert.match(f.surface.textContent, /从目标开始/);
    assert.equal(
      [...f.surface.querySelectorAll("button")].filter(
        (node) => node.textContent === "创建计划",
      ).length,
      1,
    );
  } finally {
    f.dispose();
  }
  const failed = await fixture({
    list: () => {
      throw Error("合成加载失败");
    },
  });
  try {
    assert.match(
      failed.surface.querySelector('[role="alert"]').textContent,
      /合成加载失败/,
    );
    assert.ok(
      [...failed.surface.querySelectorAll("button")].some(
        (node) => node.textContent === "重新加载",
      ),
    );
    assert.equal(failed.surface.querySelector(".loading"), null);
  } finally {
    failed.dispose();
  }
});

test("a late initial plan list keeps the user's creation form and draft", async () => {
  let finishList;
  const pending = new Promise((resolve) => {
    finishList = resolve;
  });
  const f = await fixture({ list: () => pending });
  try {
    await f.click("创建计划");
    const topic = f.surface.querySelector(".create-form input");
    topic.value = "合成学习草稿";
    finishList({
      plans: [{ plan_id: "plan-a", topic: "plan-a", status: "active" }],
    });
    await tick();
    assert.equal(f.surface.querySelector(".create-form input"), topic);
    assert.equal(topic.value, "合成学习草稿");
    assert.equal(f.calls.filter((call) => call.action === "get").length, 0);
    assert.equal(
      f.surface.querySelector('.plan-button[aria-current="true"]'),
      null,
    );
  } finally {
    f.dispose();
  }
});
