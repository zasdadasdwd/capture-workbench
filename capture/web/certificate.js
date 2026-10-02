// 自动为 Markdown 代码块添加复制按钮，新增命令时不需要修改页面代码。
function showCopyError(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.style.display = "block";
  clearTimeout(showCopyError.timer);
  showCopyError.timer = setTimeout(() => (toast.style.display = "none"), 4000);
}

/** 精确复制原始文本，保留 Python 缩进，复制结果不包含行号和按钮文字。 */
async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "已复制";
    clearTimeout(button.copyTimer);
    button.copyTimer = setTimeout(
      () => (button.textContent = button.dataset.label),
      1600,
    );
  } catch {
    showCopyError("复制失败，请允许浏览器访问剪贴板，或手动选择命令复制。");
  }
}
function copyButton(label, text, accessibleLabel) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.label = label;
  button.textContent = label;
  button.setAttribute("aria-label", accessibleLabel);
  button.onclick = () => copyText(text, button);
  return button;
}

/** 每个非空代码行独立复制，同时保留整段复制入口供多行脚本使用。 */
for (const [blockIndex, code] of [
  ...document.querySelectorAll(".certificate-document pre > code"),
].entries()) {
  const original = code.textContent;
  const pre = code.parentElement;
  const block = document.createElement("div");
  block.className = "command-block";
  const toolbar = document.createElement("div");
  toolbar.className = "command-toolbar";
  const language = document.createElement("span");
  language.textContent =
    [...code.classList]
      .find((value) => value.startsWith("language-"))
      ?.slice(9) || "命令";
  toolbar.append(
    language,
    copyButton("复制全部", original, `复制第 ${blockIndex + 1} 段全部代码`),
  );
  pre.before(block);
  block.append(toolbar, pre);
  pre.replaceChildren();
  for (const [index, line] of original
    .replace(/\n$/, "")
    .split("\n")
    .entries()) {
    const row = document.createElement("div");
    row.className = "command-line";
    const content = document.createElement("code");
    content.textContent = line || " ";
    row.append(content);
    if (line.trim())
      row.append(
        copyButton(
          "复制",
          line,
          `复制第 ${blockIndex + 1} 段第 ${index + 1} 行`,
        ),
      );
    pre.append(row);
  }
}
