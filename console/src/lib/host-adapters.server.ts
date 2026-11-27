import { createReadStream } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { createHash } from "node:crypto";
import path from "node:path";
import readline from "node:readline";
import { epStatePath, EP_HOST_SESSIONS, EP_STATE_ROOT, EP_DEFAULT_STATE_ROOT } from "@/lib/ep-state-paths";

// Host traces outside the state root are read only for the default install; an isolated
// EVOLVING_PROFILE_STATE_ROOT (tests, a second install) must not see this machine's real sessions.
const DEFAULT_INSTALL = EP_STATE_ROOT === EP_DEFAULT_STATE_ROOT;

// Per-host observers. Every host exposes different evidence; each adapter only claims what its
// own records prove. Unsupported steps return state "none" with a reason, never a guess.
type Json = Record<string, any>;
export type HostObservation = { delivery: Json; answer: Json; retain: Json; tool_calls?: Json[] };

const none = (reason: string) => ({ state: "none", reason });
const iso = (ms: number) => new Date(ms).toISOString();
const cacheTtl = <T,>(store: Map<string, { at: number; v: T }>, key: string, ms: number) => { const h = store.get(key); return h && Date.now() - h.at < ms ? h.v : undefined; };

// ───────── Codex: rollout files under ~/.codex/sessions ─────────
const rolloutPaths = new Map<string, { at: number; v: string | null }>();
const rolloutScans = new Map<string, { stamp: string; v: Json }>();
async function findRollout(sid: string): Promise<string | null> {
  const hit = cacheTtl(rolloutPaths, sid, 30000); if (hit !== undefined) return hit;
  let found: string | null = null;
  const roots = [EP_HOST_SESSIONS, process.env.EVOLVING_PROFILE_HOST_ARCHIVED_SESSIONS_ROOT || (DEFAULT_INSTALL ? path.join(homedir(), ".codex/archived_sessions") : epStatePath("archived-host-sessions"))];
  const walk = async (dir: string, depth: number): Promise<void> => {
    if (found || depth > 5) return;
    let entries; try { entries = await readdir(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries.sort((a, b) => b.name.localeCompare(a.name))) {
      if (found) return;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) await walk(p, depth + 1);
      else if (e.name.endsWith(`${sid}.jsonl`)) { found = p; return; }
    }
  };
  for (const r of roots) { await walk(r, 0); if (found) break; }
  rolloutPaths.set(sid, { at: Date.now(), v: found });
  return found;
}
async function scanRollout(file: string) {
  const info = await stat(file); const stamp = `${info.size}:${info.mtimeMs}`;
  const cached = rolloutScans.get(file); if (cached?.stamp === stamp) return cached.v;
  const turns = new Map<string, Json>(), delivered = new Map<string, number>(); const tools: { ts: number; name: string }[] = [];
  const rl = readline.createInterface({ input: createReadStream(file, { encoding: "utf8" }), crlfDelay: Infinity });
  const deadline = Date.now() + 9000; let complete = true;
  for await (const line of rl) {
    if (Date.now() > deadline) { complete = false; rl.close(); break; }
    const interesting = line.includes('"task_started"') || line.includes('"task_complete"') || line.includes('"function_call"') || line.includes('"custom_tool_call"') || line.includes("ep_prompt_binding check_id");
    if (!interesting) continue;
    try {
      const row = JSON.parse(line), p = row.payload ?? {}, ts = Date.parse(row.timestamp);
      if (p.type === "task_started") turns.set(p.turn_id, { ...(turns.get(p.turn_id) ?? {}), start: Number(p.started_at) * 1000 || ts });
      else if (p.type === "task_complete") turns.set(p.turn_id, { ...(turns.get(p.turn_id) ?? {}), done: Number(p.completed_at) * 1000 || ts, duration_ms: p.duration_ms ?? null, ttft_ms: p.time_to_first_token_ms ?? null, final_chars: String(p.last_agent_message ?? "").length });
      else if (p.type === "function_call" || p.type === "custom_tool_call") tools.push({ ts, name: String(p.name ?? p.type) });
      const m = /ep_prompt_binding check_id=\\*"([0-9a-f]{32})/.exec(line); if (m && !delivered.has(m[1])) delivered.set(m[1], ts);
    } catch { /* skip malformed */ }
  }
  const v = { turns, delivered, tools, complete }; rolloutScans.set(file, { stamp, v });
  return v;
}
async function observeCodex(ingress: Json, checkId: string): Promise<HostObservation> {
  const file = await findRollout(String(ingress.session_id || ""));
  if (!file) return { delivery: none("codex_rollout_missing"), answer: none("codex_rollout_missing"), retain: none("no_per_prompt_retain_receipt") };
  const s = await scanRollout(file);
  const d = s.delivered.get(checkId);
  const turn = s.turns.get(String(ingress.turn_id || ""));
  let answer: Json = none("turn_not_found_in_rollout");
  if (turn?.start) {
    const end = turn.done ?? Infinity; const mine = s.tools.filter((t: any) => t.ts >= turn.start && t.ts <= end);
    const counts: Record<string, number> = {}; for (const t of mine) counts[t.name] = (counts[t.name] ?? 0) + 1;
    answer = { state: turn.done ? "done" : "open", steps: mine.length, tool_calls: mine.length, stop_reason: turn.done ? "task_complete" : null, final_chars: turn.final_chars ?? null,
      duration_ms: turn.duration_ms ?? (turn.done ? turn.done - turn.start : null), ttft_ms: turn.ttft_ms, started_at: iso(turn.start), ended_at: turn.done ? iso(turn.done) : null,
      tools: Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count })) };
  }
  return { delivery: d ? { state: "confirmed", at: iso(d), evidence: "codex_rollout_contains_check_id" } : { state: "none", reason: "check_id_not_in_rollout", scan: s.complete ? "complete" : "partial" },
    answer, retain: none("codex_retain_has_no_per_prompt_receipt") };
}

