export function cycleNodeId(nodeIds: string[], currentId: string, direction: -1 | 1): string | null {
  if (!nodeIds.length) return null;
  const current = nodeIds.indexOf(currentId);
  if (current < 0) return direction > 0 ? nodeIds[0] : nodeIds[nodeIds.length - 1];
  return nodeIds[(current + direction + nodeIds.length) % nodeIds.length];
}
