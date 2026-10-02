// 三维力导向布局：仅使用实际完整值证据建边；已有坐标在实时追踪时保持稳定。
const positions = new Map();
const seeded = (id, index) => {
  let hash = 2166136261;
  for (const character of id)
    hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  const angle = ((hash >>> 0) / 4294967295) * Math.PI * 2;
  const radius = 12 + Math.sqrt(index + 1) * 4.5;
  return [
    Math.cos(angle) * radius,
    Math.sin(angle) * radius,
    ((hash >>> 9) % 47) - 23,
  ];
};

self.onmessage = ({ data }) => {
  if (data.reset) positions.clear();
  const fixed = new Set([
    ...Object.keys(data.saved || {}),
    ...(data.reset
      ? []
      : data.nodes
          .filter((node) => positions.has(node.id))
          .map((node) => node.id)),
  ]);
  for (const [id, value] of Object.entries(data.saved || {}))
    positions.set(id, value);
  data.nodes.forEach((node, index) => {
    if (!positions.has(node.id)) positions.set(node.id, seeded(node.id, index));
  });
  const active = data.nodes.slice(0, 300);
  const index = new Map(active.map((node, i) => [node.id, i]));
  const points = active.map((node) => [...positions.get(node.id)]);
  const velocity = active.map(() => [0, 0, 0]);
  const edges = data.edges
    .map((edge) => [
      index.get(edge.from),
      index.get(edge.to),
      edge.relation === "exact" ? 1 : 0.55,
    ])
    .filter(
      ([from, to]) => from !== undefined && to !== undefined && from !== to,
    );
  if (fixed.size < active.length) {
    for (let turn = 0; turn < 120; turn++) {
      const force = active.map(() => [0, 0, 0]);
      for (let i = 0; i < points.length; i++) {
        for (let j = i + 1; j < points.length; j++) {
          const delta = points[i].map((value, axis) => value - points[j][axis]);
          const distance = Math.max(3, Math.hypot(...delta));
          const strength = 22 / (distance * distance);
          for (let axis = 0; axis < 3; axis++) {
            const value = (delta[axis] / distance) * strength;
            force[i][axis] += value;
            force[j][axis] -= value;
          }
        }
      }
      for (const [from, to, weight] of edges) {
        const delta = points[to].map(
          (value, axis) => value - points[from][axis],
        );
        const distance = Math.max(1, Math.hypot(...delta));
        const strength = (distance - 13) * 0.012 * weight;
        for (let axis = 0; axis < 3; axis++) {
          const value = (delta[axis] / distance) * strength;
          force[from][axis] += value;
          force[to][axis] -= value;
        }
      }
      points.forEach((point, i) => {
        if (fixed.has(active[i].id)) return;
        for (let axis = 0; axis < 3; axis++) {
          velocity[i][axis] =
            (velocity[i][axis] + force[i][axis] - point[axis] * 0.001) * 0.78;
          point[axis] += velocity[i][axis];
        }
      });
    }
    const extent = Math.max(1, ...points.flat().map(Math.abs));
    const scale = fixed.size ? 1 : Math.min(1, 62 / extent);
    points.forEach((point, i) => {
      if (!fixed.has(active[i].id))
        positions.set(
          active[i].id,
          point.map((value) => value * scale),
        );
    });
  }
  self.postMessage({
    sequence: data.sequence,
    positions: Object.fromEntries(
      data.nodes.map((node) => [node.id, positions.get(node.id)]),
    ),
  });
};
