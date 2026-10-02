/** 用键值表展示 HTTP 头，保持重复头的顺序，所有值以纯文本插入。 */
function renderMessageHeaders(target, message) {
  target.replaceChildren();
  const headers = message?.headers || [];
  if (!headers.length) {
    const empty = document.createElement("p");
    empty.className = "muted message-empty";
    empty.textContent = message ? "没有头部字段" : "暂无头部内容";
    target.append(empty);
    return;
  }
  const table = document.createElement("table");
  const body = document.createElement("tbody");
  const counts = new Map();
  for (const [key, value] of headers) {
    const row = document.createElement("tr");
    const normalized = key.toLowerCase();
    const index = counts.get(normalized) || 0;
    counts.set(normalized, index + 1);
    row.dataset.analysisHeader = `${normalized}[${index}]`;
    const name = document.createElement("th");
    name.scope = "row";
    name.textContent = key;
    const cell = document.createElement("td");
    cell.textContent = value;
    row.append(name, cell);
    body.append(row);
  }
  table.append(body);
  target.append(table);
}

/** 复制当前分区的原始文本；权限不足时保留手动复制提示。 */
async function copyMessageText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择内容复制");
  }
}

/** 完整请求弹窗：头部独立折叠，正文按需加载，JSON 树延迟生成。 */
class RequestViewer {
  constructor() {
    this.dialog = document.getElementById("requestViewer");
    this.flow = null;
    this.part = "request";
    this.parsed = null;
    this.copyContent = "";
    this.sequence = 0;
    this.session = null;
    this.id = null;
    this.dialog.querySelectorAll("[data-viewer-part]").forEach((button) => {
      button.onclick = () => {
        this.part = button.dataset.viewerPart;
        this.render(true);
      };
    });
    document.getElementById("viewerMode").onchange = () => this.render();
    document.getElementById("viewerCopy").onclick = async () => {
      try {
        await navigator.clipboard.writeText(
          this.copyContent ?? JSON.stringify(this.parsed, null, 2),
        );
        toast("已复制当前内容");
      } catch {
        toast("复制失败，请手动选择内容复制");
      }
    };
    document.getElementById("viewerCopyHeaders").onclick = () =>
      copyMessageText(
        (this.flow?.[this.part]?.headers || [])
          .map(([key, value]) => `${key}: ${value}`)
          .join("\n"),
      );
    document.getElementById("viewerCopyMessage").onclick = () => {
      const message = this.flow?.[this.part];
      if (!message) return;
      const firstLine =
        this.part === "response"
          ? `${message.http_version || "HTTP"} ${this.flow.code || ""}`
          : `${message.method || this.flow.method || ""} ${message.url || this.flow.url || ""} ${message.http_version || "HTTP"}`;
      copyMessageText(
        `${firstLine}\n${(message.headers || []).map(([key, value]) => `${key}: ${value}`).join("\n")}\n\n${message.body_text || ""}`,
      );
    };
    document.getElementById("viewerCollapse").onclick = () => {
      this.dialog
        .querySelectorAll(".json-tree details[open]")
        .forEach((node) => {
          node.open = false;
        });
    };
    this.dialog.addEventListener("close", () => {
      this.sequence++;
      this.flow = null;
      this.parsed = null;
      this.copyContent = "";
      document.getElementById("viewerRaw").textContent = "";
      document.getElementById("viewerHeaders").replaceChildren();
      document.getElementById("viewerTree").replaceChildren();
    });
  }

