// Standalone DOM regressions for the extension's unbundled browser module.
// Run from frontend/: node --test tests/knowledge-library-ui.test.mjs
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { test } from "node:test";

const require = createRequire(resolve("package.json"));
const { Window } = require("happy-dom");
const { default: extension } =
  await import("../../backend/knowledge_base_extension/static/index.mjs");
const tick = () => new Promise((resolve) => setTimeout(resolve, 15));

async function fixture(overrides = {}) {
  const window = new Window({ url: "http://localhost:2026" });
  globalThis.document = window.document;
  globalThis.FormData = window.FormData;
  const calls = [];
  const bases = [
    { knowledge_base_id: "base-a", name: "Redis" },
    { knowledge_base_id: "base-b", name: "第二个知识库" },
  ];
  const versions = [
    {
      id: "version-a",
      status: "ready",
      format: ".txt",
      size: 12,
      source: { type: "upload" },
      parser_version: "text-v1",
    },
  ];
  const doc = {
    document_id: "doc-a",
    knowledge_base_id: "base-a",
    name: "redis.txt",
    status: "ready",
    index_status: "ready",
    revision: 3,
    current_version_id: "version-a",
    versions,
  };
  const responses = {
    bases: () => ({ knowledge_bases: bases }),
    documents: ({ knowledge_base_id }) => ({
      documents: knowledge_base_id === "base-a" ? [doc] : [],
    }),
    document: () => doc,
    index_status: () => ({
      embedding_configured: true,
      answer_model_granted: true,
      documents: [
        { document_id: "doc-a", index_status: "ready", completed: 2, total: 2 },
      ],
    }),
    content: () => ({
      pages: [{ page: null, text: "<script>window.attacked = true</script>" }],
    }),
    create: ({ name }) => {
      bases.push({ knowledge_base_id: "base-c", name });
      return {};
    },
    rename: () => ({}),
    rename_document: () => ({}),
    index: () => ({ index_status: "ready", version_id: "version-a" }),
    delete_preview: () => ({
      name: "redis.txt",
      digest: "snapshot-digest",
      documents: [{ document_id: "doc-a" }],
    }),
    cleanup: () => ({ cleanup_pending: 0, index_cleanup: "done" }),
    local_roots: () => ({
      roots: [{ root_id: "study", actual_path: "E:/study" }],
    }),
    local_list: () => ({
      actual_path: "E:/study",
      entries: [
        { kind: "directory", name: "notes" },
        { kind: "file", name: "redis.txt" },
      ],
    }),
    import_local: () => ({
      status: "duplicate",
      document_id: "doc-a",
      version_id: "version-a",
    }),
    search: () => ({
      elapsed_ms: 12,
      evidence: [
        {
          document_name: "redis.txt",
          version_id: "version-a",
          chunk_id: "chunk-a",
          location: { page: null },
          score: 0.8,
          text: "synthetic evidence",
          citation_url: "/api/personal-knowledge/citations/citation-a",
        },
      ],
    }),
    ...overrides,
  };
  const surface = document.createElement("div");
  document.body.append(surface);
  const controller = extension.surfaces[0].mount(surface, {
    signal: new AbortController().signal,
    async callBackend(action, payload) {
      calls.push({ action, payload });
      const value = await responses[action](payload);
      return { ok: true, ...value };
    },
  });
  await tick();
  const button = (name) =>
    [...surface.querySelectorAll("button")].find(
      (node) => node.textContent.trim() === name,
    );
  const click = async (name) => {
    const node = button(name);
    assert.ok(node, `Missing button: ${name}`);
    node.click();
    await tick();
  };
  return {
    window,
    surface,
    calls,
    controller,
    click,
    button,
    cleanup() {
      controller.dispose();
      window.happyDOM.abort();
    },
  };
}

test("automatically selects a base and preserves document/index information", async () => {
  const f = await fixture();
  try {
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "documents" && payload.knowledge_base_id === "base-a",
      ),
    );
    assert.match(f.surface.textContent, /redis\.txt/);
    assert.match(f.surface.textContent, /解析.*就绪|解析.*ready/);
    assert.match(f.surface.textContent, /索引.*就绪|索引.*ready/);
    await f.click("第二个知识库");
    assert.match(f.surface.textContent, /还没有资料/);
  } finally {
    f.cleanup();
  }
});

