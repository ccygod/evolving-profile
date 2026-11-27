"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ArrowDownToLine, Boxes, Braces, ChevronDown, Clock3, Eye, FileSearch, FileText, GitFork, Globe, Heart, History, Layers, Link2, MessageSquare, Package, Route as RouteIcon, Save, Scale, Send, ShieldCheck, ShieldQuestion, Sparkles, Telescope, Database, Bot } from "lucide-react";

type Json = Record<string, any>;
type Lane = "trunk" | "user" | "agent" | "rag" | "out";
type NState = "done" | "partial" | "idle" | "none";
type Kind = "ingress" | "hook" | "route" | "fork" | "pref" | "context" | "deliver" | "answer" | "retain" | "pack" | "tool" | "static";

type NodeDef = { id: string; lane: Lane; kind: Kind; x: number; y: number; w: number; h: number; icon: any; zh: string; en: string; subZh: string; subEn: string; descZh: string; descEn: string; tools?: string[]; children?: string[]; follow?: string };

const LANE: Record<Lane, { tone: string; zh: string; en: string }> = {
  trunk: { tone: "#6366f1", zh: "入口主干", en: "Trunk" }, user: { tone: "#14b8a6", zh: "用户记忆", en: "User memory" },
  agent: { tone: "#f59e0b", zh: "智能体过程记忆", en: "Agent process memory" }, rag: { tone: "#38bdf8", zh: "外部 RAG", en: "External RAG" }, out: { tone: "#8b5cf6", zh: "使用与沉淀", en: "Use & retain" },
};
const DIMENSIONS = [["沟通与呈现", "Communication", "#6366f1"], ["学习与理解", "Learning", "#14b8a6"], ["分析与决策", "Analysis", "#f59e0b"], ["执行与协作", "Execution", "#ec4899"], ["质量与交付", "Delivery", "#38bdf8"]] as const;
const HOSTS: Record<string, [string, string]> = { "claude-desktop": ["Claude 桌面端", "Claude desktop"], "claude-code": ["Claude Code 命令行", "Claude Code CLI"], codex: ["Codex", "Codex"], trae: ["Trae", "Trae"], hermes: ["Hermes", "Hermes"], antigravity: ["Antigravity", "Antigravity"], unknown: ["未知宿主", "Unknown host"] };

