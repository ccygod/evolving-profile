export type ClusterCentroid = { cx: number; cy: number };

/** Place an optional integration cluster in the middle and distribute peers around it. */
export function buildClusterCentroids(
  clusters: Array<[key: string, memberCount: number]>,
  ringRadius: number,
  centerKey?: string
): Map<string, ClusterCentroid> {
  const result = new Map<string, ClusterCentroid>();
  const centered = centerKey && clusters.some(([key]) => key === centerKey) ? centerKey : undefined;
  if (centered) result.set(centered, { cx: 0, cy: 0 });

  const ring = clusters.filter(([key]) => key !== centered);
  ring.forEach(([key], index) => {
    const angle = (index / Math.max(ring.length, 1)) * Math.PI * 2 - Math.PI / 2;
    result.set(key, { cx: Math.cos(angle) * ringRadius, cy: Math.sin(angle) * ringRadius });
  });
  return result;
}

// ---------------------------------------------------------------------------
// Orbit layout: one optional core cluster at the origin, peer clusters evenly
// spaced on a ring around it. Inside every cluster, members are scattered with
// the same hash-jittered ring formula the ungrouped (facts) constellation uses,
// at the same density (radius = 30·√n), so every graph shares one texture.
// ---------------------------------------------------------------------------

export const ORBIT_SPACING = 24;

export type OrbitCluster = { cx: number; cy: number; r: number; inner: number };

/** Radius of a cluster holding `count` scattered members — same density as the facts view. */
export function orbitClusterRadius(count: number, _hasAnchor = false, spacing = ORBIT_SPACING): number {
  return Math.max(40, spacing * Math.sqrt(Math.max(count, 1)));
}

/**
 * Offset of the j-th of `count` members from its cluster center. Mirrors the
 * facts layout: angle sweeps the circle with a hash nudge, radius falls in
 * [0.3R, R] (anchored clusters keep the middle clear for their hub node).
 */
export function scatterOffset(j: number, count: number, seed: number, r: number, hasAnchor: boolean): [number, number] {
  const angle = (j / Math.max(count, 1)) * Math.PI * 2 + ((seed % 100) / 100) * 0.5;
  const lo = hasAnchor ? 0.3 : count < 4 ? 0.15 : 0.08;
  const radius = r * lo + ((Math.abs(seed) % 1000) / 1000) * r * (1 - lo);
  const jx = ((seed % 200) - 100) * 0.12;
  const jy = (((seed >> 8) % 200) - 100) * 0.12;
  return [Math.cos(angle) * radius + jx, Math.sin(angle) * radius + jy];
}

/**
 * Place clusters: `centerKey` (when present) sits at the origin; each peer sits
 * just outside it at its own distance (small groups hug the core), and the
 * distances grow together only as far as needed to keep neighbours apart.
 * `ringRadius` is the farthest peer distance.
 */
export function buildOrbitLayout(
  clusters: Array<[key: string, memberCount: number, hasAnchor: boolean]>,
  centerKey?: string,
  gap = 24
): { centroids: Map<string, OrbitCluster>; ringRadius: number; centerKey?: string } {
  const centroids = new Map<string, OrbitCluster>();
  const radius = (count: number, anchor: boolean) => orbitClusterRadius(count, anchor);
  const center = centerKey ? clusters.find(([key]) => key === centerKey) : undefined;
  const peers = clusters.filter(([key]) => key !== center?.[0]);
  // Spacing uses the drawn region size: scatter radius + jitter + region padding.
  const REGION_PAD = 44;
  const centerR = center ? radius(center[1], center[2]) + REGION_PAD : 0;
  const peerR = peers.map(([, count, anchor]) => radius(count, anchor) + REGION_PAD);
  const angles = peers.map((_, i) => (i / Math.max(peers.length, 1)) * Math.PI * 2 - Math.PI / 2);

  let dist = peerR.map((r) => (center ? centerR + r + gap : 0));
  if (peers.length > 1) {
    if (!center) dist = peerR.map(() => Math.max(...peerR));
    const clear = () =>
      peers.every((_, i) => {
        const j = (i + 1) % peers.length;
        if (i === j) return true;
        const d = Math.hypot(
          Math.cos(angles[i]) * dist[i] - Math.cos(angles[j]) * dist[j],
          Math.sin(angles[i]) * dist[i] - Math.sin(angles[j]) * dist[j]
        );
        return d > peerR[i] + peerR[j] + gap / 2;
      });
    for (let k = 0; k < 200 && !clear(); k++) dist = dist.map((d) => d * 1.04 + 1);
  }

  if (center) centroids.set(center[0], { cx: 0, cy: 0, r: centerR - REGION_PAD, inner: 0 });
  peers.forEach(([key], index) => {
    const d = dist[index];
    centroids.set(key, {
      cx: d ? Math.cos(angles[index]) * d : 0,
      cy: d ? Math.sin(angles[index]) * d : 0,
      r: peerR[index] - REGION_PAD,
      inner: 0,
    });
  });
  return { centroids, ringRadius: Math.max(0, ...dist), centerKey: center?.[0] };
}
