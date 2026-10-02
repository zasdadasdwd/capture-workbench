// 在 CSS 加载前读取主题，避免刷新黑色界面时短暂出现白屏。
function applyTheme(theme) {
  const value = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = value;
  try {
    localStorage.setItem("capture.theme", value);
  } catch {
    // 隐私模式禁用存储时，当前页面仍可正常切换主题。
  }
}

try {
  applyTheme(localStorage.getItem("capture.theme") || "light");
} catch {
  applyTheme("light");
}