const N = (id: string, lane: Lane, kind: Kind, x: number, y: number, icon: any, zh: string, subZh: string, descZh: string, en: string, subEn: string, descEn: string, extra: Partial<NodeDef> = {}): NodeDef => ({ id, lane, kind, x, y, w: 140, h: 56, icon, zh, en, subZh, subEn, descZh, descEn, ...extra });
const C = [214, 376, 538, 700, 862];
const NODES: NodeDef[] = [
  N("ingress", "trunk", "ingress", 20, 30, ArrowDownToLine, "Prompt 入口", "UserPromptSubmit", "宿主把用户这句话交给 EP 的第一站，记录时间、宿主、会话并核验是否真人输入。", "Prompt ingress", "UserPromptSubmit", "First stop: records time, host and session, and verifies the prompt is human.", { w: 150, h: 58 }),
  N("hook", "trunk", "hook", 20, 130, Link2, "Hook 绑定", "check_id 贯穿", "给这一轮生成唯一 check_id，之后所有调用都靠它对应回这条 Prompt。", "Hook binding", "check_id threads all", "Issues a check_id; every later call is tied back to this prompt through it.", { w: 150, h: 58 }),
  N("route", "trunk", "route", 20, 230, RouteIcon, "记忆路由", "memory_check", "判断这句话需不需要查历史、该走召回还是研究，只是建议，不是授权。", "Memory routing", "memory_check", "Decides whether history is needed and whether to recall or research. Advisory only.", { w: 150, h: 58, tools: ["memory_check"] }),
  N("fork", "trunk", "fork", 62, 367, GitFork, "分叉", "", "按需分到三条记忆路线，也可以一条都不走。", "Fork", "", "Fans out to up to three memory routes, or none.", { w: 66, h: 66 }),

  N("u-root", "user", "static", C[0], 130, Database, "用户记忆库", "事实·经历·偏好", "用户的事实、经历、实体、条件化偏好和情景摘要，可用 catalog 目录导航。", "User memory bank", "facts · experiences · prefs", "Facts, experiences, entities, conditional preferences and scenario summaries; navigable via catalog.", { h: 60, tools: ["catalog_list", "catalog_search", "catalog_read"] }),
  N("u-pref", "user", "pref", C[1], 52, Heart, "多维度偏好", "", "按五个维度提供协作偏好：沟通、理解、分析、协作、交付。注入的是参考，不扩张授权。", "Multi-dimension preferences", "", "Collaboration preferences along five dimensions. Injected as reference, never as authorization.", { tools: ["user_preference", "read_preference", "read_preference_unit"] }),
  N("u-recall", "user", "tool", C[1], 130, History, "历史召回", "user_recall", "单点历史事实、旧决定、经验的检索。", "History recall", "user_recall", "Targeted lookup of a past fact, decision or experience.", { tools: ["user_recall"] }),
  N("u-research", "user", "tool", C[1], 200, Telescope, "深度研究 / 全量", "user_research · exhaustive", "多对象、时间线、跨项目的开放式盘点，含 100% SQL 全量无截断扫描。", "Deep & exhaustive research", "user_research · exhaustive", "Open-ended & 100% SQL exhaustive inventory across objects, timelines and projects.", { tools: ["user_research", "read_research", "ep_exhaustive_search"] }),
  N("u-scenario", "user", "tool", C[2], 130, FileText, "情景摘要", "scenario", "项目或会话归属不清时，先看情景摘要再决定读哪几条。", "Scenario summary", "scenario", "When project/session ownership is unclear, check scenario summaries before reading sources.", { tools: ["search_scenario_summary", "read_scenario_summary", "scenario_gate"] }),
  N("u-source", "user", "tool", C[3], 130, FileSearch, "原文回读", "read_source", "关键结论回到原文逐字核验，摘要不能当证据。", "Source readback", "read_source", "Key claims are verified against the verbatim source; summaries are not evidence.", { h: 60, tools: ["read_source", "find_sources"] }),
  N("u-pack", "user", "pack", C[4], 130, Package, "用户记忆包", "证据 + 偏好", "这条路线最终交给上下文组装的内容。", "User memory pack", "evidence + prefs", "What this route finally hands to context assembly.", { h: 60, children: ["u-pref", "u-recall", "u-research", "u-scenario", "u-source"] }),

  N("a-root", "agent", "static", C[0], 370, Bot, "过程记忆库", "做事的经验", "智能体过去怎么执行、踩过什么坑、怎么修复的经验库。", "Process memory bank", "how work was done", "Experience of how the agent executed, what went wrong and how it was fixed.", { h: 60 }),
  N("a-recall", "agent", "tool", C[1], 312, History, "过程召回", "agent_recall", "检索过去类似任务的执行路径与教训。", "Process recall", "agent_recall", "Finds execution paths and lessons from similar past tasks.", { h: 52, tools: ["agent_recall", "read_agent_process_memory"] }),
  N("a-research", "agent", "tool", C[1], 374, Telescope, "过程研究", "agent_research", "跨任务比较过程模式。", "Process research", "agent_research", "Compares process patterns across tasks.", { h: 52, tools: ["agent_research"] }),
  N("a-observe", "agent", "static", C[1], 436, Eye, "过程观察", "能力·失败·修复", "把能力观测、失败事件、修复模式合并展示；沉淀可复用的过程策略。", "Process observation", "capability · failure · fix", "Capability observations, failure events and repair patterns, merged; yields reusable strategies.", { h: 52 }),
  N("a-gate", "agent", "static", C[2], 374, ShieldCheck, "迁移与兼容门控", "再验证", "旧经验换到新任务前，先验证是否兼容。", "Transfer & compatibility gate", "re-validation", "Old experience is re-validated before being applied to a new task.", { h: 52 }),
  N("a-strategy", "agent", "static", C[3], 374, Sparkles, "提示·建议·防护", "脚手架", "把经验变成提示、建议、脚手架和防护。", "Hints · advice · guardrails", "scaffolds", "Turns experience into hints, advice, scaffolds and guardrails.", { h: 52 }),
  N("a-pack", "agent", "pack", C[4], 370, Package, "过程记忆包", "策略", "这条路线最终交给上下文组装的内容。", "Process memory pack", "strategies", "What this route finally hands to context assembly.", { h: 60, children: ["a-recall", "a-research"] }),

  N("r-root", "rag", "static", C[0], 590, Globe, "外部知识源", "文档·网页·库", "用户之外的外部资料。", "External sources", "docs · web · corpora", "Material that lives outside the user's own memory.", { h: 60 }),
  N("r-search", "rag", "tool", C[1], 590, FileSearch, "混合检索", "词法 + 向量", "关键词检索与向量检索并行。", "Hybrid retrieval", "lexical + vector", "Keyword and vector retrieval run in parallel.", { h: 60, tools: ["rag_search", "personal_record_search"] }),
  N("r-fuse", "rag", "static", C[2], 590, Layers, "融合 · 重排", "RRF + Rerank", "把两路结果融合并重排。", "Fuse & rerank", "RRF + rerank", "Fuses both result lists and reranks them.", { h: 60, follow: "r-search" }),
  N("r-jev", "rag", "static", C[3], 590, Scale, "JEV 审查", "证据与范围", "审查证据是否站得住、范围是否匹配。", "JEV review", "evidence & scope", "Reviews whether evidence holds up and scope matches.", { h: 60, follow: "r-search" }),
  N("r-pack", "rag", "pack", C[4], 590, Package, "RAG 知识包", "外部证据", "这条路线最终交给上下文组装的内容。", "RAG knowledge pack", "external evidence", "What this route finally hands to context assembly.", { h: 60, children: ["r-search"] }),

  N("context", "out", "context", 1070, 140, Boxes, "上下文组装", "三包合一", "把三条路线的记忆包按优先级和预算合并成一段上下文。", "Context assembly", "three packs → one", "Merges the three packs into one context by priority and budget.", { w: 160, h: 64 }),
  N("deliver", "out", "deliver", 1070, 270, Send, "送达智能体", "宿主确认", "上下文是否真的进入了智能体：在 Claude 自己的会话记录里找带本轮 check_id 的内容来确认。", "Delivered to agent", "host confirmation", "Whether the context reached the agent needs a host receipt.", { w: 160, h: 64 }),
  N("answer", "out", "answer", 1070, 400, MessageSquare, "回答 / 执行", "", "智能体据此回答或执行。数据来自 Claude 自己的会话记录：步数、工具调用、耗时、结束状态。", "Answer / action", "", "The agent answers or acts. Taken from Claude's own transcript: steps, tool calls, duration, finish state.", { w: 160, h: 64 }),
  N("retain", "out", "retain", 1070, 530, Save, "写回沉淀", "retain", "一轮结束后由 Stop Hook 把这一轮写回，形成闭环。依据是 Stop Hook 的写回事件。", "Retain", "write back", "Writes new facts and experience back to the bank, closing the loop.", { w: 160, h: 64 }),
];
const EDGES: [string, string][] = [["ingress", "hook"], ["hook", "route"], ["route", "fork"], ["fork", "u-root"], ["fork", "a-root"], ["fork", "r-root"],
  ["u-root", "u-pref"], ["u-root", "u-recall"], ["u-root", "u-research"], ["u-recall", "u-scenario"], ["u-research", "u-scenario"], ["u-scenario", "u-source"], ["u-pref", "u-pack"], ["u-source", "u-pack"],
  ["a-root", "a-recall"], ["a-root", "a-research"], ["a-root", "a-observe"], ["a-recall", "a-gate"], ["a-research", "a-gate"], ["a-observe", "a-gate"], ["a-gate", "a-strategy"], ["a-strategy", "a-pack"],
  ["r-root", "r-search"], ["r-search", "r-fuse"], ["r-fuse", "r-jev"], ["r-jev", "r-pack"],
  ["u-pack", "context"], ["a-pack", "context"], ["r-pack", "context"], ["context", "deliver"], ["deliver", "answer"], ["answer", "retain"]];
