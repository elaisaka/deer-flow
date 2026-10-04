function mountPlans(surface, context) {
  const make = (tag, text, attrs = {}) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  surface.append(make("link", "", {rel: "stylesheet", href: new URL("./styles.css", import.meta.url).href, crossorigin: "use-credentials"}));
  // Keep styles and content contained within the host's shadow surface.
  const root = make("div");
  surface.append(root);
  root.classList.add("organization-plans");
  root.append(make("h2", "文件整理方案"), make("p", "请核对授权目录、源文件和目标路径。确认按钮只批准当前展示的版本；批量操作可能部分完成。聊天中的“确认”不会授予执行权限。"));
  const refresh = make("button", "刷新方案列表", {type: "button"});
  const identifier = make("input", "", {placeholder: "输入 plan_id", "aria-label": "方案编号", maxlength: "32"});
  const load = make("button", "查看方案", {type: "button"});
  const list = make("div");
  const alert = make("p", "", {role: "alert"});
  const detail = make("div");
  root.append(refresh, identifier, load, alert, list, detail);
  let disposed = false;
  let generation = 0;
  const active = () => !disposed && !context.signal.aborted;
  const requireResult = (result) => {
    if (!result?.ok) throw Error(result?.error?.code || "结果未验证，请刷新查询实际记录");
    return result;
  };
  async function run(button, action) {
    button.disabled = true;
    alert.textContent = "";
    try { await action(); } catch (error) { if (active()) alert.textContent = String(error.message); }
    finally { if (active() && button.isConnected) button.disabled = false; }
  }
  function show(plan) {
    detail.replaceChildren();
    detail.append(make("h3", `方案 ${plan.plan_id} · 版本 ${plan.version}`), make("p", `状态：${plan.status} · 授权目录：${plan.root_path}`), make("p", `处理子目录：${plan.directory || "根目录"} · 不递归 · 创建时间：${plan.created_at}`), make("pre", JSON.stringify(plan.rule, null, 2)), make("p", `方案摘要：${plan.digest}`));
    if (plan.confirmation) detail.append(make("p", `用户确认：版本 ${plan.confirmation.version} · ${plan.confirmation.confirmed_at}`));
    const table = make("table");
    const head = make("tr");
    for (const label of ["源路径", "目标路径", "执行状态", "实际路径 / 核验", "错误 / 撤销记录"]) head.append(make("th", label));
    table.append(head);
    for (const item of plan.items) {
      const row = make("tr");
      const actual = item.actual_path ? `${item.actual_path} · 当前核验：${item.actual_verified === true ? "通过" : "未通过"}` : "尚无已验证执行结果";
      for (const value of [item.source_path, item.target_path || "—", item.state, actual, [item.error, item.undo_state, item.undo_error, item.actual_error].filter(Boolean).join(" · ") || "—"]) row.append(make("td", value));
      table.append(row);
    }
    const tableWrap = make("div", "", {class: "table-wrap"});
    tableWrap.append(table);
    detail.append(tableWrap, make("p", `可撤销：${plan.can_undo ? "是，撤销时仍会核对文件" : "否"}`));
    if (plan.status === "awaiting_confirmation" && !plan.items.some(item => item.state === "conflict") && plan.items.some(item => item.state === "pending")) {
      detail.append(make("p", "确认密钥仅保存在 Windows 私有目录。请在此输入，勿发送到聊天；密钥不保存在页面或方案记录中。"));
      const code = make("input", "", {type: "password", "aria-label": "Windows 确认密钥", placeholder: "Windows 确认密钥", autocomplete: "off"});
      const approve = make("button", `确认版本 ${plan.version} 并执行`, {type: "button"});
      approve.addEventListener("click", () => run(approve, async () => {
        const token = document.cookie.split("; ").find(value => value.startsWith("csrf_token="))?.slice("csrf_token=".length);
        if (!token) throw Error("缺少登录会话的 CSRF 凭据，请重新登录");
        const approvalCode = code.value;
        code.value = "";
        const response = await fetch("/api/file-organization/confirm", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(token)}, body: JSON.stringify({plan_id: plan.plan_id, version: plan.version, digest: plan.digest, approval_code: approvalCode})});
        const result = await response.json();
        if (!response.ok) throw Error(result.detail || "用户确认未获准");
        requireResult(result);
        if (active()) show(result);
      }));
      detail.append(code, approve);
    }
  }
  async function select(id) {
    const turn = ++generation;
    const plan = requireResult(await context.callBackend("get", {plan_id: id}));
    if (active() && turn === generation) {identifier.value = id; show(plan);}
  }
  async function refreshList() {
    const data = requireResult(await context.callBackend("list", {}));
    if (!active()) return;
    list.replaceChildren();
    for (const plan of data.plans) {
      const button = make("button", `${plan.plan_id} · v${plan.version || "?"} · ${plan.status}`, {type: "button"});
      button.addEventListener("click", () => run(button, () => select(plan.plan_id)));
      list.append(button);
    }
  }
  refresh.addEventListener("click", () => run(refresh, refreshList));
  load.addEventListener("click", () => run(load, () => select(identifier.value.trim())));
  void run(refresh, refreshList);
  return {dispose() {disposed = true; root.remove();}};
}

export default {apiVersion: 1, module: "file-organization.v1", icon: "folder", surfaces: [{id: "plans", slot: "page", title: "文件整理方案", navigation: {label: "File organization", labelZh: "文件整理", icon: "folder"}, mount: mountPlans}]};
