/** 请求详情左边缘拖动：记忆宽度，拖窄到阈值后收起，不增加操作按钮。 */
class DetailResizer {
  constructor() {
    this.panel = document.getElementById("detailPanel");
    this.grid = document.getElementById("contentGrid");
    this.handle = document.getElementById("detailResize");
    this.width = Number(localStorage.getItem("capture.detail.width")) || 520;
    this.drag = null;
    this.frame = null;
    this.handle.onpointerdown = (event) => this.start(event);
    this.handle.onpointermove = (event) => this.move(event);
    this.handle.onpointerup = (event) => this.finish(event);
    this.handle.onpointercancel = () => this.finish(null, true);
    this.handle.onlostpointercapture = () => this.finish();
    this.handle.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") return hideRequestDetail();
      const width =
        this.panel.getBoundingClientRect().width +
        (event.key === "ArrowLeft" ? 24 : -24);
      if (width < 200) hideRequestDetail();
      else {
        this.apply(width);
        this.save();
      }
    };
    this.observer = new ResizeObserver(() => {
      if (!this.drag) this.apply(this.width);
    });
    this.observer.observe(this.grid);
    this.apply(this.width);
  }

  /** 捕获指针，拖出边缘后仍保持连续操作。 */
  start(event) {
    if (event.button !== 0 || this.drag) return;
    event.preventDefault();
    this.drag = {
      id: event.pointerId,
      x: event.clientX,
      width: this.panel.getBoundingClientRect().width,
      next: event.clientX,
      moved: false,
    };
    this.handle.setPointerCapture(event.pointerId);
  }

  /** 移动 1px 即进入拖动，允许细微调整；布局更新仍按帧合并。 */
  move(event) {
    if (!this.drag || event.pointerId !== this.drag.id) return;
    this.drag.next = event.clientX;
    if (Math.abs(event.clientX - this.drag.x) < 1 && !this.drag.moved) return;
    this.drag.moved = true;
    document.body.classList.add("resizing-detail");
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      if (this.drag) this.apply(this.drag.width + this.drag.x - this.drag.next);
    });
  }

  /** 松手位置直接决定最终宽度，避免尚未绘制的微调被还原。 */
  finish(event, cancelled = false) {
    if (!this.drag || (event && event.pointerId !== this.drag.id)) return;
    if (event) this.drag.next = event.clientX;
    if (Math.abs(this.drag.next - this.drag.x) >= 1) this.drag.moved = true;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    const drag = this.drag;
    this.drag = null;
    if (this.handle.hasPointerCapture(drag.id))
      this.handle.releasePointerCapture(drag.id);
    document.body.classList.remove("resizing-detail");
    const width = drag.width + drag.x - drag.next;
    // 最小展示宽度为 200px，再拖窄 20px 即收起，不留长距离的无响应区。
    if (!cancelled && drag.moved && width <= 180) {
      this.apply(drag.width);
      hideRequestDetail();
    } else {
      this.apply(cancelled || !drag.moved ? drag.width : width);
      this.save();
    }
  }

  /** 桌面为列表保留至少 220px；窄屏沿用覆盖式详情并限制在容器内。 */
  apply(width) {
    const available = this.grid.getBoundingClientRect().width;
    const maximum = Math.max(
      200,
      available - (window.innerWidth > 1100 ? 220 : 0),
    );
    const actual = Math.min(maximum, Math.max(200, width));
    this.width = actual;
    this.grid.style.setProperty("--detail-width", `${actual}px`);
    this.handle.setAttribute("aria-valuenow", Math.round(actual));
    this.handle.setAttribute("aria-valuemax", Math.round(maximum));
  }
  save() {
    localStorage.setItem("capture.detail.width", this.width);
  }
}
const detailResizer = new DetailResizer();