// ───────── Trae: hook bridge log + EP-written transcript ─────────
const traeLog = new Map<string, { at: number; v: Json[] }>();
async function traeEvents(): Promise<Json[]> {
  const hit = cacheTtl(traeLog, "log", 8000); if (hit) return hit;
  const out: Json[] = [];
  try { for (const line of (await readFile(epStatePath("trae/logs/bridge.log"), "utf8")).split("\n")) { if (!line.startsWith("{")) continue; try { out.push(JSON.parse(line)); } catch { /* skip */ } } } catch { /* missing */ }
  traeLog.set("log", { at: Date.now(), v: out }); return out;
}
async function observeTrae(ingress: Json): Promise<HostObservation> {
  const sid = String(ingress.session_id || ""), at = Date.parse(ingress.at);
  const mine = (await traeEvents()).filter((e) => e.sid && sid.startsWith(String(e.sid)));
  const recall = mine.filter((e) => e.event === "recall").map((e) => ({ e, d: Math.abs(Date.parse(e.ts) - at) })).filter((x) => x.d < 60000).sort((a, b) => a.d - b.d)[0]?.e;
  const retains = mine.filter((e) => e.event === "retain" && Date.parse(e.ts) >= at - 1000).sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
  let answer: Json = none("trae_transcript_missing");
  try {
    const recs = (await readFile(String(ingress.transcript_path), "utf8")).split("\n").filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
    const prompt = String(ingress.prompt_preview || "").slice(0, 60);
    const idx = recs.findIndex((r: Json) => r.role === "user" && !String(r.content).startsWith("[tool_result") && String(r.content).replace(/\s+/g, " ").startsWith(prompt.replace(/\s+/g, " ")));
    if (idx >= 0) {
      let end = recs.length; for (let i = idx + 1; i < recs.length; i++) if (recs[i].role === "user" && !String(recs[i].content).startsWith("[tool_result")) { end = i; break; }
      const seg = recs.slice(idx + 1, end), calls: Record<string, number> = {}; let steps = 0, finalChars = 0;
      for (const r of seg) if (r.role === "assistant") { steps++; const m = /^\[tool_call (\S+)\]/.exec(String(r.content)); if (m) calls[m[1]] = (calls[m[1]] ?? 0) + 1; else finalChars = String(r.content).length; }
      const lastIsFinal = seg.length && seg[seg.length - 1].role === "assistant" && !/^\[tool_call /.test(String(seg[seg.length - 1].content));
      answer = { state: lastIsFinal ? "done" : "open", steps, tool_calls: Object.values(calls).reduce((a, b) => a + b, 0), stop_reason: lastIsFinal ? "final_reply" : null, final_chars: finalChars,
        duration_ms: retains[0] ? Math.max(0, Date.parse(retains[0].ts) - at) : null, started_at: ingress.at, ended_at: retains[0]?.ts ?? null, tools: Object.entries(calls).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count })) };
    }
  } catch { /* transcript unreadable */ }
  return {
    delivery: recall ? { state: "prepared", at: recall.ts, injected_chars: recall.injected_chars ?? null, out_chars: recall.out_chars ?? null, hook_ms: recall.ms ?? null, evidence: "trae_hook_bridge_log" } : none("no_recall_event_near_prompt"),
    answer, retain: retains[0] ? { state: "queued", at: retains[0].ts, rollout: "stop_hook", adapter_rc: retains[0].adapter_rc ?? null, hook_ms: retains[0].ms ?? null, events: retains.length } : none("no_stop_hook_after_prompt") };
}

