import { NextRequest, NextResponse } from "next/server";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { epStatePath } from "@/lib/ep-state-paths";
import { samePromptBinding } from "@/lib/prompt-binding";
import { findSyntheticRow, observeHost } from "@/lib/host-adapters.server";
import { findClaudeTranscript, indexClaudeTranscript } from "@/lib/claude-turn-index.server";

export const dynamic = "force-dynamic";

const PROMPT_INGRESS_PATH = process.env.EVOLVING_PROFILE_PROMPT_INGRESS_PATH || epStatePath("audit/prompt-ingress.jsonl");
const TOOL_ACTIVITY_PATH = process.env.EVOLVING_PROFILE_MCP_TOOL_ACTIVITY_PATH || epStatePath("audit/mcp-tool-activity.jsonl");
const ROUTE_DIR = process.env.EVOLVING_PROFILE_MEMORY_ROUTE_RECEIPTS_PATH || epStatePath("audit/memory-route-receipts");
const HOOK_LOG_PATH = process.env.EVOLVING_PROFILE_CLAUDE_HOOK_LOG || epStatePath("claude-code/desktop-hook.log");
const GUIDANCE_DIR = process.env.EVOLVING_PROFILE_GUIDANCE_ENTRY_RECEIPTS_PATH || epStatePath("audit/guidance-entry-receipts");

type Json = Record<string, any>;

async function newestMatching(directory: string, ingress: Json, at: (row: Json) => string) {
  let best: Json | null = null;
  try {
    for (const file of await readdir(directory)) {
      if (!file.endsWith(".json")) continue;
      let receipt: Json;
      try { receipt = JSON.parse(await readFile(path.join(directory, file), "utf8")); } catch { continue; }
      if (!samePromptBinding(ingress, receipt)) continue;
      if (!best || at(receipt).localeCompare(at(best)) > 0) best = receipt;
    }
  } catch { /* directory missing: stage stays unobserved */ }
  return best;
}

async function toolCalls(checkId: string) {
  if (!checkId) return [];
  try {
    const rows: Json[] = [];
    for (const line of (await readFile(TOOL_ACTIVITY_PATH, "utf8")).split("\n")) {
      if (!line.includes(checkId)) continue;
      try { const row = JSON.parse(line); if (row.check_id === checkId) rows.push(row); } catch { /* skip */ }
    }
    return rows.map((row) => {
      const started = Date.parse(row.invocation_started_at || row.at);
      const ended = Date.parse(row.at);
      return {
        id: row.tool_call_id || row.occurrence_id || `${row.tool}:${row.at}`,
        tool: row.tool, plane: row.plane ?? null, stage: row.stage ?? null,
        query: row.query || "", started_at: row.invocation_started_at || row.at, ended_at: row.at,
        duration_ms: Number.isNaN(started) || Number.isNaN(ended) ? null : Math.max(0, ended - started),
        candidate_count: row.candidate_count ?? null, returned_count: row.returned_count ?? null,
        source_read_count: row.source_read_count ?? null, research_id: row.research_id ?? null,
        memory_ids: Array.isArray(row.memory_ids) ? row.memory_ids.length : (row.memory_id ? 1 : 0),
        binding_state: row.binding_state ?? null,
        jev: row.jev_review ? { status: row.jev_review.status ?? null, latency_ms: row.jev_review.latency_ms ?? null, model: row.jev_review.model ?? null, effect: row.jev_review.effect ?? null } : null,
        relevance: row.relevance_audit ? { level: row.relevance_audit.effective_level ?? null, reasons: row.relevance_audit.reasons ?? [] } : null,
      };
    }).sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
  } catch { return []; }
}


