function mount(surface, context) {
  const el = (tag, text = "", attrs = {}) => {
    const node = document.createElement(tag);
    node.textContent = String(text);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  surface.append(el("link", "", {rel:"stylesheet", href:new URL("./styles.css", import.meta.url).href, crossorigin:"use-credentials"}));
  const root = el("div", "", {class:"knowledge"});
  surface.append(root);
  const alert = el("p", "", {role:"alert", class:"error"});
  const feedback=el("p","",{role:"status"});
  root.append(el("h2", "个人知识库"), el("p", "保存原件、来源、解析版本和向量索引。解析 ready 不等于可检索；更新后必须索引新版本。PDF、UTF-8 Markdown/TXT，单文件最多 10 MiB。"), alert,feedback);
  const reportImport=result=>{feedback.textContent=`${result.status==="duplicate"?"重复导入，复用已有版本":`导入结果：${result.status}`} · document_id: ${result.document_id} · version_id: ${result.version_id}${result.error?` · 错误：${result.error}`:""}`;};
  const bases = el("section"), docs = el("section"), details = el("section");
  root.append(bases, docs, details);
  let active = true, base = null, documentId = null, sequence = 0;
  const check = result => {if (!result?.ok) throw Error(result?.error?.code || "操作未验证"); return result;};
  const call = async (action, payload={}) => check(await context.callBackend(action, payload));
  const run = async (fn, button) => {
    if (button) button.disabled = true;
    alert.textContent = "";
    try {await fn();} catch(error) {if (active) alert.textContent = error.message;}
    finally {if (active && button?.isConnected) button.disabled = false;}
  };
  const button = (text, fn) => {const node=el("button",text,{type:"button"});node.addEventListener("click",()=>void run(fn,node));return node;};
  const input = (label, attrs={}) => el("input","",{"aria-label":label,placeholder:label,...attrs});
  let polling = null;
  const indexDescription=(states,state)=>`索引：${state?.index_status||"unindexed"} · ${state?.completed||0}/${state?.total||0} · ${state?.error||""} · embedding 配置：${states.embedding_configured?"已配置，真实效果需验收":"缺失"} · 问答模型授权：${states.answer_model_granted?"已授予":"缺失"}`;
  async function indexDocument(id,rebuild) {
    const chosen=base;
    const result=await call("index",{document_id:id,rebuild});
    feedback.textContent=`索引 ${result.index_status} · version_id: ${result.version_id}。仅选中资料的片段发送至管理员配置的 embedding 服务；远程服务可能收费。`;
    if(polling)clearInterval(polling);
    polling=setInterval(()=>void run(async()=>{
      if(!active||base!==chosen){clearInterval(polling);polling=null;return;}
      const state=await call("index_status",{knowledge_base_id:chosen});
      if(!active||base!==chosen)return;
      const item=state.documents.find(d=>d.document_id===id);
      feedback.textContent=item?`索引 ${item.index_status} · ${item.completed}/${item.total} ${item.error||""}`:"来源不可用";
      const info=details.querySelector("[data-index-status]");
      if(documentId===id&&info)info.textContent=item?indexDescription(state,item):"来源不可用";
      if(!item||item.index_status!=="processing") {clearInterval(polling);polling=null;await loadDocuments();}
    }),1500);
    await loadDocuments();
  }
  async function post(path, body, multipart=false) {
    const token = document.cookie.split("; ").find(v=>v.startsWith("csrf_token="))?.slice(11);
    if (!token) throw Error("缺少 CSRF 凭据，请重新登录");
    const response=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"X-CSRF-Token":decodeURIComponent(token),...(!multipart?{"Content-Type":"application/json"}:{})},body:multipart?body:JSON.stringify(body)});
    const result=await response.json();
    if (!response.ok) throw Error(typeof result.detail === "string" ? result.detail : "请求被拒绝");
    return check(result);
  }
  function uploadControls(parent, selected=null) {
    const file=input("选择 PDF / Markdown / TXT",{type:"file",accept:".pdf,.md,.txt"});
    const name=input("可选资料展示名称（同名时改名后新建）");
    parent.append(el("p",selected?`更新 ${selected.name} · 文档 ${selected.document_id} · 当前修订 ${selected.revision}。保留旧版本；请核对选定文件后确认，无需 Windows 密钥。`:"上传到当前知识库。相同内容去重，同名不同内容拒绝覆盖。"),file);
    if(!selected) parent.append(name);
    parent.append(button(selected?"确认选定文件并更新版本":"导入上传文件",async()=>{
      const f=file.files[0];if(!f)throw Error("请选择文件");if(f.size>10*1024*1024)throw Error("文件超过 10 MiB");
      const body=new FormData();body.set("file",f);body.set("knowledge_base_id",base);
      if(!selected&&name.value.trim())body.set("display_name",name.value.trim());
      if(selected){body.set("document_id",selected.document_id);body.set("expected_revision",selected.revision);}
      const result=await post("/api/personal-knowledge/import",body,true);
      reportImport(result);
      await loadDocuments();await showDocument(result.document_id);
    }));
  }
  async function showDocument(id) {
    const turn=++sequence;const result=await call("document",{document_id:id});
    if(!active||turn!==sequence)return;documentId=id;details.replaceChildren();
    details.append(el("h3",result.name),el("p",`document_id: ${id} · revision: ${result.revision} · 当前可用版本: ${result.current_version_id||"无"}`));
    const states=await call("index_status",{knowledge_base_id:result.knowledge_base_id});
    if(!active||turn!==sequence)return;
    const state=states.documents.find(d=>d.document_id===id);
    details.append(el("p",indexDescription(states,state),{"data-index-status":id}));
    details.append(el("p","建立索引会把这篇资料的解析片段发送至配置的 embedding 端点；本地 Ollama 在本机处理，远程服务可能按量收费。"),button("索引当前版本 / 重试失败",()=>indexDocument(id,false)),button("重建当前版本索引",()=>indexDocument(id,true)));
    const renamed=input("资料展示名称",{value:result.name});
    details.append(renamed,button("更改展示名称",async()=>{await call("rename_document",{document_id:id,name:renamed.value});await loadDocuments();await showDocument(id);}));
    for(const version of result.versions) {
      details.append(el("pre",JSON.stringify(version,null,2)));
      if(version.status==="ready") details.append(button(`查看版本 ${version.id} 的解析内容`,async()=>{
        const content=await call("content",{document_id:id,version_id:version.id});
        if(documentId!==id||!active)return;
        for(const page of content.pages)details.append(el("h4",page.page===null?"文本（无页码）":`PDF 第 ${page.page} 页`),el("pre",page.text));
      }));
    }
    details.append(button("查看当前可用版本的解析内容",async()=>{
      const content=await call("content",{document_id:id});
      if(documentId!==id||!active)return;
      const text=el("div");
      for(const page of content.pages)text.append(el("h4",page.page===null?"文本（无页码）":`PDF 第 ${page.page} 页`),el("pre",page.text));
      details.append(text);
    }));
    uploadControls(details,result);
    details.append(button("预览删除资料",()=>deletion("document",id)));
  }
  async function deletion(kind,target) {
    const preview=await call("delete_preview",{kind,target});
    if(!active)return;details.replaceChildren();
    details.append(el("h3",`确认删除：${preview.name}`),el("p","仅删除知识库保存的原件副本、解析内容、索引及资料记录，不删除 Windows 原文件。引用入口立即失效。既有聊天已收到的片段仍属于聊天历史；若需清除这些副本，请另行删除相应聊天及备份。此操作无法撤销，无需 Windows 密钥。目标变化后本次确认失效。"),el("pre",JSON.stringify(preview,null,2)));
    details.append(button("确认上述范围并删除",async()=>{
      const result=await post("/api/personal-knowledge/delete",{kind,target,digest:preview.digest});
      if(kind==="knowledge_base")base=null;
      details.replaceChildren(el("p",`已删除。副本待清理: ${result.cleanup_pending}；索引清理待重试: ${result.index_cleanup_pending?"是":"否"}；Windows 原文件保留。`));
      await refresh();if(base)await loadDocuments();else docs.replaceChildren();
    }),button("取消",async()=>{details.replaceChildren();if(documentId)await run(()=>showDocument(documentId));}));
  }
  async function localPicker() {
    documentId=null;sequence++;
    const roots=await call("local_roots");details.replaceChildren(el("h3","选择 Windows 授权文件"));
    const rootSelect=el("select","",{"aria-label":"授权目录"});
    for(const item of roots.roots)rootSelect.append(el("option",`${item.root_id} · ${item.actual_path}`,{value:item.root_id}));
    const path=input("授权目录内相对子目录（空表示根）");const entries=el("div");
    async function list() {
      const result=await call("local_list",{root_id:rootSelect.value,relative_path:path.value});
      if(!active)return;entries.replaceChildren(el("p",result.actual_path));
      for(const entry of result.entries) {
        if(entry.kind==="directory")entries.append(button(`打开 ${entry.name}`,async()=>{path.value=[path.value,entry.name].filter(Boolean).join("/");await list();}));
        else if(entry.kind==="file"&&/\.(pdf|md|txt)$/i.test(entry.name))entries.append(button(`导入 ${entry.name}`,async()=>{
          const relative_path=[path.value,entry.name].filter(Boolean).join("/");
          const imported=await call("import_local",{knowledge_base_id:base,root_id:rootSelect.value,relative_path});
          reportImport(imported);
          await loadDocuments();await showDocument(imported.document_id);
        }));
        else entries.append(el("p",`${entry.name} · ${entry.kind}（不可导入）`));
      }
    }
    details.append(rootSelect,path,button("列出所选目录",list),entries);await list();
  }
  async function loadDocuments() {
    const chosen=base;const result=await call("documents",{knowledge_base_id:chosen});
    if(!active||chosen!==base)return;docs.replaceChildren(el("h3","资料列表"));
    for(const doc of result.documents)docs.append(button(`${doc.name} · 解析 ${doc.status} · 索引 ${doc.index_status} ${doc.index_error||""} · ${doc.document_id}`,()=>showDocument(doc.document_id)));
    docs.append(el("p",`当前选定知识库 ID：${chosen}。聊天选择不会自动同步，请在聊天中明确指定这个 ID 或知识库名称。`));
    const query=input("在当前知识库检索测试（问题会发送至 embedding 服务）");
    const hits=el("div");
    docs.append(query,button("检索当前选定知识库",async()=>{
      const result=await call("search",{knowledge_base_ids:[chosen],query:query.value});
      if(!active||chosen!==base)return;
      hits.replaceChildren(el("p",`检索 ${result.elapsed_ms} ms · ${result.evidence.length} 个片段；分数为相似度，不是正确概率。${result.evidence_insufficient?"资料不足：没有达到阈值的证据。":"回答是否正确仍需检查证据。"}`));
      for(const evidence of result.evidence) {
        const location=evidence.location.page===null?"文本，无页码":`PDF 第 ${evidence.location.page} 页`;
        const entry=el("article");
        entry.append(el("h4",evidence.document_name),el("p",`版本 ${evidence.version_id} · 片段 ${evidence.chunk_id} · ${location} · 相似度 ${evidence.score}`),el("pre",evidence.text),el("a","查看受权限控制的原文片段",{href:evidence.citation_url,target:"_blank",rel:"noopener noreferrer"}));
        hits.append(entry);
      }
    }),hits);
    uploadControls(docs);docs.append(button("从 Windows 授权目录选取文件",localPicker));
  }
  async function refresh() {
    const result=await call("bases");if(!active)return;bases.replaceChildren(el("h3","知识库"));
    const name=input("知识库名称");bases.append(name,button("创建知识库",async()=>{await call("create",{name:name.value});await refresh();}));
    for(const item of result.knowledge_bases) {
      const row=el("div","",{class:"row"});
      row.append(button(item.name,async()=>{base=item.knowledge_base_id;documentId=null;sequence++;details.replaceChildren();await loadDocuments();}));
      const rename=input("知识库新名称",{value:item.name});row.append(rename,button("重命名",async()=>{await call("rename",{knowledge_base_id:item.knowledge_base_id,name:rename.value});await refresh();}),button("预览删除知识库",()=>deletion("knowledge_base",item.knowledge_base_id)));bases.append(row);
    }
    bases.append(button("查询并重试知识库副本清理",async()=>{const result=await call("cleanup");details.replaceChildren(el("pre",JSON.stringify(result,null,2)));}));
  }
  void run(refresh);
  return {dispose(){active=false;if(polling)clearInterval(polling);root.remove();}};
}
export default {apiVersion:1,module:"knowledge-base.v1",icon:"book-open",surfaces:[{id:"library",slot:"page",title:"个人知识库",navigation:{label:"Knowledge library",labelZh:"个人知识库",icon:"book-open"},mount}]};
