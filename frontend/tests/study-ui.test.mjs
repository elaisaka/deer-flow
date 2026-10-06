// Synthetic DOM, no real user's answers or confirmations.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { test } from "node:test";
const { Window } = createRequire(resolve("package.json"))("happy-dom");
const { mountStudy } =
  await import("../../backend/knowledge_base_extension/static/study.mjs");
const tick = () => new Promise((r) => setTimeout(r, 20));
const note = {
  note_id: "note-a",
  title: "合成笔记",
  body: "保存的正文",
  revision: 1,
  status: "draft",
  source_type: "user_note",
  sources: [],
  index_status: "unindexed",
  document_status: "not_imported",
};
async function fixture(overrides = {}) {
  const window = new Window({ url: "http://localhost:2026" });
  globalThis.document = window.document;
  const calls = [],
    surface = document.createElement("div");
  document.body.append(surface);
  const responses = {
    notes_list: () => ({ notes: [note] }),
    notes_get: () => ({ note }),
    bases: () => ({
      knowledge_bases: [{ knowledge_base_id: "base-a", name: "合成库" }],
    }),
    reviews_list: () => ({ reviews: [] }),
    mistakes_list: () => ({ mistakes: [] }),
    ...overrides,
  };
  const controller = mountStudy(surface, {
    async callBackend(action, payload) {
      calls.push({ action, payload });
      return { ok: true, ...(await responses[action](payload)) };
    },
  });
  const click = async (text) => {
    const b = [...surface.querySelectorAll("button")].find(
      (n) => n.textContent === text,
    );
    assert.ok(b, text);
    b.click();
    await tick();
  };
  return {
    window,
    surface,
    calls,
    controller,
    click,
    dispose() {
      controller.dispose();
      window.happyDOM.abort();
    },
  };
}
test("unsaved note stays across tabs and failed retry keeps idempotency key", async () => {
  const f = await fixture({
    notes_edit: () => ({
      ok: false,
      error: { code: "study_operation_failed" },
    }),
  });
  try {
    await f.controller.show("notes");
    await f.click("合成笔记");
    const body = f.surface.querySelector('textarea[aria-label="笔记正文"]');
    body.value = "未保存的理解";
    body.dispatchEvent(new f.window.Event("input"));
    await f.click("保存草稿");
    await f.click("保存草稿");
    assert.equal(
      f.calls.filter((c) => c.action === "notes_edit")[0].payload.request_id,
      f.calls.filter((c) => c.action === "notes_edit")[1].payload.request_id,
    );
    await f.controller.show("reviews");
    await f.controller.show("notes");
    assert.equal(
      f.surface.querySelector('textarea[aria-label="笔记正文"]').value,
      "未保存的理解",
    );
    assert.equal(f.calls.filter((c) => c.action === "notes_approve").length, 0);
  } finally {
    f.dispose();
  }
});
test("late response cannot replace another tab; disposal rejects late paint", async () => {
  let resolveNotes;
  const f = await fixture({
    notes_list: () =>
      new Promise((r) => {
        resolveNotes = r;
      }),
  });
  try {
    const pending = f.controller.show("notes");
    await tick();
    await f.controller.show("reviews");
    resolveNotes({ notes: [note] });
    await pending;
    assert.ok(f.surface.textContent.includes("应用内复习"));
    assert.ok(!f.surface.textContent.includes("合成笔记"));
    f.controller.dispose();
    assert.equal(f.surface.children.length, 0);
  } finally {
    f.dispose();
  }
});
test("selected review starts without auto-answer or self-assessment", async () => {
  const review = {
    review_id: "review-a",
    title: "RDB",
    due_date: "2026-10-05",
    due_status: "today",
    status: "active",
    revision: 1,
    timezone: "Asia/Shanghai",
    history: [],
  };
  const f = await fixture({
    reviews_list: () => ({ reviews: [review] }),
    reviews_get: () => ({ review }),
    reviews_start: () => ({ review, note }),
  });
  try {
    await f.controller.show("reviews");
    await f.click("RDB · 今日 · 2026-10-05");
    assert.ok(f.surface.textContent.includes("保存的正文"));
    assert.equal(
      f.calls.filter((c) =>
        ["reviews_submit", "reviews_finish"].includes(c.action),
      ).length,
      0,
    );
  } finally {
    f.dispose();
  }
});