// Hook-log events (recall = injection, retain = Stop write-back) for one Claude session.
async function hookEvents(sessionId: string) {
  const out: Json[] = [];
  if (!sessionId) return out;
  try {
    for (const line of (await readFile(HOOK_LOG_PATH, "utf8")).split("\n")) {
      if (!line.includes(sessionId)) continue;
      try { const r = JSON.parse(line); if (r.sid_full === sessionId && (r.event === "recall" || r.event === "retain")) out.push(r); } catch { /* skip */ }
    }
  } catch { /* log missing */ }
  return out;
}

async function hostTurn(ingress: Json, checkId: string) {
  const sessionId = String(ingress.session_id || ""), at = Date.parse(ingress.at);
  const events = await hookEvents(sessionId);
  const recall = events.filter((e) => e.event === "recall").map((e) => ({ e, d: Math.abs(Date.parse(e.ts) - at) })).filter((x) => x.d < 60000).sort((a, b) => a.d - b.d)[0]?.e ?? null;
  const retains = events.filter((e) => e.event === "retain" && Date.parse(e.ts) >= at - 1000).sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
  const file = await findClaudeTranscript(sessionId);
  let delivered: { at: string } | null = null, answer: Json | null = null, scan = "unavailable";
  if (file) {
    const { recs, complete } = await indexClaudeTranscript(file, Date.now() + 8000);
    scan = complete ? "complete" : "partial";
    const d = recs.find((r) => r.k === "d" && r.checkId === checkId);
    if (d) delivered = { at: new Date(d.ts).toISOString() };
    const users = recs.filter((r) => r.k === "u").map((r) => r.ts).sort((a, b) => a - b);
    const start = users.filter((ts) => Math.abs(ts - at) < 5 * 60000).sort((a, b) => Math.abs(a - at) - Math.abs(b - at))[0];
    if (start !== undefined) {
      const end = users.find((ts) => ts > start + 500) ?? Infinity;
      const mine = recs.filter((r) => r.k === "a" && r.ts >= start && r.ts < end) as Extract<typeof recs[number], { k: "a" }>[];
      if (mine.length) {
        const tools: Record<string, number> = {};
        for (const r of mine) for (const name of r.tools) tools[name] = (tools[name] ?? 0) + 1;
        const final = [...mine].reverse().find((r) => r.stop) ?? mine[mine.length - 1];
        answer = { steps: new Set(mine.map((r) => r.id ?? r.ts)).size, tools: Object.entries(tools).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count })),
          tool_calls: mine.reduce((n, r) => n + r.tools.length, 0), stop_reason: final.stop, final_chars: final.chars,
          started_at: new Date(start).toISOString(), ended_at: new Date(mine[mine.length - 1].ts).toISOString(), duration_ms: mine[mine.length - 1].ts - start, open: end === Infinity };
      }
    }
  }
  const written = retains.find((e) => e.rollout === "written");
  const lastRetain = retains[retains.length - 1];
  return {
    delivery: delivered ? { state: "confirmed", at: delivered.at, injected_chars: recall?.injected_chars ?? null, out_chars: recall?.out_chars ?? null, hook_ms: recall?.ms ?? null, evidence: "claude_native_transcript_attachment" }
      : recall ? { state: "prepared", at: recall.ts, injected_chars: recall.injected_chars ?? null, out_chars: recall.out_chars ?? null, hook_ms: recall.ms ?? null, evidence: "hook_log_recall_event", scan } : { state: "none", scan },
    answer: answer ? { state: answer.stop_reason === "end_turn" ? "done" : "open", ...answer } : { state: "none", scan },
    retain: written ? { state: "written", at: written.ts, rollout: "written", turns: written.turns ?? null, hook_ms: written.ms ?? null, adapter_rc: written.adapter_rc ?? null, events: retains.length }
      : lastRetain ? { state: "queued", at: lastRetain.ts, rollout: lastRetain.rollout ?? null, turns: lastRetain.turns ?? null, settle_remaining_s: lastRetain.settle_remaining_s ?? null, hook_ms: lastRetain.ms ?? null, adapter_rc: lastRetain.adapter_rc ?? null, events: retains.length } : { state: "none" },
  };
}

