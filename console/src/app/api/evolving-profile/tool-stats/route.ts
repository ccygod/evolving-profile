import { NextRequest, NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import { epStatePath } from "@/lib/ep-state-paths";

export const dynamic = "force-dynamic";
const TOOL_ACTIVITY_PATH = process.env.EVOLVING_PROFILE_MCP_TOOL_ACTIVITY_PATH || epStatePath("audit/mcp-tool-activity.jsonl");
let cache: { key: string; at: number; body: any } | null = null;

// Aggregates real MCP tool calls per tool over a time window (default 24h).
export async function GET(request: NextRequest) {
  const hours = Math.min(24 * 30, Math.max(1, Number(request.nextUrl.searchParams.get("hours") || 24)));
  const key = String(hours);
  if (cache && cache.key === key && Date.now() - cache.at < 15000) return NextResponse.json(cache.body);
  const since = Date.now() - hours * 3600 * 1000;
  const tools: Record<string, { calls: number; returned: number; total_ms: number; timed: number; jev_failed: number; last_at: string | null }> = {};
  let total = 0, prompts = new Set<string>();
  try {
    for (const line of (await readFile(TOOL_ACTIVITY_PATH, "utf8")).split("\n")) {
      if (!line) continue;
      // Cheap pre-filter on the leading timestamp before parsing the full JSON row.
      const m = /^\{"at": "([^"]+)"/.exec(line);
      if (m && Date.parse(m[1]) < since) continue;
      let row: any; try { row = JSON.parse(line); } catch { continue; }
      const at = Date.parse(row.at); if (Number.isNaN(at) || at < since || !row.tool) continue;
      const t = (tools[row.tool] ??= { calls: 0, returned: 0, total_ms: 0, timed: 0, jev_failed: 0, last_at: null });
      t.calls++; total++; t.returned += Number(row.returned_count ?? 0) || 0;
      const started = Date.parse(row.invocation_started_at || row.at);
      if (!Number.isNaN(started)) { t.total_ms += Math.max(0, at - started); t.timed++; }
      if (row.jev_review && row.jev_review.status && row.jev_review.status !== "ok") t.jev_failed++;
      if (!t.last_at || row.at > t.last_at) t.last_at = row.at;
      if (row.check_id) prompts.add(row.check_id);
    }
  } catch { /* file missing: empty stats */ }
  const body = { schema: "evolving-profile.tool-stats.v1", hours, total_calls: total, prompts_with_calls: prompts.size,
    tools: Object.fromEntries(Object.entries(tools).map(([k, v]) => [k, { calls: v.calls, returned: v.returned, avg_ms: v.timed ? Math.round(v.total_ms / v.timed) : null, jev_failed: v.jev_failed, last_at: v.last_at }])) };
  cache = { key, at: Date.now(), body };
  return NextResponse.json(body);
}
