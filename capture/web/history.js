// 历史管理与实时抓包分开加载，避免启动时扫描并打开所有旧记录。
const $ = (id) => document.getElementById(id);
let sessions = [],
  deleting = null;

/** API 错误显示给用户，不把失败当成已经删除。 */
async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = await response.text();
    try {
      message = JSON.parse(message).detail || message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
function notify(message) {
  $("toast").textContent = message;
  $("toast").style.display = "block";
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => ($("toast").style.display = "none"), 5000);
}
function action(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (error) {
      notify(error.message);
    }
  };
}
function bytes(value) {
  if (value < 1024) return value + " B";
  if (value < 1048576) return (value / 1024).toFixed(1) + " KiB";
  return (value / 1048576).toFixed(1) + " MiB";
}

/** 渲染摘要和操作链接；流量字符串只放文本节点。 */
function render() {
  const keyword = $("historySearch").value.trim().toLowerCase();
  const kind = $("historyKind").value;
  const filtered = sessions.filter(
    (item) =>
      item.id.toLowerCase().includes(keyword) && (!kind || item.kind === kind),
  );
  $("historyRows").replaceChildren();
  $("historyTotal").textContent =
    `${filtered.length} 个会话 · ${bytes(filtered.reduce((sum, item) => sum + item.disk_bytes, 0))}`;
  $("historyEmpty").hidden = !!filtered.length;
  for (const session of filtered) {
    const row = document.createElement("tr");
    const time = document.createElement("td");
    const title = document.createElement("strong");
    title.textContent =
      session.id.slice(0, 10) +
      " " +
      session.id.slice(11, 19).replaceAll("-", ":");
    const id = document.createElement("small");
    id.textContent = session.id;
    time.append(title, id);
    row.append(time);
    for (const [index, value] of [
      session.kind === "replay" ? "重放" : "抓包",
      session.count,
      {
        stopped: "已结束",
        failed: "失败",
        cancelled: "已取消",
        running: "异常结束",
        interrupted: "异常结束",
      }[session.status] || session.status,
      bytes(session.disk_bytes),
    ].entries()) {
      const cell = document.createElement("td");
      cell.textContent = value;
      cell.dataset.label = ["类型", "记录数", "状态", "磁盘占用"][index];
      row.append(cell);
    }
    const controls = document.createElement("td");
    const open = document.createElement("a");
    open.textContent = "打开会话";
    open.href = "/?session=" + encodeURIComponent(session.id);
    const download = document.createElement("a");
    download.textContent = "下载会话包";
    download.href = `/api/history/${encodeURIComponent(session.id)}/download`;
    const remove = document.createElement("button");
    remove.textContent = "删除";
    remove.className = "danger";
    remove.onclick = () => {
      deleting = session.id;
      $("deleteHistoryDescription").textContent =
        `会话：${session.id}，共 ${session.count} 条记录，占用 ${bytes(session.disk_bytes)}。`;
      $("deleteHistoryId").value = "";
      $("deleteHistoryConfirm").disabled = true;
      $("deleteHistoryDialog").showModal();
      $("deleteHistoryId").focus();
    };
    controls.className = "history-controls";
    controls.append(open, download, remove);
    row.append(controls);
    $("historyRows").append(row);
  }
}
async function refresh() {
  sessions = await request("/api/history");
  render();
}
$("historySearch").oninput = render;
$("historyKind").onchange = render;
$("historyRefresh").onclick = action(refresh);
$("deleteHistoryCancel").onclick = () => $("deleteHistoryDialog").close();
$("deleteHistoryId").oninput = () =>
  ($("deleteHistoryConfirm").disabled =
    $("deleteHistoryId").value !== deleting);
$("deleteHistoryForm").onsubmit = action(async (event) => {
  event.preventDefault();
  if (!deleting || $("deleteHistoryId").value !== deleting) return;
  $("deleteHistoryConfirm").disabled = true;
  try {
    await request(`/api/history/${encodeURIComponent(deleting)}`, {
      method: "DELETE",
    });
    $("deleteHistoryDialog").close();
    deleting = null;
    notify("会话已删除");
    await refresh();
  } finally {
    $("deleteHistoryConfirm").disabled =
      $("deleteHistoryId").value !== deleting;
  }
});
$("historyTheme").onclick = () =>
  applyTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  );
action(refresh)();