test("create and base rename keep the existing backend payloads", async () => {
  const f = await fixture();
  try {
    f.surface.querySelector('[aria-label="知识库名称"]').value = "新的资料库";
    await f.click("创建知识库");
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "create" && payload.name === "新的资料库",
      ),
    );
    const rename = f.surface.querySelector('[aria-label="知识库新名称"]');
    rename.value = "Redis 新名称";
    await f.click("重命名");
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "rename" &&
          payload.knowledge_base_id === "base-a" &&
          payload.name === "Redis 新名称",
      ),
    );
  } finally {
    f.cleanup();
  }
});

test("detail retains version metadata, literal content, renaming and both indexing actions", async () => {
  const f = await fixture();
  try {
    f.surface.querySelector('[data-document-id="doc-a"]').click();
    await tick();
    assert.match(f.surface.textContent, /version-a/);
    await f.click("查看当前可用版本的解析内容");
    assert.match(
      f.surface.textContent,
      /<script>window.attacked = true<\/script>/,
    );
    assert.equal(f.surface.querySelector("script"), null);
    await f.click("查看版本 version-a 的解析内容");
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "content" && payload.version_id === "version-a",
      ),
    );
    await f.click("索引当前版本 / 重试失败");
    await f.click("重建当前版本索引");
    assert.deepEqual(
      f.calls
        .filter(({ action }) => action === "index")
        .map(({ payload }) => payload),
      [
        { document_id: "doc-a", rebuild: false },
        { document_id: "doc-a", rebuild: true },
      ],
    );
    f.surface.querySelector('[aria-label="资料展示名称"]').value = "新名称";
    await f.click("更改展示名称");
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "rename_document" && payload.name === "新名称",
      ),
    );
  } finally {
    f.cleanup();
  }
});

test("search and Windows picker preserve scope and explicit selected-file import", async () => {
  const f = await fixture();
  try {
    f.surface.querySelector(
      '[aria-label="在当前知识库检索测试（问题会发送至 embedding 服务）"]',
    ).value = "Redis 持久化";
    await f.click("检索当前选定知识库");
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "search" && payload.knowledge_base_ids[0] === "base-a",
      ),
    );
    assert.equal(
      f.surface.querySelector('a[target="_blank"]').getAttribute("rel"),
      "noopener noreferrer",
    );
    await f.click("从 Windows 授权目录选取文件");
    await f.click("打开 notes");
    await f.click("导入 redis.txt");
    assert.ok(
      f.calls.some(
        ({ action, payload }) =>
          action === "import_local" &&
          payload.root_id === "study" &&
          payload.relative_path === "notes/redis.txt" &&
          payload.knowledge_base_id === "base-a",
      ),
    );
  } finally {
    f.cleanup();
  }
});

test("deletion remains preview-first, cancels safely and retains cleanup", async () => {
  const f = await fixture();
  try {
    f.surface.querySelector('[data-document-id="doc-a"]').click();
    await tick();
    await f.click("预览删除资料");
    assert.match(f.surface.textContent, /Windows 原文件/);
    assert.match(f.surface.textContent, /聊天历史/);
    assert.match(f.surface.textContent, /snapshot-digest/);
    await f.click("取消");
    assert.ok(f.button("预览删除资料"));
    await f.click("预览删除知识库");
    assert.deepEqual(
      f.calls.filter(({ action }) => action === "delete_preview").at(-1)
        .payload,
      { kind: "knowledge_base", target: "base-a" },
    );
    await f.click("查询并重试知识库副本清理");
    assert.ok(f.calls.some(({ action }) => action === "cleanup"));
  } finally {
    f.cleanup();
  }
});

test("upload and update retain CSRF, selected bytes, document ID and expected revision", async () => {
  const f = await fixture();
  const originalFetch = globalThis.fetch;
  const uploads = [];
  try {
    document.cookie = "csrf_token=synthetic-token";
    globalThis.fetch = async (path, request) => {
      uploads.push({ path, request });
      return {
        ok: true,
        json: async () => ({
          ok: true,
          status: "ready",
          document_id: "doc-a",
          version_id: "version-a",
        }),
      };
    };
    const file = new f.window.File(["synthetic"], "redis.txt", {
      type: "text/plain",
    });
    const setFile = () => {
      const input = [...f.surface.querySelectorAll('input[type="file"]')].at(
        -1,
      );
      Object.defineProperty(input, "files", {
        configurable: true,
        value: [file],
      });
      input.dispatchEvent(new f.window.Event("change"));
    };
    setFile();
    await f.click("导入上传文件");
    setFile();
    await f.click("确认选定文件并更新版本");
    assert.equal(uploads[0].request.headers["X-CSRF-Token"], "synthetic-token");
    assert.equal(uploads[0].request.body.get("knowledge_base_id"), "base-a");
    assert.equal(uploads[1].request.body.get("document_id"), "doc-a");
    assert.equal(uploads[1].request.body.get("expected_revision"), "3");
    assert.equal(uploads[1].request.body.get("file").name, "redis.txt");
  } finally {
    globalThis.fetch = originalFetch;
    f.cleanup();
  }
});