test("note self-assessment requires an explicit choice and retries the same result request", async () => {
  const review = {
    review_id: "review-note",
    title: "笔记自评",
    due_date: "2026-10-05",
    due_status: "today",
    status: "active",
    revision: 4,
    timezone: "Asia/Shanghai",
    intervals: [1, 3, 7, 14],
    history: [],
  };
  let tries = 0;
  const f = await fixture({
    reviews_list: () => ({ reviews: [review] }),
    reviews_get: () => ({ review }),
    reviews_start: () => ({ review, note: { ...note, revision: 2 } }),
    reviews_finish: () =>
      ++tries === 1
        ? { ok: false, error: { code: "study_operation_failed" } }
        : { review: { ...review, revision: 5, due_date: "2026-10-08" } },
  });
  try {
    await f.controller.show("reviews");
    await f.click("笔记自评 · 今日 · 2026-10-05");
    await f.click("确认本次复习结果");
    assert.equal(
      f.calls.filter((c) => c.action === "reviews_finish").length,
      0,
    );
    const choice = f.surface.querySelector(
      'select[aria-label="本人确认的复习结果"]',
    );
    choice.value = "continue";
    await f.click("确认本次复习结果");
    assert.equal(choice.value, "continue");
    await f.click("确认本次复习结果");
    const calls = f.calls.filter((c) => c.action === "reviews_finish");
    assert.equal(calls.length, 2);
    assert.equal(calls[0].payload.request_id, calls[1].payload.request_id);
    assert.deepEqual(
      { ...calls[1].payload, request_id: undefined },
      {
        review_id: "review-note",
        expected_revision: 4,
        target_revision: 2,
        result: "continue",
        attempt_id: null,
        request_id: undefined,
      },
    );
    assert.ok(f.surface.textContent.includes("下次日期：2026-10-08"));
    assert.equal(
      f.calls.filter((c) => c.action === "reviews_submit").length,
      0,
    );
  } finally {
    f.dispose();
  }
});