// ───────── Doubao Work: EP call ledger (no hooks on this host) ─────────
async function doubaoLedger(): Promise<Json[]> {
  try { return (await readFile(epStatePath("doubao/call-ledger.jsonl"), "utf8")).split("\n").filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean); } catch { return []; }
}
function doubaoTaskRows(rows: Json[]) {
  const by = new Map<string, Json[]>(); for (const r of rows) if (r.check_id) by.set(r.check_id, [...(by.get(r.check_id) ?? []), r]);
  return [...by.entries()].map(([checkId, list]) => { const first = list.sort((a, b) => a.at - b.at)[0];
    return { at: iso(first.at * 1000), event: "prompt_ingress", session_id: first.session ?? checkId, turn_id: null, hook_invocation_id: checkId, host_id: "doubao_work", source: "doubao-ledger", prompt_origin: "host_reported",
      prompt_preview: `豆包工作任务 · ${list.map((x) => x.tool).filter((v, i, a) => a.indexOf(v) === i).join(" / ")} · ${String(first.query ?? "").slice(0, 60)}`, prompt_fingerprint: createHash("sha256").update(checkId).digest("hex").slice(0, 16) }; });
}
async function observeDoubao(ingress: Json): Promise<HostObservation> {
  const calls = (await doubaoLedger()).filter((r) => r.check_id === ingress.hook_invocation_id).sort((a, b) => a.at - b.at);
  const reason = "doubao_work_has_no_hook_only_call_ledger";
  return { delivery: none(reason), answer: none(reason), retain: none(reason),
    tool_calls: calls.map((r, i) => ({ id: `doubao-${r.at}-${i}`, tool: r.tool, plane: null, stage: null, query: r.query ?? "", started_at: iso(r.at * 1000), ended_at: iso(r.at * 1000), duration_ms: null, candidate_count: null, returned_count: r.records ?? null, source_read_count: null, research_id: null, memory_ids: 0, binding_state: r.ok ? "ledger_ok" : "ledger_failed", jev: null, relevance: null })) };
}