export async function GET(_request: NextRequest, context: { params: Promise<{ promptId: string }> }) {
  const { promptId } = await context.params;
  let ingress: Json | undefined;
  try {
    for (const line of (await readFile(PROMPT_INGRESS_PATH, "utf8")).trim().split("\n").reverse()) {
      if (!line.includes(promptId.split(":", 1)[0])) continue;
      try { const row = JSON.parse(line); if (`${row.prompt_fingerprint}:${row.at}` === promptId) { ingress = row; break; } } catch { /* skip */ }
    }
  } catch { /* ingress unavailable */ }
  if (!ingress) ingress = await findSyntheticRow(promptId);
  if (!ingress) return NextResponse.json({ error: "prompt_not_found" }, { status: 404 });

  const isClaude = String(ingress.host_id || "") === "claude-desktop" || String(ingress.source || "").startsWith("claude-desktop");
  const [route, guidance, calls, host] = await Promise.all([
    newestMatching(ROUTE_DIR, ingress, (r) => String(r.updated_at ?? r.at ?? "")),
    newestMatching(GUIDANCE_DIR, ingress, (r) => String(r.at ?? "")),
    toolCalls(String(ingress.hook_invocation_id || "")),
    isClaude ? hostTurn(ingress, String(ingress.hook_invocation_id || "")) : observeHost(ingress, String(ingress.hook_invocation_id || "")),
  ]);

  const result = guidance?.result ?? guidance?.rendered_guidance ?? {};
  const stable = (result.stable_profile ?? guidance?.rendered_guidance?.stable_profile ?? []) as Json[];
  const included = (result.included ?? guidance?.rendered_guidance?.included ?? []) as Json[];
  const candidates = (guidance?.rendered_guidance?.preference_candidates ?? result.preference_candidates ?? []) as Json[];
  const brief = (item: Json) => ({ id: item.id ?? null, text: String(item.text ?? item.title ?? "").slice(0, 260), applies_when: item.applies_when ?? [], exceptions: item.exceptions ?? [], status: item.status ?? null });

  return NextResponse.json({
    schema: "evolving-profile.prompt-trace.v1",
    prompt: { id: promptId, at: ingress.at, text: ingress.prompt_preview ?? "", host: ingress.host_id ?? "unknown", source: ingress.source ?? null, session_id: ingress.session_id ?? null, check_id: ingress.hook_invocation_id ?? null, hook_declared_origin: ingress.prompt_origin ?? null },
    guidance: guidance ? {
      host_state: guidance.host_visibility ?? null, coverage: guidance.coverage ?? result.coverage ?? null,
      stable_count: guidance.stable_profile_count ?? stable.length, candidate_count: guidance.preference_candidate_count ?? candidates.length,
      included_count: guidance.included_count ?? included.length, deferred_count: guidance.deferred_count ?? 0, context_chars: guidance.context_chars ?? null,
      stable: stable.slice(0, 6).map(brief), included: included.slice(0, 6).map(brief), at: guidance.at ?? null,
    } : null,
    route: route ? {
      decision: route.decision ?? null, recommended_route: route.recommended_route ?? null, history_dependency: route.history_dependency ?? null,
      reason: route.reason ?? null, required_ep_tool: route.required_ep_tool ?? null, memory_policy: route.memory_policy?.mode ?? null,
      focus_terms: route.focus_terms ?? [], matched_entities: route.catalog_probe?.matched_entities ?? [],
      catalog_hints: (route.catalog_hints ?? []).slice(0, 6).map((h: Json) => ({ title: h.title, source_count: h.source_count ?? null, boundary: h.boundary ?? null })),
      fallback_route: route.fallback_route ?? null,
    } : null,
    tool_calls: (host as any)?.tool_calls?.length ? (host as any).tool_calls : calls,
    delivery: host?.delivery ?? null, answer: host?.answer ?? null, retain: host?.retain ?? null,
  });
}