test("short-answer retry sends only entered text and keeps the original feedback", async () => {
  const exercise = {
    exercise_id: "short-1",
    question: "解释 RDB",
    kind: "short_answer",
    knowledge_points: ["RDB"],
    options: [],
  };
  const m = {
    mistake_id: "mistake-short",
    revision: 2,
    status: "collected",
    classification: "needs_review",
    knowledge_points: ["RDB"],
    exercise,
    original_attempt: {
      answer: "原答题",
      feedback: "旧参考反馈",
      reference_evaluation: true,
      sources: [],
    },
    sources: [],
    retry_attempts: [],
  };
  const f = await fixture({
    mistakes_list: () => ({ mistakes: [m] }),
    mistakes_get: () => ({ mistake: m }),
    mistakes_start: () => ({ exercise, sources: [] }),
    mistakes_submit: () => ({
      attempt: {
        attempt_id: "new-attempt",
        reference_evaluation: true,
        feedback: "新参考反馈",
        sources: [],
      },
    }),
  });
  try {
    await f.controller.show("mistakes");
    await f.click("RDB · 建议复核");
    assert.ok(f.surface.textContent.includes("模型评价不等于判错"));
    await f.click("开始重练");
    assert.equal(
      f.calls.filter((c) => c.action === "mistakes_submit").length,
      0,
    );
    const answer = f.surface.querySelector('textarea[aria-label="本次答案"]');
    assert.equal(answer.value, "");
    answer.value = "合成测试输入：保存快照";
    answer.dispatchEvent(new f.window.Event("input"));
    f.surface
      .querySelector("form")
      .dispatchEvent(
        new f.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    const sent = f.calls.find((c) => c.action === "mistakes_submit");
    assert.equal(sent.payload.answer, "合成测试输入：保存快照");
    assert.equal(sent.payload.mistake_id, "mistake-short");
    assert.ok(
      f.surface.textContent.includes("旧参考反馈") &&
        f.surface.textContent.includes("新参考反馈"),
    );
    assert.ok(f.surface.textContent.includes("一次答对不等于已掌握"));
  } finally {
    f.dispose();
  }
});

test("preview binds actual revision and only explicit browser click imports; editing invalidates the button", async () => {
  const preview = {
    ...note,
    knowledge_base_id: "base-a",
    knowledge_base_name: "合成库",
    document_revision: null,
    digest: "synthetic-digest",
  };
  let savedNote = note;
  const f = await fixture({
    notes_preview: () => ({ preview }),
    notes_get: () => ({ note: savedNote }),
  });
  const originalFetch = globalThis.fetch,
    posts = [];
  globalThis.fetch = async (path, options) => {
    posts.push({ path, ...options });
    savedNote = {
      ...note,
      status: "imported",
      approved_revision: 1,
      document_id: "synthetic-document",
      document_status: "current",
    };
    return { ok: true, json: async () => ({ ok: true, note: savedNote }) };
  };
  try {
    document.cookie = "csrf_token=synthetic-token";
    await f.controller.show("notes");
    await f.click("合成笔记");
    f.surface.querySelector('select[aria-label="目标知识库"]').value = "base-a";
    await f.click("预览具体修订并确认入库");
    assert.ok(f.surface.textContent.includes("合成库 / 合成笔记"));
    assert.equal(posts.length, 0);
    await f.click("我已核对，确认此修订入库");
    assert.equal(posts.length, 1);
    assert.equal(posts[0].path, "/api/personal-learning/notes/approve");
    const payload = JSON.parse(posts[0].body);
    assert.equal(payload.expected_revision, 1);
    assert.equal(payload.digest, "synthetic-digest");
    assert.ok(!("confirmed" in payload));
    assert.equal(posts[0].headers["X-CSRF-Token"], "synthetic-token");
    const notice = f.surface.querySelector('[role="alert"]');
    assert.equal(notice.hidden, false);
    assert.ok(notice.textContent.includes("笔记已入库"));
    assert.ok(notice.textContent.includes("尚未建立索引"));
    assert.ok(f.surface.textContent.includes("文档 当前版本"));
    f.surface.querySelector('select[aria-label="目标知识库"]').value = "base-a";
    await f.click("预览具体修订并确认入库");
    const body = f.surface.querySelector('textarea[aria-label="笔记正文"]');
    body.value = "编辑新正文";
    body.dispatchEvent(new f.window.Event("input"));
    const approve = [...f.surface.querySelectorAll("button")].find(
      (b) => b.textContent === "我已核对，确认此修订入库",
    );
    assert.ok(approve.disabled);
    assert.equal(posts.length, 1);
    assert.equal(f.calls.filter((c) => c.action === "notes_approve").length, 0);
  } finally {
    globalThis.fetch = originalFetch;
    f.dispose();
  }
});

test("an edited imported note keeps its library and separates indexed history from its draft", async () => {
  const edited = {
    ...note,
    revision: 3,
    document_id: "synthetic-document",
    document_status: "current",
    index_status: "ready",
    knowledge_base_id: "base-a",
    knowledge_base_name: "合成库",
    published_note_revision: 1,
  };
  const f = await fixture({
    notes_list: () => ({ notes: [edited] }),
    notes_get: () => ({ note: edited }),
    bases: () => ({
      knowledge_bases: [
        { knowledge_base_id: "other-base", name: "其他库" },
        { knowledge_base_id: "base-a", name: "合成库" },
      ],
    }),
    notes_preview: () => ({
      preview: {
        ...edited,
        knowledge_base_id: "base-a",
        knowledge_base_name: "合成库",
        document_revision: 1,
        digest: "synthetic",
      },
    }),
  });
  try {
    await f.controller.show("notes");
    await f.click("合成笔记");
    const target = f.surface.querySelector('select[aria-label="目标知识库"]');
    assert.equal(target.value, "base-a");
    assert.equal(target.disabled, true);
    assert.ok(f.surface.textContent.includes("当前草稿修订 3 尚未入库"));
    assert.ok(f.surface.textContent.includes("已入库笔记修订 1"));
    const index = [...f.surface.querySelectorAll("button")].find(
      (b) => b.textContent === "建立或重试索引",
    );
    assert.equal(index.disabled, true);
    await f.click("预览具体修订并确认入库");
    assert.equal(
      f.calls.find((c) => c.action === "notes_preview").payload
        .knowledge_base_id,
      "base-a",
    );
    assert.equal(f.calls.filter((c) => c.action === "notes_index").length, 0);
  } finally {
    f.dispose();
  }
});

test("a late pause cannot repaint the newly selected review and paused objects resume explicitly", async () => {
  const r = (id, status = "active") => ({
    review_id: id,
    title: id,
    due_date: "2026-10-05",
    due_status: status === "paused" ? "paused" : "today",
    status,
    revision: 1,
    timezone: "Asia/Shanghai",
    history: [],
  });
  let release;
  const f = await fixture({
    reviews_list: () => ({ reviews: [r("r-a"), r("r-b"), r("r-c", "paused")] }),
    reviews_get: ({ review_id }) => ({
      review: r(review_id, review_id === "r-c" ? "paused" : "active"),
    }),
    reviews_start: ({ review_id }) => ({ review: r(review_id), note }),
    reviews_pause: () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  });
  try {
    await f.controller.show("reviews");
    await f.click("r-a · 今日 · 2026-10-05");
    const pause = [...f.surface.querySelectorAll("button")].find(
      (b) => b.textContent === "暂停此复习对象",
    );
    pause.click();
    await tick();
    await f.click("r-b · 今日 · 2026-10-05");
    release({ review: r("r-a", "paused") });
    await tick();
    assert.ok(
      !f.surface.textContent.includes("复习已暂停；到期日期和历史保留。"),
    );
    await f.click("r-c · 暂停 · 2026-10-05");
    assert.ok(f.surface.textContent.includes("恢复此复习对象"));
    assert.equal(
      f.calls.filter((c) => c.action === "reviews_resume").length,
      0,
    );
  } finally {
    f.dispose();
  }
});