const LANES_BOX: { lane: Lane; x: number; y: number; w: number; h: number; zh: string; en: string; paths: number }[] = [
  { lane: "user", x: 196, y: 16, w: 824, h: 250, zh: "A · 用户记忆", en: "A · User memory", paths: 5 },
  { lane: "agent", x: 196, y: 278, w: 824, h: 232, zh: "B · 智能体过程记忆", en: "B · Agent process memory", paths: 3 },
  { lane: "rag", x: 196, y: 522, w: 824, h: 160, zh: "C · 外部 RAG", en: "C · External RAG", paths: 1 },
];
const W = 1250, H = 700;
const BYID = Object.fromEntries(NODES.map((n) => [n.id, n]));
const ALL_TOOLS = Array.from(new Set(NODES.flatMap((n) => n.tools ?? [])));

const t = (en: boolean, zh: string, e: string) => (en ? e : zh);
const REASONS: Record<string, [string, string]> = {
  doubao_work_has_no_hook_only_call_ledger: ["豆包工作没有 Hook，EP 只能看到它发起的工具调用台账，所以这个环节无法观测。", "Doubao Work has no hooks; EP only sees its tool-call ledger, so this step cannot be observed."],
  codex_retain_has_no_per_prompt_receipt: ["Codex 的写回由后台增量 worker 按会话处理，目前没有逐条 Prompt 的回执。", "Codex retain is handled per session by a background worker; no per-prompt receipt yet."],
  workbuddy_no_retain_receipt: ["WorkBuddy 的 Trace 里没有写回记录。", "WorkBuddy traces carry no retain record."],
  no_stop_hook_after_prompt: ["这条 Prompt 之后还没有看到 Stop Hook。", "No Stop hook seen after this prompt."],
  no_recall_event_near_prompt: ["Hook 日志里没有找到这条 Prompt 附近的注入记录。", "No injection event near this prompt in the hook log."],
  check_id_not_in_rollout: ["Codex 会话记录里没有找到本轮 check_id，说明上下文可能没有注入。", "This turn's check_id is not in the Codex rollout, so context may not have been injected."],
  ep_context_not_seen_in_generation_input: ["WorkBuddy 的模型输入里没有 EP 上下文。", "No EP context in WorkBuddy's model input."],
};
const EVIDENCE: Record<string, [string, string]> = {
  claude_native_transcript_attachment: ["在 Claude 自己的会话记录里找到了带本轮 check_id 的 Hook 上下文，说明内容确实进入了智能体。", "Found the hook context with this turn's check_id in Claude's own transcript."],
  codex_rollout_contains_check_id: ["在 Codex 的会话记录（rollout）里找到了带本轮 check_id 的内容，说明上下文确实进入了智能体。", "Found this turn's check_id in the Codex rollout."],
  workbuddy_trace_generation_input_has_check_id: ["在 WorkBuddy 的模型输入里找到了本轮 check_id。", "Found this turn's check_id in WorkBuddy's model input."],
  workbuddy_trace_mentions_ep_but_no_check_id: ["WorkBuddy 的模型输入里有 EP 内容，但没有本轮 check_id，只能算已准备。", "EP content appears in WorkBuddy's model input without this turn's check_id; counted as prepared."],
  trae_hook_bridge_log: ["只在 Trae 的 Hook 日志里看到生成记录，没有宿主侧收到的证据。", "Only Trae's hook log shows it was produced; no host-side proof of receipt."],
  hook_log_recall_event: ["只在 Hook 日志里看到生成记录，Claude 会话记录里没找到对应内容。", "Only the hook log shows it was produced; Claude's transcript has no matching content."],
};
const reasonText = (en: boolean, code?: string) => (code && REASONS[code] ? REASONS[code][en ? 1 : 0] : code ?? "");
const fmtTime = (iso?: string) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleString(undefined, { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"; };
const ms = (v?: number | null) => (typeof v === "number" ? (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`) : "—");
const STATE_TEXT: Record<NState, [string, string]> = { done: ["已触发", "Triggered"], partial: ["部分确认", "Partial"], idle: ["本轮未触发", "Not triggered"], none: ["无回执", "No receipt"] };

function edgePath(a: NodeDef, b: NodeDef) {
  const acx = a.x + a.w / 2, bcx = b.x + b.w / 2;
  if (Math.abs(acx - bcx) < 20 && b.y >= a.y + a.h - 4) {
    return `M ${acx} ${a.y + a.h} L ${bcx} ${b.y}`;
  }
  if (a.id === "u-pref" && b.id === "u-pack") {
    const sx = a.x + a.w, sy = a.y + a.h / 2, tx = b.x + b.w / 2, ty = b.y;
    const r = 16;
    return `M ${sx} ${sy} L ${tx - r} ${sy} Q ${tx} ${sy} ${tx} ${sy + r} L ${tx} ${ty}`;
  }
  const sx = a.x + a.w, sy = a.y + a.h / 2, tx = b.x, ty = b.y + b.h / 2;
  const gap = tx - sx;
  const dx = gap > 0 ? gap * 0.5 : Math.max(24, Math.abs(gap) * 0.35);
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
}
function Fact({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) { return <div className="pj-fact"><dt>{label}</dt><dd className={mono ? "font-mono text-[11px]" : ""}>{value ?? "—"}</dd></div>; }
function Chip({ children, tone }: { children: React.ReactNode; tone?: "ok" | "warn" | "mute" | "info" }) { return <span className={`pj-chip pj-chip-${tone ?? "mute"}`}>{children}</span>; }

export function PromptJourney({ row, english }: { row: Json; english: boolean }) {
  const [data, setData] = useState<Json | null>(null);
  const [stats, setStats] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"prompt" | "stats">("prompt");
  const [sel, setSel] = useState<string | null>(null);
  const [openCall, setOpenCall] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const box = useRef<HTMLDivElement>(null);
  const promptId = String(row?.prompt_id ?? "");

  useEffect(() => {
    if (!promptId) return;
    const c = new AbortController();
    setData(null); setError(null); setOpenCall(null);
    fetch(`/api/evolving-profile/prompt-trace/${encodeURIComponent(promptId)}`, { cache: "no-store", signal: c.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status))))).then(setData)
      .catch((e) => { if (e?.name !== "AbortError") setError(String(e?.message || e)); });
    return () => c.abort();
  }, [promptId]);
  useEffect(() => {
    const c = new AbortController();
    fetch("/api/evolving-profile/tool-stats?hours=24", { cache: "no-store", signal: c.signal }).then((r) => (r.ok ? r.json() : null)).then(setStats).catch(() => {});
    return () => c.abort();
  }, []);
  useEffect(() => {
    const el = box.current; if (!el) return;
    const fit = () => setScale(Math.max(0.42, Math.min(1, el.clientWidth / W)));
    fit(); const ro = new ResizeObserver(fit); ro.observe(el); return () => ro.disconnect();
  }, [data]);

  const calls: Json[] = data?.tool_calls ?? [];
  const states = useMemo(() => {
    const out: Record<string, { state: NState; calls: Json[]; badge?: string }> = {};
    if (!data) return out;
    const g = data.guidance, hasRoute = Boolean(data.route);
    const delivered = g && ["observed", "received", "delivered"].includes(String(g.host_state));
    const callsFor = (tools?: string[]) => (tools?.length ? calls.filter((c) => tools.includes(c.tool)) : []);
    for (const n of NODES) {
      if (mode === "stats") {
        const used = (n.tools ?? []).map((x) => stats?.tools?.[x]).filter(Boolean);
        const total = used.reduce((s: number, x: Json) => s + x.calls, 0);
        const avg = used.length ? Math.round(used.reduce((s: number, x: Json) => s + (x.avg_ms ?? 0) * x.calls, 0) / Math.max(1, total)) : null;
        out[n.id] = { state: total ? "done" : n.tools?.length ? "idle" : "none", calls: [], badge: total ? `${total}${t(english, " 次", "×")} · ${ms(avg)}` : undefined };
        continue;
      }
      const cs = callsFor(n.tools);
      let state: NState = "none";
      if (n.kind === "ingress") state = "done";
      else if (n.kind === "hook") state = data.prompt.check_id ? "done" : "partial";
      else if (n.kind === "route") state = hasRoute ? "done" : "none";
      else if (n.kind === "fork") state = hasRoute || calls.length ? "done" : "none";
      else if (n.kind === "pref") state = cs.length ? "done" : g ? (delivered ? "done" : "partial") : hasRoute ? "idle" : "none";
      else if (n.kind === "tool") state = cs.length ? "done" : hasRoute ? "idle" : "none";
      else if (n.kind === "static" && n.tools) state = cs.length ? "done" : hasRoute ? "idle" : "none";
      else if (n.kind === "context") state = g ? (g.context_chars ? "done" : "partial") : "none";
      else if (n.kind === "deliver") state = data.delivery ? (data.delivery.state === "confirmed" ? "done" : data.delivery.state === "prepared" ? "partial" : "none") : g ? (delivered ? "done" : "partial") : "none";
      else if (n.kind === "answer") state = data.answer ? (data.answer.state === "done" ? "done" : data.answer.state === "open" ? "partial" : "none") : "none";
      else if (n.kind === "retain") state = data.retain ? (data.retain.state === "written" ? "done" : data.retain.state === "queued" ? "partial" : "none") : "none";
      let hostBadge: string | undefined;
      if (n.kind === "deliver" && data.delivery?.injected_chars) hostBadge = `${data.delivery.injected_chars}${t(english, " 字", " ch")}`;
      if (n.kind === "answer" && data.answer?.steps) hostBadge = `${data.answer.steps}${t(english, " 步 · ", " steps · ")}${ms(data.answer.duration_ms)}`;
      if (n.kind === "retain" && data.retain?.state !== "none" && data.retain) hostBadge = data.retain.state === "written" ? t(english, "已写入", "written") : t(english, "排队中", "queued");
      out[n.id] = { state, calls: cs, badge: hostBadge ?? (cs.length ? `${cs.length}${t(english, " 次", "×")} · ${ms(cs.reduce((s, c) => s + (c.duration_ms ?? 0), 0))}` : undefined) };
    }
    for (const n of NODES) {
      if (n.follow) { const p = out[n.follow]; out[n.id] = { state: p?.state === "done" ? "partial" : hasRoute ? "idle" : "none", calls: [] }; }
      if (n.kind === "pack") { const kids = (n.children ?? []).map((id) => out[id]?.state); out[n.id] = { state: kids.some((s) => s === "done") ? "done" : kids.some((s) => s === "partial") ? "partial" : hasRoute ? "idle" : "none", calls: [] }; }
    }
    return out;
  }, [data, calls, mode, stats, english]);

  if (error) return <div className="pj-root p-8 text-center text-sm text-muted-foreground">{t(english, "无法读取这条 Prompt 的链路记录。", "Could not read this prompt's trace.")} ({error})</div>;
  if (!data) return <div className="pj-root p-8"><div className="pj-skeleton h-6 w-2/3" /><div className="pj-skeleton mt-4 h-24 w-full" /><div className="pj-skeleton mt-4 h-64 w-full" /></div>;

  const origin = row?.origin_status === "verified";
  const touched = NODES.filter((n) => n.kind !== "pack" && ["done", "partial"].includes(states[n.id]?.state)).length;
  const selected = sel ? BYID[sel] : null;
  const sState = selected ? states[selected.id] : undefined;
  const span = calls.length ? { t0: Math.min(...calls.map((c) => Date.parse(c.started_at))), total: Math.max(1, Math.max(...calls.map((c) => Date.parse(c.ended_at))) - Math.min(...calls.map((c) => Date.parse(c.started_at)))) } : null;
  const g = data.guidance, r = data.route;

  const callRows = (list: Json[]) => (
    <ol className="pj-waterfall">{list.map((c) => {
      const tone = LANE[(NODES.find((n) => n.tools?.includes(c.tool))?.lane ?? "user")].tone;
      const left = span ? ((Date.parse(c.started_at) - span.t0) / span.total) * 100 : 0, width = span ? Math.max(2.5, ((c.duration_ms ?? 0) / span.total) * 100) : 100, open = openCall === c.id;
      return <li key={c.id}>
        <button type="button" className="pj-call" onClick={() => setOpenCall(open ? null : c.id)} aria-expanded={open}>
          <span className="pj-call-name"><i style={{ background: tone }} />{c.tool}</span>
          <span className="pj-track"><b style={{ left: `${Math.min(left, 97)}%`, width: `${Math.min(width, 100 - Math.min(left, 97))}%`, background: `linear-gradient(90deg, ${tone}, color-mix(in oklab, ${tone} 55%, white))` }} /></span>
          <span className="pj-call-meta">{ms(c.duration_ms)}<ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} /></span>
        </button>
        {open ? <div className="pj-call-detail"><dl className="pj-facts">
          <Fact label={t(english, "候选 / 返回 / 回读", "Cand / ret / read")} value={`${c.candidate_count ?? "—"} / ${c.returned_count ?? "—"} / ${c.source_read_count ?? "—"}`} />
          <Fact label={t(english, "相关度档位", "Relevance")} value={c.relevance?.level ?? "—"} />
          <Fact label="JEV" value={c.jev ? `${c.jev.status} · ${ms(c.jev.latency_ms)}` : "—"} />
          <Fact label={t(english, "绑定", "Binding")} value={c.binding_state ?? "—"} mono /></dl>
          {c.query ? <div className="pj-quote"><p>{c.query}</p><small>{t(english, "查询词", "Query")}</small></div> : null}</div> : null}
      </li>;
    })}</ol>);

  const body = selected && sState ? (() => {
    if (selected.kind === "ingress" || selected.kind === "hook") return (
      <dl className="pj-facts"><Fact label={t(english, "收到时间", "Received")} value={fmtTime(data.prompt.at)} /><Fact label={t(english, "宿主", "Host")} value={(HOSTS[data.prompt.host] ?? [data.prompt.host || HOSTS.unknown[0], data.prompt.host || HOSTS.unknown[1]])[english ? 1 : 0]} />
        <Fact label={t(english, "来源核验", "Origin check")} value={origin ? <Chip tone="ok">{t(english, "已核验为真人输入", "Verified human")}</Chip> : <Chip tone="warn">{t(english, "未核验", "Unverified")}</Chip>} />
        <Fact label={t(english, "核验依据", "Evidence")} value={row?.origin_evidence?.reason ?? "—"} mono /><Fact label="session" value={data.prompt.session_id} mono /><Fact label="check_id" value={data.prompt.check_id} mono /></dl>);
    if (selected.kind === "route") return r ? (
      <div className="space-y-3"><div className="flex flex-wrap gap-2"><Chip tone="info">{t(english, "建议路线", "Suggested")} · {r.recommended_route}</Chip><Chip>{t(english, "历史依赖", "History")} · {r.history_dependency}</Chip>{r.memory_policy ? <Chip tone="ok">{t(english, "记忆策略", "Policy")} · {r.memory_policy}</Chip> : null}</div>
        {r.reason ? <p className="text-sm leading-6 text-muted-foreground">{r.reason}</p> : null}
        {r.focus_terms?.length ? <div className="flex flex-wrap gap-1.5">{r.focus_terms.map((x: string) => <Chip key={x}>{x}</Chip>)}</div> : null}
        {r.catalog_hints?.length ? <ul className="space-y-1">{r.catalog_hints.map((h: Json, i: number) => <li key={i} className="flex justify-between gap-3 rounded-lg bg-muted/40 px-3 py-1.5 text-xs"><span className="truncate">{h.title}</span><span className="shrink-0 text-muted-foreground">{h.source_count ?? "—"} {t(english, "条来源", "sources")}</span></li>)}</ul> : null}</div>) : <p className="pj-empty">{t(english, "没有取得路由回执。", "No route receipt.")}</p>;
    if (selected.kind === "deliver") { const d = data.delivery; return d && d.state !== "none" ? (
      <div className="space-y-3"><dl className="pj-facts"><Fact label={t(english, "宿主送达", "Host delivery")} value={d.state === "confirmed" ? <Chip tone="ok">{t(english, "宿主已收到", "Host received")}</Chip> : <Chip tone="warn">{t(english, "已生成 · 宿主侧未找到收到记录", "Generated · no host-side receipt")}</Chip>} />
        <Fact label={t(english, "时间", "At")} value={fmtTime(d.at)} /><Fact label={t(english, "注入字符", "Injected chars")} value={d.injected_chars} /><Fact label={t(english, "Hook 输出字符", "Hook output chars")} value={d.out_chars} /><Fact label={t(english, "Hook 耗时", "Hook time")} value={ms(d.hook_ms)} /><Fact label={t(english, "依据", "Evidence")} value={d.evidence} mono /></dl>
        <p className="text-xs leading-5 text-muted-foreground">{EVIDENCE[d.evidence]?.[english ? 1 : 0] ?? d.evidence}</p></div>) : <p className="pj-empty">{data.delivery?.reason ? reasonText(english, data.delivery.reason) : t(english, "这个宿主没有送达回执来源。", "No delivery receipt source for this host.")}</p>; }
    if (selected.kind === "answer") { const a = data.answer; return a && a.state !== "none" ? (
      <div className="space-y-3"><dl className="pj-facts"><Fact label={t(english, "结束状态", "Finish")} value={a.state === "done" ? <Chip tone="ok">end_turn</Chip> : <Chip tone="warn">{a.stop_reason ?? t(english, "进行中", "in progress")}</Chip>} />
        <Fact label={t(english, "模型步数", "Model steps")} value={a.steps} /><Fact label={t(english, "工具调用", "Tool calls")} value={a.tool_calls} /><Fact label={t(english, "总耗时", "Duration")} value={ms(a.duration_ms)} /><Fact label={t(english, "最终回复字数", "Final reply chars")} value={a.final_chars} /><Fact label={t(english, "开始", "Started")} value={fmtTime(a.started_at)} /></dl>
        {a.tools?.length ? <div><h5 className="pj-h5">{t(english, "本轮用到的工具", "Tools used this turn")}</h5><div className="flex flex-wrap gap-1.5">{a.tools.map((x: Json) => <Chip key={x.name}><span className="font-mono">{x.name}</span> ×{x.count}</Chip>)}</div></div> : null}
        <p className="text-xs leading-5 text-muted-foreground">{t(english, "数据来自宿主自己的会话记录：这一轮用户消息到下一条用户消息之间，智能体做了什么。", "From the host's own records: what the agent did between this user message and the next.")}</p></div>) : <p className="pj-empty">{data.answer?.reason ? reasonText(english, data.answer.reason) : t(english, "没有在会话记录里找到这一轮的回答。", "No answer found in the transcript for this turn.")}</p>; }
    if (selected.kind === "retain") { const r2 = data.retain; return r2 && r2.state !== "none" ? (
      <div className="space-y-3"><dl className="pj-facts"><Fact label={t(english, "写回状态", "Retain state")} value={r2.state === "written" ? <Chip tone="ok">{t(english, "已写入 rollout", "Rollout written")}</Chip> : <Chip tone="warn">{t(english, "已排队 · 等待合并窗口", "Queued · waiting for settle window")}</Chip>} />
        <Fact label={t(english, "Stop Hook 时间", "Stop hook at")} value={fmtTime(r2.at)} /><Fact label="rollout" value={r2.rollout} mono /><Fact label={t(english, "累计轮数", "Turns so far")} value={r2.turns} /><Fact label={t(english, "剩余等待(秒)", "Settle left (s)")} value={r2.settle_remaining_s ?? "—"} /><Fact label="adapter rc" value={r2.adapter_rc} mono /><Fact label={t(english, "Hook 耗时", "Hook time")} value={ms(r2.hook_ms)} /></dl>
        <p className="text-xs leading-5 text-muted-foreground">{t(english, "依据是 Stop Hook 的写回事件。「已写入」代表 rollout 已落盘，进入记忆库还要经后台合并，这一步目前没有逐条回执。", "Based on the Stop-hook retain event. Written means the rollout is on disk; entering the bank still goes through background merging, which has no per-prompt receipt yet.")}</p></div>) : <p className="pj-empty">{data.retain?.reason ? reasonText(english, data.retain.reason) : t(english, "这一轮之后还没有看到 Stop Hook 的写回事件。", "No Stop-hook retain event seen after this turn yet.")}</p>; }
    if (selected.kind === "pref" || selected.kind === "context") return g ? (
      <div className="space-y-3"><dl className="pj-facts"><Fact label={t(english, "稳定偏好", "Stable")} value={g.stable_count} /><Fact label={t(english, "候选偏好", "Candidates")} value={g.candidate_count} /><Fact label={t(english, "已注入", "Injected")} value={g.included_count} /><Fact label={t(english, "上下文字符", "Context chars")} value={g.context_chars} />
        <Fact label={t(english, "宿主送达", "Host delivery")} value={String(g.host_state) === "hook_context_pending" ? <Chip tone="warn">{t(english, "已准备 · 送达未确认", "Prepared · unconfirmed")}</Chip> : <Chip tone="ok">{g.host_state}</Chip>} /></dl>
        {selected.kind === "pref" ? [...g.stable, ...g.included].slice(0, 5).map((it: Json, i: number) => <div key={`${it.id}-${i}`} className="pj-quote"><p>{it.text}</p>{it.applies_when?.length ? <small>{t(english, "适用：", "When: ")}{it.applies_when.join("；")}</small> : null}</div>) : null}
        {selected.kind === "pref" && sState.calls.length ? callRows(sState.calls) : null}</div>) : <p className="pj-empty">{t(english, "没有取得偏好注入回执。", "No guidance receipt.")}</p>;
    if (sState.calls.length) return callRows(sState.calls);
    return <p className="pj-empty">{mode === "stats" ? t(english, "这个节点在近 24 小时没有记录到工具调用，或它没有独立的工具。", "No tool calls in the last 24h, or this node has no tool of its own.") : sState.state === "idle" ? t(english, "本轮没有触发这条路径，这不是错误：路由判断不需要时就不会走。", "This path was not triggered this turn. Not an error: it is skipped when routing says it is not needed.") : t(english, "这个环节目前没有逐条 Prompt 的回执，所以不显示成功或失败。", "This step has no per-prompt receipt, so neither success nor failure is shown.")}</p>;
  })() : null;

  return (
    <section className="pj-root" aria-label={t(english, "EP 链路", "EP flow")}>
      <header className="pj-hero">
        <p className="pj-quote-text">{data.prompt.text || row.user_prompt}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Chip><Clock3 className="h-3 w-3" />{fmtTime(data.prompt.at)}</Chip><Chip tone="info">{(HOSTS[data.prompt.host] ?? [data.prompt.host || HOSTS.unknown[0], data.prompt.host || HOSTS.unknown[1]])[english ? 1 : 0]}</Chip>
          {origin ? <Chip tone="ok"><ShieldCheck className="h-3 w-3" />{t(english, "真人输入已核验", "Human verified")}</Chip> : <Chip tone="warn"><ShieldQuestion className="h-3 w-3" />{t(english, "来源未核验", "Origin unverified")}</Chip>}
        </div>
      </header>

      <div className="pj2-kpis">
        <div><b>3</b><span>{t(english, "条记忆路线", "memory routes")}</span></div>
        <div><b>5</b><span>{t(english, "个偏好维度", "preference dimensions")}</span></div>
        <div><b>{NODES.length}</b><span>{t(english, "个逻辑节点", "logic nodes")}</span></div>
        <div><b>{ALL_TOOLS.length}</b><span>{t(english, "个 MCP 工具", "MCP tools")}</span></div>
        <div className="pj2-live"><b>{mode === "prompt" ? calls.length : (stats?.total_calls ?? "—")}</b><span>{mode === "prompt" ? t(english, `次调用 · 触发 ${touched} 节点`, `calls · ${touched} nodes lit`) : t(english, `次调用 · 近 24 小时 · ${stats?.prompts_with_calls ?? "—"} 条 Prompt`, `calls · 24h · ${stats?.prompts_with_calls ?? "—"} prompts`)}</span></div>
        <div className="pj2-toggle" role="group" aria-label={t(english, "显示范围", "Scope")}>
          <button type="button" aria-pressed={mode === "prompt"} onClick={() => setMode("prompt")}>{t(english, "本条 Prompt", "This prompt")}</button>
          <button type="button" aria-pressed={mode === "stats"} onClick={() => setMode("stats")}>{t(english, "近 24 小时", "Last 24h")}</button>
        </div>
      </div>

      <div ref={box} className="pj2-stage" style={{ height: H * scale }}>
        <div className="pj2-canvas" style={{ width: W, height: H, transform: `scale(${scale})` }}>
          {LANES_BOX.map((l) => <div key={l.lane} className="pj2-lane" style={{ left: l.x, top: l.y, width: l.w, height: l.h, ["--tone" as any]: LANE[l.lane].tone }}>
            <b>{t(english, l.zh, l.en)}</b><span>{l.lane === "user" ? t(english, `${l.paths} 条检索路径 · 5 个偏好维度`, `${l.paths} retrieval paths · 5 preference dimensions`) : t(english, `${l.paths} 条路径`, `${l.paths} path(s)`)}</span></div>)}
          <svg className="pj2-edges" width={W} height={H} aria-hidden="true">
            <defs>{(Object.keys(LANE) as Lane[]).map((k) => <linearGradient key={k} id={`pj2-g-${k}`} x1="0" x2="1"><stop offset="0" stopColor={LANE[k].tone} stopOpacity=".15" /><stop offset="1" stopColor={LANE[k].tone} /></linearGradient>)}</defs>
            {EDGES.map(([a, b]) => { const A = BYID[a], B = BYID[b], on = ["done", "partial"].includes(states[a]?.state) && ["done", "partial"].includes(states[b]?.state); const lane = B.lane === "trunk" || A.lane === "trunk" ? (B.lane === "trunk" ? "trunk" : B.lane) : B.lane === "out" ? A.lane : B.lane;
              return <g key={`${a}-${b}`}><path d={edgePath(A, B)} className="pj2-edge" /><path d={edgePath(A, B)} className={`pj2-edge-on ${on ? "is-on" : ""}`} stroke={on ? `url(#pj2-g-${lane})` : "transparent"} /></g>; })}
          </svg>
          {NODES.map((n) => { const s = states[n.id]; const Icon = n.icon; const tone = LANE[n.lane].tone; const act = selected?.id === n.id;
            return <button key={n.id} type="button" onClick={() => setSel(n.id)} aria-pressed={act} title={`${t(english, n.zh, n.en)} · ${t(english, STATE_TEXT[s?.state ?? "none"][0], STATE_TEXT[s?.state ?? "none"][1])}`}
              className={`pj2-node pj2-${s?.state ?? "none"} ${n.kind === "fork" ? "is-fork" : ""} ${act ? "is-sel" : ""}`} style={{ left: n.x, top: n.y, width: n.w, height: n.h, ["--tone" as any]: tone }}>
              <span className="pj2-ico"><Icon className="h-4 w-4" /></span>
              {n.kind !== "fork" ? <span className="pj2-txt"><strong>{t(english, n.zh, n.en)}</strong>
                {n.kind === "pref" ? <em className="pj2-dims">{DIMENSIONS.map(([zh, en, c]) => <i key={zh} title={t(english, zh, en)} style={{ background: c }} />)}</em> : <em>{t(english, n.subZh, n.subEn)}</em>}</span> : null}
              {s?.badge ? <span className="pj2-badge">{s.badge}</span> : null}
            </button>; })}
        </div>
      </div>
      <div className="pj2-legend"><span><i className="d-done" />{t(english, "已触发", "Triggered")}</span><span><i className="d-partial" />{t(english, "部分确认", "Partial")}</span><span><i className="d-idle" />{t(english, "本轮未触发（正常）", "Not triggered (normal)")}</span><span><i className="d-none" />{t(english, "无逐条回执", "No per-prompt receipt")}</span></div>

      <p className="pj2-hint">{t(english, "点击任意节点查看详情", "Click any node for details")}</p>
      <Dialog open={Boolean(selected)} onOpenChange={(open) => { if (!open) setSel(null); }}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          {selected && sState ? <div className="pj2-pop" style={{ ["--tone" as any]: LANE[selected.lane].tone }}>
            <div className="mb-3 flex items-center gap-3"><span className="pj2-ico"><selected.icon className="h-4 w-4" /></span>
              <div className="min-w-0"><DialogTitle className="text-base">{t(english, selected.zh, selected.en)}</DialogTitle><DialogDescription className="mt-0.5 text-xs leading-5">{t(english, selected.descZh, selected.descEn)}</DialogDescription></div></div>
            <div className="mb-3 flex flex-wrap items-center gap-2"><Chip tone={sState.state === "done" ? "ok" : sState.state === "partial" ? "warn" : "mute"}>{STATE_TEXT[sState.state][english ? 1 : 0]}</Chip>
              <Chip>{t(english, LANE[selected.lane].zh, LANE[selected.lane].en)}</Chip>{(selected.tools ?? []).map((x) => <Chip key={x}><span className="font-mono">{x}</span></Chip>)}</div>
            {body}
          </div> : null}
        </DialogContent>
      </Dialog>

      {mode === "prompt" && calls.length ? <div className="pj-panel mt-3"><h3 className="mb-2 text-sm font-semibold">{t(english, "本条 Prompt 的调用时间线", "Call timeline for this prompt")}</h3>{callRows(calls)}</div> : null}

      <aside className="pj-gaps"><h4>{t(english, "没有被证明的部分", "Not proven")}</h4><ul>
        {!g ? <li>{t(english, "没有找到偏好注入回执。", "No guidance receipt found.")}</li> : !["observed", "received", "delivered"].includes(String(g.host_state)) ? <li>{t(english, "偏好上下文已准备，但宿主是否真正收到未确认。", "Guidance prepared; host delivery unconfirmed.")}</li> : null}
        {!calls.length ? <li>{t(english, "本轮没有记忆工具调用记录（可能是自足任务，也可能未调用）。", "No memory-tool calls recorded (self-contained task, or none made).")}</li> : null}
        <li>{t(english, "过程观察、门控、融合重排、JEV 审查目前没有按 Prompt 绑定的回执，只显示逻辑位置。送达、回答、写回目前只有 Claude 桌面端的 Prompt 能观测，其他宿主显示为无回执。", "Process observation, gates, fusion/rerank and JEV have no per-prompt receipts, so only their logical position is shown. Delivery, answer and retain are observable for Claude desktop prompts only; other hosts show no receipt.")}</li></ul></aside>
      <details className="pj-raw"><summary><Braces className="h-3.5 w-3.5" />{t(english, "原始回执", "Raw receipts")}</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>
    </section>
  );
}