test("load failure is visible with a retry action and disposal removes resources", async () => {
  let fail = true;
  const f = await fixture({
    bases: () => {
      if (fail) throw Error("synthetic-load-failure");
      return { knowledge_bases: [] };
    },
  });
  try {
    assert.match(
      f.surface.querySelector('[role="alert"]').textContent,
      /synthetic-load-failure/,
    );
    assert.equal(f.surface.querySelector(".loading"), null);
    fail = false;
    await f.click("重新加载");
    assert.match(f.surface.textContent, /创建第一个知识库/);
    f.controller.dispose();
    assert.equal(f.surface.childElementCount, 0);
  } finally {
    f.cleanup();
  }
});

test("a late document response cannot overwrite the newly selected knowledge base", async () => {
  let resolveDocument;
  const f = await fixture({
    document: () =>
      new Promise((resolve) => {
        resolveDocument = resolve;
      }),
  });
  try {
    f.surface.querySelector('[data-document-id="doc-a"]').click();
    await tick();
    await f.click("第二个知识库");
    resolveDocument({
      name: "旧响应",
      knowledge_base_id: "base-a",
      versions: [],
    });
    await tick();
    assert.equal(f.surface.querySelector(".detail-panel").hidden, true);
    assert.doesNotMatch(f.surface.textContent, /旧响应/);
    assert.match(f.surface.textContent, /还没有资料/);
  } finally {
    f.cleanup();
  }
});

test("delete confirmation sends the original preview digest only after an explicit click", async () => {
  const f = await fixture();
  const originalFetch = globalThis.fetch;
  const requests = [];
  try {
    document.cookie = "csrf_token=synthetic-token";
    globalThis.fetch = async (path, request) => {
      requests.push({ path, request });
      return {
        ok: true,
        json: async () => ({
          ok: true,
          cleanup_pending: 0,
          index_cleanup_pending: false,
        }),
      };
    };
    f.surface.querySelector('[data-document-id="doc-a"]').click();
    await tick();
    await f.click("预览删除资料");
    assert.equal(requests.length, 0);
    await f.click("确认上述范围并删除");
    assert.equal(requests[0].path, "/api/personal-knowledge/delete");
    assert.deepEqual(JSON.parse(requests[0].request.body), {
      kind: "document",
      target: "doc-a",
      digest: "snapshot-digest",
    });
    assert.equal(
      requests[0].request.headers["X-CSRF-Token"],
      "synthetic-token",
    );
    assert.match(f.surface.textContent, /Windows 原文件保留/);
  } finally {
    globalThis.fetch = originalFetch;
    f.cleanup();
  }
});

for (const route of ["local", "upload"]) {
  test(`duplicate ${route} import keeps a visible result beside selected document`, async () => {
    const f = await fixture();
    const previousFetch = globalThis.fetch;
    try {
      if (route === "local") {
        await f.click("从 Windows 授权目录选取文件");
        await f.click("导入 redis.txt");
      } else {
        document.cookie = "csrf_token=synthetic-token";
        globalThis.fetch = async () => ({
          ok: true,
          json: async () => ({
            ok: true,
            status: "duplicate",
            document_id: "doc-a",
            version_id: "version-a",
          }),
        });
        const input = f.surface.querySelector('input[type="file"]');
        Object.defineProperty(input, "files", {
          value: [new f.window.File(["synthetic"], "redis.txt")],
          configurable: true,
        });
        await f.click("导入上传文件");
      }
      const result = f.surface.querySelector(
        '.detail-panel .import-result[role="status"]',
      );
      assert.ok(
        result,
        "Import feedback must remain beside the selected document after refresh",
      );
      assert.equal(result.hidden, false);
      assert.match(result.textContent, /重复导入.*复用已有版本.*未新增文档/);
      assert.match(
        f.surface.querySelector(".notice.feedback").textContent,
        /重复导入/,
      );
      await f.click("第二个知识库");
      assert.equal(
        f.surface.querySelector(".detail-panel .import-result"),
        null,
      );
    } finally {
      globalThis.fetch = previousFetch;
      f.cleanup();
    }
  });
}
