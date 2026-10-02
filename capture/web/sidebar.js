/** 请求目录侧栏：拖动时按帧更新，宽度与收起状态在本机记忆。 */
class DirectorySidebar {
  constructor() {
    this.sidebar = document.getElementById("requestSidebar");
    this.handle = document.getElementById("sidebarResize");
    this.toggle = document.getElementById("toggleSidebar");
    this.edgeToggle = document.getElementById("collapseSidebar");
    this.width = Number(localStorage.getItem("capture.sidebar.width")) || 232;
    this.collapsed =
      localStorage.getItem("capture.sidebar.collapsed") === "true";
    this.drag = null;
    this.frame = null;
    this.apply();
    this.toggle.onclick = () => {
      this.collapsed = !this.collapsed;
      this.apply();
      this.save();
    };
    this.ignoreEdgeClick = false;
    this.edgeToggle.onclick = (event) => {
      if (this.ignoreEdgeClick && event.detail !== 0) {
        this.ignoreEdgeClick = false;
        return;
      }
      this.ignoreEdgeClick = false;
      this.toggle.click();
    };
    // 短按切换；长按进入拖动模式，移动超过 6px 也可直接拖动。
    for (const target of [this.handle, this.edgeToggle]) {
      target.onpointerdown = (event) => this.startDrag(event, target);
      target.onpointermove = (event) => this.moveDrag(event);
      target.onpointerup = (event) => this.finishDrag(event);
      target.onpointercancel = () => this.finishDrag();
      target.onlostpointercapture = () => this.finishDrag();
    }
    this.handle.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "Enter"].includes(event.key))
        return;
      event.preventDefault();
      if (event.key === "Enter") return this.toggle.click();
      this.resize(
        event.key === "Home"
          ? 0
          : this.collapsed && event.key === "ArrowRight"
            ? 160
            : (this.collapsed
                ? 0
                : this.sidebar.getBoundingClientRect().width) +
              (event.key === "ArrowRight" ? 24 : -24),
      );
      this.save();
    };
    window.addEventListener("resize", () => this.apply());
  }

  /** 捕获指针，按住按钮移出其范围后仍能继续拖动。 */
  startDrag(event, target) {
    if (event.button !== 0 || this.drag) return;
    event.preventDefault();
    this.ignoreEdgeClick = false;
    this.drag = {
      x: event.clientX,
      width: this.sidebar.getBoundingClientRect().width,
      startedCollapsed: this.collapsed,
      next: event.clientX,
      pointerId: event.pointerId,
      target,
      moved: false,
      pressedAt: performance.now(),
      longPressed: false,
    };
    target.setPointerCapture(event.pointerId);
    if (target === this.edgeToggle) {
      const drag = this.drag;
      drag.pressTimer = setTimeout(() => {
        if (this.drag !== drag) return;
        drag.longPressed = true;
        document.body.classList.add("resizing-sidebar");
      }, 280);
    }
  }

  /** 小幅抖动视为点击，真正拖动后按帧合并布局更新。 */
  moveDrag(event) {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    this.drag.next = event.clientX;
    if (!this.drag.moved && Math.abs(this.drag.next - this.drag.x) < 6) return;
    this.drag.moved = true;
    document.body.classList.add("resizing-sidebar");
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.updateDrag();
    });
  }

  /** 向左缩到 80px 收起；从收起状态向右拖 24px 即展开。 */
  resize(width, threshold = this.collapsed ? 24 : 80) {
    this.collapsed = width < threshold;
    if (!this.collapsed)
      this.width = Math.min(this.maxWidth(), Math.max(160, width));
    this.apply();
  }
  maxWidth() {
    return Math.max(160, Math.min(480, window.innerWidth - 320));
  }
  updateDrag() {
    if (this.drag?.moved)
      this.resize(
        this.drag.width + this.drag.next - this.drag.x,
        this.drag.startedCollapsed ? 24 : 80,
      );
  }
  /** 长按或拖动结束都屏蔽随后的点击；长按未移动时保持原宽度。 */
  finishDrag(event) {
    if (!this.drag) return;
    if (event && event.pointerId !== this.drag.pointerId) return;
    clearTimeout(this.drag.pressTimer);
    if (event) this.moveDrag(event);
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.updateDrag();
    this.ignoreEdgeClick =
      this.drag.target === this.edgeToggle &&
      (this.drag.moved ||
        this.drag.longPressed ||
        performance.now() - this.drag.pressedAt >= 280 ||
        !event);
    const { target, pointerId } = this.drag;
    this.drag = null;
    if (target.hasPointerCapture(pointerId))
      target.releasePointerCapture(pointerId);
    document.body.classList.remove("resizing-sidebar");
    this.save();
  }
  apply() {
    const width = this.collapsed
      ? 0
      : Math.max(160, Math.min(this.width, this.maxWidth()));
    this.sidebar.style.width = `${width}px`;
    this.sidebar.classList.toggle("collapsed", this.collapsed);
    document
      .getElementById("sidebarDivider")
      .classList.toggle("collapsed", this.collapsed);
    this.sidebar.inert = this.collapsed;
    this.handle.setAttribute("aria-valuenow", width);
    this.toggle.setAttribute("aria-expanded", String(!this.collapsed));
    this.toggle.setAttribute(
      "aria-label",
      this.collapsed ? "展开请求目录" : "收起请求目录",
    );
    this.edgeToggle.textContent = this.collapsed ? "›" : "‹";
    this.edgeToggle.title = this.collapsed
      ? "短按展开，长按拖动侧栏"
      : "短按收起，长按拖动侧栏";
    this.edgeToggle.setAttribute("aria-expanded", String(!this.collapsed));
    this.edgeToggle.setAttribute(
      "aria-label",
      this.collapsed ? "一键展开请求目录" : "一键收起请求目录",
    );
  }
  save() {
    localStorage.setItem("capture.sidebar.width", this.width);
    localStorage.setItem("capture.sidebar.collapsed", this.collapsed);
  }
}
const directorySidebar = new DirectorySidebar();
