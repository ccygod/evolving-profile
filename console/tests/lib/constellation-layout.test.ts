import { describe, expect, it } from "vitest";
import { buildClusterCentroids, buildOrbitLayout, scatterOffset } from "@/lib/constellation-layout";

describe("buildClusterCentroids", () => {
  it("keeps the fusion cluster at the origin while surrounding dimensions occupy the ring", () => {
    const centroids = buildClusterCentroids([
      ["communication", 18],
      ["learning", 8],
      ["reasoning", 25],
      ["collaboration", 45],
      ["delivery", 74],
      ["fusion", 14],
    ], 700, "fusion");

    expect(centroids.get("fusion")).toMatchObject({ cx: 0, cy: 0 });
    for (const key of ["communication", "learning", "reasoning", "collaboration", "delivery"]) {
      expect(Math.hypot(centroids.get(key)!.cx, centroids.get(key)!.cy)).toBeCloseTo(700, 6);
    }
  });
});

describe("buildOrbitLayout", () => {
  it("keeps the core at the origin and peers just outside it, clear of the core and each other", () => {
    const { centroids, ringRadius } = buildOrbitLayout([
      ["communication", 14, true],
      ["learning", 11, true],
      ["reasoning", 15, true],
      ["collaboration", 24, true],
      ["delivery", 55, true],
      ["fusion", 13, true],
    ], "fusion");
    const core = centroids.get("fusion")!;
    expect(core).toMatchObject({ cx: 0, cy: 0 });
    const peers = ["communication", "learning", "reasoning", "collaboration", "delivery"].map((k) => centroids.get(k)!);
    for (const p of peers) {
      expect(Math.hypot(p.cx, p.cy)).toBeLessThanOrEqual(ringRadius + 1e-6);
      expect(Math.hypot(p.cx, p.cy)).toBeGreaterThan(core.r + p.r);
    }
    for (let i = 0; i < peers.length; i++) {
      const a = peers[i];
      const b = peers[(i + 1) % peers.length];
      expect(Math.hypot(a.cx - b.cx, a.cy - b.cy)).toBeGreaterThan(a.r + b.r);
    }
  });

  it("puts a lone cluster at the origin and scatters members within its radius (plus jitter)", () => {
    const { centroids } = buildOrbitLayout([["episode", 74, false]]);
    const c = centroids.get("episode")!;
    expect(c).toMatchObject({ cx: 0, cy: 0 });
    for (let j = 0; j < 74; j++) {
      const [x, y] = scatterOffset(j, 74, j * 7919 + 13, c.r, false);
      expect(Math.hypot(x, y)).toBeLessThan(c.r + 18);
    }
  });
});
