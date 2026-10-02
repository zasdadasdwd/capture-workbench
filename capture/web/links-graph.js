import * as THREE from "three";
import { OrbitControls } from "./vendor/three/OrbitControls.js";

/** 三维节点关系图渲染器：实例化节点、批量连线、按需绘制与稳定布局。 */
export class NetworkGraph {
  constructor(container, fallback, onSelect) {
    this.container = container;
    this.fallback = fallback;
    this.onSelect = onSelect;
    this.nodes = [];
    this.edges = [];
    this.positions = {};
    this.mode = "3d";
    this.sequence = 0;
    this.worker = new Worker("/links-layout-worker.js?v=20261002-network-v4");
    this.worker.onmessage = ({ data }) => {
      if (data.sequence !== this.sequence) return;
      this.positions = data.positions;
      this.draw();
    };
    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
      container.append(this.renderer.domElement);
      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color("#19344d");
      this.camera = new THREE.PerspectiveCamera(48, 1, 0.1, 3000);
      this.camera.position.set(30, 50, 170);
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = false;
      this.controls.minDistance = 10;
      this.controls.maxDistance = 650;
      this.controls.addEventListener("change", () => this.requestDraw());
      this.raycaster = new THREE.Raycaster();
      this.raycaster.params.Line.threshold = 1.2;
      this.meshGroup = new THREE.Group();
      this.scene.add(this.meshGroup);
      this.labels = document.createElement("div");
      this.labels.className = "network-labels";
      container.append(this.labels);
      const canvas = this.renderer.domElement;
      this.tooltip = document.createElement("div");
      this.tooltip.className = "galaxy-tooltip";
      this.tooltip.hidden = true;
      container.append(this.tooltip);
      canvas.addEventListener("pointermove", (event) => {
        if (event.buttons || !this.nodeMesh) {
          this.tooltip.hidden = true;
          return;
        }
        const bounds = canvas.getBoundingClientRect();
        this.raycaster.setFromCamera(
          new THREE.Vector2(
            ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
            (-(event.clientY - bounds.top) / bounds.height) * 2 + 1,
          ),
          this.camera,
        );
        const hit = this.raycaster.intersectObject(this.nodeMesh)[0];
        const node =
          hit?.instanceId !== undefined ? this.nodes[hit.instanceId] : null;
        this.tooltip.hidden = !node;
        if (!node) return;
        this.tooltip.textContent = `${node.method || ""} ${node.url}
${node.code || node.status || ""}`;
        this.tooltip.style.left =
          Math.max(
            8,
            Math.min(bounds.width - 240, event.clientX - bounds.left + 10),
          ) + "px";
        this.tooltip.style.top =
          Math.max(
            10,
            Math.min(bounds.height - 90, event.clientY - bounds.top + 10),
          ) + "px";
      });
      canvas.addEventListener("pointerleave", () => {
        this.tooltip.hidden = true;
      });
      canvas.addEventListener("pointerdown", (event) => {
        this.pointer = [event.clientX, event.clientY];
      });
      canvas.addEventListener("pointerup", (event) => {
        if (
          !this.pointer ||
          Math.hypot(
            event.clientX - this.pointer[0],
            event.clientY - this.pointer[1],
          ) > 5
        )
          return;
        const bounds = canvas.getBoundingClientRect();
        this.raycaster.setFromCamera(
          new THREE.Vector2(
            ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
            (-(event.clientY - bounds.top) / bounds.height) * 2 + 1,
          ),
          this.camera,
        );
        const hit =
          this.nodeMesh && this.raycaster.intersectObject(this.nodeMesh)[0];
        if (hit && hit.instanceId !== undefined) {
          this.onSelect({ node: this.nodes[hit.instanceId] });
          return;
        }
        for (const line of this.lines || []) {
          const crossing = this.raycaster.intersectObject(line)[0];
          if (crossing) {
            const edge = line.userData.edges[Math.floor(crossing.index / 2)];
            if (edge) {
              this.onSelect({ edge });
              return;
            }
          }
        }
      });
    } catch (error) {
      this.renderer?.dispose();
      this.renderer = null;
      container.replaceChildren();
      this.mode = "2d";
    }
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(container);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.requestDraw();
    });
    this.resize();
  }

  resize() {
    if (!this.renderer) return;
    const width = this.container.clientWidth,
      height = this.container.clientHeight;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.requestDraw();
  }

  setData(nodes, edges, { reset = false, saved = {} } = {}) {
    this.nodes = nodes;
    this.edges = edges;
    this.worker.postMessage({
      nodes,
      edges,
      saved,
      reset,
      sequence: ++this.sequence,
    });
  }

  highlight(nodeId = null, edgeId = null, field = null) {
    this.selected = nodeId;
    this.selectedEdge = edgeId;
    this.selectedField = field;
    this.draw();
  }

  relevant(edge) {
    if (edge.judgment === "excluded") return false;
    if (this.selectedEdge)
      return (
        edge.id === this.selectedEdge ||
        (this.selectedField && edge.source_field === this.selectedField)
      );
    if (this.selectedField)
      return (
        (edge.from === this.selected &&
          edge.source_field === this.selectedField) ||
        (edge.to === this.selected && edge.target_field === this.selectedField)
      );
    return (
      !this.selected || edge.from === this.selected || edge.to === this.selected
    );
  }

  draw() {
    if (this.mode !== "3d" || !this.renderer) {
      this.svg();
      return;
    }
    this.container.hidden = false;
    this.fallback.hidden = true;
    // 替换图时释放旧几何与材质，避免实时展开累积 GPU 内存。
    for (const child of [...this.meshGroup.children]) {
      this.meshGroup.remove(child);
      child.geometry?.dispose();
      child.material?.dispose();
    }
    this.lines = [];
    const connected = new Set([this.selected]);
    for (const edge of this.edges)
      if (this.relevant(edge)) {
        connected.add(edge.from);
        connected.add(edge.to);
      }
    const mesh = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1.35, 16, 12),
      new THREE.MeshBasicMaterial(),
      this.nodes.length,
    );
    const matrix = new THREE.Matrix4();
    this.nodes.forEach((node, index) => {
      const position = this.positions[node.id] || [0, 0, 0];
      matrix.makeTranslation(...position);
      mesh.setMatrixAt(index, matrix);
      const dim =
        !!(this.selected || this.selectedField || this.selectedEdge) &&
        !connected.has(node.id);
      mesh.setColorAt(
        index,
        new THREE.Color(
          dim
            ? 0x253448
            : node.id === this.selected
              ? 0xe7f5ff
              : node.source
                ? 0x7185ff
                : node.response_match
                  ? 0x23d4dc
                  : node.group
                    ? 0xbb9fff
                    : 0xf5bf42,
        ),
      );
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    this.nodeMesh = mesh;
    this.meshGroup.add(mesh);
    const buckets = [[], [], []];
    for (const edge of this.edges) {
      const dim =
        edge.judgment === "excluded" ||
        ((this.selected || this.selectedField || this.selectedEdge) &&
          !this.relevant(edge));
      buckets[
        dim
          ? 2
          : edge.relation === "exact" && edge.available_before_target
            ? 0
            : 1
      ].push(edge);
    }
    buckets.forEach((edges, index) => {
      if (!edges.length) return;
      const vertices = [];
      for (const edge of edges)
        vertices.push(
          ...(this.positions[edge.from] || [0, 0, 0]),
          ...(this.positions[edge.to] || [0, 0, 0]),
        );
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(vertices, 3),
      );
      const material =
        index === 1
          ? new THREE.LineDashedMaterial({
              color: 0xa898f3,
              dashSize: 2,
              gapSize: 1,
              transparent: true,
              opacity: 0.8,
            })
          : new THREE.LineBasicMaterial({
              color: index === 2 ? 0x263247 : 0x68b6ec,
              transparent: true,
              opacity: index === 2 ? 0.3 : 0.65,
            });
      const line = new THREE.LineSegments(geometry, material);
      if (index === 1) line.computeLineDistances();
      line.userData.edges = edges;
      this.lines.push(line);
      this.meshGroup.add(line);
    });
    if (this.edges.length) {
      const arrows = new THREE.InstancedMesh(
        new THREE.ConeGeometry(0.5, 1.7, 6),
        new THREE.MeshBasicMaterial(),
        this.edges.length,
      );
      const up = new THREE.Vector3(0, 1, 0);
      this.edges.forEach((edge, index) => {
        const from = new THREE.Vector3(
          ...(this.positions[edge.from] || [0, 0, 0]),
        );
        const to = new THREE.Vector3(...(this.positions[edge.to] || [0, 0, 0]));
        const direction = to.clone().sub(from).normalize();
        const rotation = new THREE.Quaternion().setFromUnitVectors(
          up,
          direction,
        );
        const position = from.lerp(to, 0.72);
        const matrix = new THREE.Matrix4().compose(
          position,
          rotation,
          new THREE.Vector3(1, 1, 1),
        );
        arrows.setMatrixAt(index, matrix);
        const dim =
          edge.judgment === "excluded" ||
          ((this.selected || this.selectedField || this.selectedEdge) &&
            !this.relevant(edge));
        arrows.setColorAt(index, new THREE.Color(dim ? 0x263247 : 0x89c5f4));
      });
      arrows.computeBoundingSphere();
      this.meshGroup.add(arrows);
    }
    if (this.selected && this.positions[this.selected]) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(3.1, 0.11, 8, 64),
        new THREE.MeshBasicMaterial({
          color: 0xa8d4ff,
          transparent: true,
          opacity: 0.9,
        }),
      );
      ring.position.set(...this.positions[this.selected]);
      ring.quaternion.copy(this.camera.quaternion);
      this.meshGroup.add(ring);
    }
    this.updateLabels();
    this.requestDraw();
  }

  /** 节点标签随镜头投影；只展示有限数量，避免密集图遮挡请求。 */
  updateLabels() {
    if (!this.labels || !this.camera) return;
    const chosen = [...this.nodes]
      .sort(
        (a, b) =>
          (b.id === this.selected) - (a.id === this.selected) ||
          (b.source ? 1 : 0) - (a.source ? 1 : 0) ||
          (b.response_match ? 1 : 0) - (a.response_match ? 1 : 0),
      )
      .slice(0, 34);
    this.labelNodes = chosen;
    this.labels.replaceChildren(
      ...chosen.map((node) => {
        const label = document.createElement("span");
        label.className =
          "network-label" +
          (node.source ? " source" : node.response_match ? " response" : "");
        let path = node.url || node.host || "";
        try {
          path =
            new URL(path).pathname.split("/").filter(Boolean).at(-1) ||
            new URL(path).host;
        } catch {}
        label.textContent = path.slice(0, 19);
        label.title = node.url || "";
        return label;
      }),
    );
    this.placeLabels();
  }

  placeLabels() {
    if (!this.labels || !this.camera || !this.labelNodes) return;
    this.camera.updateMatrixWorld();
    const width = this.container.clientWidth,
      height = this.container.clientHeight;
    const placed = [];
    this.labelNodes.forEach((node, index) => {
      const coords = this.positions[node.id];
      const label = this.labels.children[index];
      if (!coords || !label) return;
      const point = new THREE.Vector3(...coords).project(this.camera);
      const x = ((point.x + 1) * width) / 2;
      const y = ((1 - point.y) * height) / 2;
      const visible =
        point.z > -1 &&
        point.z < 1 &&
        Math.abs(point.x) < 1 &&
        Math.abs(point.y) < 1 &&
        y > 82 &&
        y < height - 34 &&
        !placed.some(
          ([px, py]) => Math.abs(px - x) < 95 && Math.abs(py - y) < 22,
        );
      label.hidden = !visible;
      if (visible) {
        placed.push([x, y]);
        label.style.left = `${x}px`;
        label.style.top = `${y}px`;
      }
    });
  }

  requestDraw() {
    if (this.frame || !this.renderer || document.hidden || this.mode !== "3d")
      return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.renderer.render(this.scene, this.camera);
      this.placeLabels();
    });
  }

  /** 无 WebGL 时仍能点击节点和证据，二维视图与三维图共用数据。 */
  svg() {
    this.container.hidden = true;
    this.fallback.hidden = false;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.classList.add("fallback-svg");
    const box = this.svgBox || [-80, -85, 220, 180];
    svg.setAttribute("viewBox", box.join(" "));
    svg.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const box = svg.getAttribute("viewBox").split(" ").map(Number);
        const scale = event.deltaY > 0 ? 1.1 : 0.9;
        this.svgBox = [
          box[0] + (box[2] * (1 - scale)) / 2,
          box[1] + (box[3] * (1 - scale)) / 2,
          Math.max(10, Math.min(1500, box[2] * scale)),
          Math.max(10, Math.min(1500, box[3] * scale)),
        ];
        svg.setAttribute("viewBox", this.svgBox.join(" "));
      },
      { passive: false },
    );
    let drag = null;
    svg.addEventListener("pointerdown", (event) => {
      if (event.target === svg) {
        drag = [
          event.clientX,
          event.clientY,
          ...svg.getAttribute("viewBox").split(" ").map(Number),
        ];
        svg.setPointerCapture(event.pointerId);
      }
    });
    svg.addEventListener("pointermove", (event) => {
      if (!drag) return;
      this.svgBox = [
        drag[2] - ((event.clientX - drag[0]) * drag[4]) / svg.clientWidth,
        drag[3] - ((event.clientY - drag[1]) * drag[5]) / svg.clientHeight,
        drag[4],
        drag[5],
      ];
      svg.setAttribute("viewBox", this.svgBox.join(" "));
    });
    svg.addEventListener("pointerup", () => {
      drag = null;
    });
    const coordinates = {};
    this.nodes.forEach((node, index) => {
      const p = this.positions[node.id] || [0, 0, 0];
      coordinates[node.id] =
        this.mode === "timeline"
          ? [
              -55 +
                ((node.started - (this.nodes[0]?.started || 0)) /
                  Math.max(
                    1,
                    (this.nodes.at(-1)?.started || 0) -
                      (this.nodes[0]?.started || 0),
                  )) *
                  150,
              -65 + (index % 25) * 5,
            ]
          : [p[0], p[1] + p[2] * 0.15];
    });
    for (const edge of this.edges) {
      const a = coordinates[edge.from],
        b = coordinates[edge.to];
      if (!a || !b) continue;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", a[0]);
      line.setAttribute("y1", a[1]);
      line.setAttribute("x2", b[0]);
      line.setAttribute("y2", b[1]);
      line.setAttribute("stroke", this.relevant(edge) ? "#6ea8e0" : "#263247");
      line.setAttribute("stroke-width", ".5");
      if (edge.relation !== "exact")
        line.setAttribute("stroke-dasharray", "2 1");
      line.addEventListener("click", () => this.onSelect({ edge }));
      svg.append(line);
    }
    for (const node of this.nodes) {
      const [x, y] = coordinates[node.id];
      const group = document.createElementNS(ns, "g");
      group.classList.add("fallback-node");
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "0");
      group.setAttribute(
        "aria-label",
        `${node.method || ""} ${node.url || node.host}`,
      );
      const circle = document.createElementNS(ns, "circle");
      circle.setAttribute("cx", x);
      circle.setAttribute("cy", y);
      circle.setAttribute("r", node.id === this.selected ? "3" : "1.8");
      circle.setAttribute(
        "fill",
        node.id === this.selected
          ? "#e4f4ff"
          : node.source
            ? "#ffc879"
            : "#7db7f6",
      );
      const title = document.createElementNS(ns, "title");
      title.textContent = node.url || node.host;
      group.append(circle, title);
      group.addEventListener("click", () => this.onSelect({ node }));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter") this.onSelect({ node });
      });
      svg.append(group);
    }
    this.fallback.replaceChildren(svg);
  }

  setMode(mode) {
    this.mode = mode === "3d" && !this.renderer ? "2d" : mode;
    this.draw();
  }
  focus(id) {
    if (this.mode !== "3d" && this.positions[id]) {
      const [x, y] = this.positions[id];
      this.svgBox = [x - 30, y - 25, 60, 50];
      this.svg();
      return;
    }
    if (!this.camera || !this.positions[id]) return;
    const position = new THREE.Vector3(...this.positions[id]);
    const delta = this.camera.position
      .clone()
      .sub(this.controls.target)
      .normalize()
      .multiplyScalar(45);
    this.controls.target.copy(position);
    this.camera.position.copy(position).add(delta);
    this.controls.update();
    this.requestDraw();
  }
  reset() {
    this.svgBox = null;
    if (this.mode !== "3d") this.svg();
    if (!this.camera) return;
    this.camera.position.set(30, 50, 170);
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    this.requestDraw();
  }
  cameraState() {
    return this.camera
      ? [...this.camera.position.toArray(), ...this.controls.target.toArray()]
      : [];
  }
  restoreCamera(state) {
    if (!this.camera || state?.length !== 6) return;
    this.camera.position.fromArray(state);
    this.controls.target.fromArray(state, 3);
    this.controls.update();
    this.requestDraw();
  }
  image() {
    if (this.mode === "3d" && this.renderer) {
      this.renderer.render(this.scene, this.camera);
      this.placeLabels();
      return this.renderer.domElement.toDataURL("image/png");
    }
    const svg = this.fallback.querySelector("svg");
    return svg
      ? "data:image/svg+xml;charset=utf-8," +
          encodeURIComponent(new XMLSerializer().serializeToString(svg))
      : null;
  }
  close() {
    this.worker.terminate();
    this.observer.disconnect();
    this.controls?.dispose();
    this.renderer?.dispose();

    if (this.frame) cancelAnimationFrame(this.frame);
  }
}