  /** 先展示加载状态；关闭弹窗后丢弃迟到响应，避免保留大正文。 */
  async open(session, id, part = "request") {
    const sequence = ++this.sequence;
    this.flow = null;
    this.session = session;
    this.id = id;
    this.part = part;
    document.getElementById("viewerReplay").disabled = true;
    document.getElementById("viewerEditReplay").disabled = true;
    renderMessageHeaders(document.getElementById("viewerHeaders"), null);
    document.getElementById("viewerHeadersTitle").textContent = "请求头";
    document.getElementById("viewerBodyTitle").textContent = "请求体";
    document.getElementById("viewerHeadersCount").textContent = "";
    document.getElementById("viewerCopyHeaders").disabled = true;
    document.getElementById("viewerCopyMessage").disabled = true;
    document.getElementById("viewerUrl").textContent = "正在读取完整请求…";
    document.getElementById("viewerRaw").textContent = "";
    document.getElementById("viewerTree").replaceChildren();
    document.getElementById("viewerNotice").textContent = "加载中…";
    document.getElementById("viewerCopy").disabled = true;
    document.getElementById("viewerMode").value = "raw";
    document.getElementById("viewerRaw").hidden = false;
    document.getElementById("viewerTree").hidden = true;
    document.getElementById("viewerCollapse").hidden = true;
    if (!this.dialog.open) this.dialog.showModal();
    try {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(session)}/flows/${encodeURIComponent(id)}?text_only=true`,
      );
      if (!response.ok) throw new Error(`读取失败（${response.status}）`);
      const flow = await response.json();
      if (sequence !== this.sequence || !this.dialog.open) return;
      this.flow = flow;
      this.render(true);
    } catch (error) {
      if (sequence === this.sequence && this.dialog.open)
        document.getElementById("viewerNotice").textContent = error.message;
    }
  }

  /** 已打开的重放详情收到更新后读取完整正文，不重建弹窗或打断阅读。 */
  async refresh(session, id) {
    if (!this.dialog.open || session !== this.session || id !== this.id) return;
    const sequence = ++this.sequence;
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(session)}/flows/${encodeURIComponent(id)}?text_only=true`,
    );
    if (!response.ok) return;
    const flow = await response.json();
    if (sequence !== this.sequence || !this.dialog.open) return;
    const completed =
      this.flow?.status === "pending" && flow.status !== "pending";
    this.flow = flow;
    this.render(completed);
  }

  /** 每次切换报文重新判断 JSON；正文与头部始终以文本插入。 */
  render(resetMode = false) {
    if (!this.flow) return;
    const message = this.flow[this.part];
    const mode = document.getElementById("viewerMode");
    const raw = document.getElementById("viewerRaw");
    const tree = document.getElementById("viewerTree");
    const replayable =
      !!this.flow.request &&
      !this.flow.request.truncated &&
      this.flow.status !== "pending";
    document.getElementById("viewerReplay").disabled = !replayable;
    document.getElementById("viewerEditReplay").disabled = !replayable;
    const response = this.part === "response";
    document.getElementById("viewerHeadersTitle").textContent = response
      ? "响应头"
      : "请求头";
    document.getElementById("viewerBodyTitle").textContent = response
      ? "响应体"
      : "请求体";
    document.getElementById("viewerHeadersCount").textContent =
      `${message?.headers?.length || 0} 项`;
    document.getElementById("viewerCopyHeaders").disabled = !message;
    document.getElementById("viewerCopyMessage").disabled = !message;
    renderMessageHeaders(document.getElementById("viewerHeaders"), message);
    this.dialog.querySelectorAll("[data-viewer-part]").forEach((button) => {
      const active = button.dataset.viewerPart === this.part;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.getElementById("viewerUrl").textContent =
      `${response ? `${message?.http_version || "HTTP"} ${this.flow.code || ""} · ` : `${message?.method || this.flow.method || ""} `}${message?.url || this.flow.url || ""}`;
    let validJson = false;
    this.parsed = null;
    if (
      message &&
      !message.truncated &&
      !message.display_truncated &&
      !message.decode_error
    ) {
      try {
        this.parsed = JSON.parse(message.body_text || "");
        validJson = true;
      } catch {
        /* 非 JSON 报文继续使用完整报文视图。 */
      }
    }
    for (const option of mode.options)
      option.disabled = option.value !== "raw" && !validJson;
    if (resetMode)
      mode.value = validJson && this.part === "response" ? "tree" : "raw";
    if (!validJson) mode.value = "raw";
    const notice = [];
    if (!message)
      notice.push(
        this.flow.status === "pending"
          ? "正在重放，等待响应…"
          : this.flow.reason ||
              "这条记录没有该报文，TLS 透传无法查看 HTTP 内容。",
      );
    if (message?.truncated)
      notice.push("采集时正文已截断或未采集，以下是已保存内容。");
    if (message?.display_truncated)
      notice.push(
        "解压后的文本超过 16 MiB 展示上限，完整原始字节可通过导出获取。",
      );
    if (message?.decode_error)
      notice.push("正文解码失败，以下展示原始字节的文本视图。");
    const jsonHeader = message?.headers?.some(
      ([key, value]) =>
        key.toLowerCase() === "content-type" && /json/i.test(value),
    );
    if (
      jsonHeader &&
      !validJson &&
      !message.truncated &&
      !message.display_truncated
    )
      notice.push("正文不是有效 JSON，无法解析为树。");
    document.getElementById("viewerNotice").textContent = notice.join(" ");
    document.getElementById("viewerCopy").disabled = !message;
    document.getElementById("viewerCollapse").hidden = mode.value !== "tree";
    raw.hidden = mode.value === "tree";
    tree.hidden = mode.value !== "tree";
    tree.replaceChildren();
    if (mode.value === "raw") {
      this.copyContent = message?.body_text || "";
      raw.textContent =
        this.copyContent || (message ? "（空正文）" : "暂无正文内容");
    } else {
      // 树视图不提前格式化整段正文，复制或切换文本时才生成大字符串。
      this.copyContent =
        mode.value === "formatted"
          ? JSON.stringify(this.parsed, null, 2)
          : null;
      raw.textContent = this.copyContent || "";
      if (mode.value === "tree") {
        const root = this.createNode("$", this.parsed);
        tree.append(root);
        if (root.tagName === "DETAILS") root.open = true;
      }
    }
  }

  /** 大数组每批展示 200 项，展开对象时才生成子树，避免一次创建大量 DOM。 */
  createNode(key, value) {
    const container = value !== null && typeof value === "object";
    const node = document.createElement(container ? "details" : "div");
    node.className = "json-node";
    const line = document.createElement(container ? "summary" : "div");
    const name = document.createElement("span");
    name.className = "json-key";
    name.textContent = `${key}: `;
    line.append(name);
    if (!container) {
      const text = document.createElement("span");
      text.className = `json-value json-${value === null ? "null" : typeof value}`;
      text.textContent = JSON.stringify(value);
      line.append(text);
      node.append(line);
      return node;
    }
    const keys = Object.keys(value);
    const label = document.createElement("span");
    label.className = "muted";
    label.textContent = Array.isArray(value)
      ? `Array [${keys.length}]`
      : `Object {${keys.length}}`;
    line.append(label);
    node.append(line);
    let loaded = false;
    node.addEventListener("toggle", () => {
      if (!node.open || loaded) return;
      loaded = true;
      const children = document.createElement("div");
      children.className = "json-children";
      let offset = 0;
      const more = document.createElement("button");
      more.type = "button";
      const appendBatch = () => {
        more.remove();
        const fragment = document.createDocumentFragment();
        const end = Math.min(offset + 200, keys.length);
        for (; offset < end; offset++)
          fragment.append(this.createNode(keys[offset], value[keys[offset]]));
        children.append(fragment);
        if (offset < keys.length) {
          more.textContent = `继续显示（剩余 ${keys.length - offset} 项）`;
          children.append(more);
        }
      };
      more.onclick = appendBatch;
      node.append(children);
      appendBatch();
    });
    return node;
  }
}
const requestViewer = new RequestViewer();