// ───────── WorkBuddy: OpenTelemetry-style agent traces ─────────
const WB_ROOT = process.env.EVOLVING_PROFILE_WORKBUDDY_TRACES_ROOT || (DEFAULT_INSTALL ? path.join(homedir(), ".workbuddy/traces") : epStatePath("workbuddy-traces"));
const wbCache = new Map<string, { stamp: string; v: Json | null }>();
async function readWbTrace(file: string): Promise<Json | null> {
  const info = await stat(file).catch(() => null); if (!info) return null; const stamp = `${info.size}:${info.mtimeMs}`;
  const hit = wbCache.get(file); if (hit?.stamp === stamp) return hit.v;
  let v: Json | null = null;
  try {
    let traceId = "", started = "", ended = "", query = "", model = "", gens = 0, errors = 0, deliveredExact = false, deliveredAny = false; const tools: Record<string, number> = {}; let toolCalls = 0;
    for (const line of (await readFile(file, "utf8")).split("\n")) {
      if (!line) continue; let d: Json; try { d = JSON.parse(line); } catch { continue; }
      if (d.type === "trace_start") { traceId = d.trace?.traceId ?? ""; started = d.trace?.startedAt ?? d.trace?.createdAt ?? started; }
      if (d.type === "trace_end") ended = d.trace?.endedAt ?? ended;
      if (d.type !== "span_end") continue;
      const sp = d.span ?? {}; if (!started || sp.startedAt < started) started = sp.startedAt; if (sp.endedAt && sp.endedAt > ended) ended = sp.endedAt; if (sp.status === "error") errors++;
      if (sp.type === "generation") { gens++; const inp = String(d.toolInput ?? ""); if (!model) { const mm = /powered by ([^\n\\]+)/.exec(inp); if (mm) model = mm[1].trim(); }
 if (!query) { const m = /<user_query>\s*([\s\S]*?)\s*<\/user_query>/.exec(inp); if (m) query = m[1]; } if (inp.includes("ep_prompt_binding")) deliveredExact = true; if (/evolving[_ ]profile/i.test(inp)) deliveredAny = true; }
      else if (sp.type === "function") { toolCalls++; tools[sp.name] = (tools[sp.name] ?? 0) + 1; }
    }
    if (traceId && started) v = { traceId, started, ended, query, model, gens, errors, toolCalls, tools, deliveredExact, deliveredAny, file };
  } catch { /* unreadable */ }
  wbCache.set(file, { stamp, v }); return v;
}
async function workbuddyRows() {
  const files: { f: string; m: number }[] = [];
  let dirs: string[] = []; try { dirs = await readdir(WB_ROOT); } catch { return []; }
  for (const dir of dirs) { let names: string[] = []; try { names = await readdir(path.join(WB_ROOT, dir)); } catch { continue; } for (const f of names) { if (!f.endsWith(".jsonl")) continue; const p = path.join(WB_ROOT, dir, f); const s = await stat(p).catch(() => null); if (s) files.push({ f: p, m: s.mtimeMs }); } }
  const rows: Json[] = [];
  for (const { f } of files.sort((a, b) => b.m - a.m).slice(0, 40)) {
    const t = await readWbTrace(f); if (!t || (!t.gens && !t.toolCalls)) continue;
    const lead = Object.keys(t.tools).slice(0, 2).join(" / ");
    const label = t.query ? t.query.replace(/\s+/g, " ").slice(0, 200) : `WorkBuddy 工作流 · ${t.model || "模型未知"} · ${t.gens} 次生成 · ${t.toolCalls} 次工具调用${lead ? ` · ${lead}` : ""}（Trace 不含用户原文）`;
    rows.push({ at: t.started, event: "prompt_ingress", session_id: t.traceId, turn_id: null, hook_invocation_id: t.traceId, host_id: "workbuddy", source: "workbuddy-trace", transcript_path: null, wb_trace_file: f, prompt_origin: "host_reported",
      prompt_preview: label, prompt_fingerprint: createHash("sha256").update(t.traceId).digest("hex").slice(0, 16) });
  }
  return rows;
}
async function observeWorkbuddy(ingress: Json): Promise<HostObservation> {
  const file = String(ingress.wb_trace_file || ""); const t = file ? await readWbTrace(file) : null;
  if (!t) return { delivery: none("workbuddy_trace_missing"), answer: none("workbuddy_trace_missing"), retain: none("workbuddy_no_retain_receipt") };
  const dur = t.ended ? Date.parse(t.ended) - Date.parse(t.started) : null;
  return { delivery: t.deliveredExact ? { state: "confirmed", at: t.started, evidence: "workbuddy_trace_generation_input_has_check_id" } : t.deliveredAny ? { state: "prepared", at: t.started, evidence: "workbuddy_trace_mentions_ep_but_no_check_id" } : none("ep_context_not_seen_in_generation_input"),
    answer: { state: t.ended && !t.errors ? "done" : t.errors ? "open" : "open", steps: t.gens, tool_calls: t.toolCalls, stop_reason: t.errors ? `${t.errors} span error(s)` : t.ended ? "trace_end" : null, final_chars: null, duration_ms: dur, started_at: t.started, ended_at: t.ended || null,
      tools: Object.entries(t.tools as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count })) },
    retain: none("workbuddy_no_retain_receipt") };
}

export const SYNTHETIC_SOURCES = ["workbuddy-trace", "doubao-ledger"];
export async function syntheticHostRows(): Promise<Json[]> { const [wb, db] = await Promise.all([workbuddyRows(), doubaoLedger().then(doubaoTaskRows)]); return [...wb, ...db]; }
export async function findSyntheticRow(promptId: string): Promise<Json | undefined> { return (await syntheticHostRows()).find((r) => `${r.prompt_fingerprint}:${r.at}` === promptId); }

export async function observeHost(ingress: Json, checkId: string): Promise<HostObservation | null> {
  const host = String(ingress.host_id || ""), src = String(ingress.source || "");
  if (host === "codex" || (src === "codex-userpromptsubmit" && host === "codex")) return observeCodex(ingress, checkId);
  if (host === "trae") return observeTrae(ingress);
  if (host === "doubao_work") return observeDoubao(ingress);
  if (host === "workbuddy") return observeWorkbuddy(ingress);
  return null;
}
