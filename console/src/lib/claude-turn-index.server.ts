import { open, readdir, stat } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";

// Reads Claude's own session transcript (append-only) into a compact index so the
// flow page can show what the host really received and how the turn was answered.
export type TurnRec =
  | { k: "u"; ts: number }
  | { k: "a"; ts: number; stop: string | null; tools: string[]; chars: number; id: string | null }
  | { k: "d"; ts: number; checkId: string };
type FileIndex = { offset: number; size: number; recs: TurnRec[] };

const ROOT = process.env.EVOLVING_PROFILE_CLAUDE_PROJECTS_ROOT || path.join(homedir(), ".claude/projects");
const indexes = new Map<string, FileIndex>();
const CHUNK = 8 * 1024 * 1024;
const CHECK = /ep_prompt_binding check_id=\\*"([0-9a-f]{32})/;
const paths = new Map<string, { at: number; file: string | null }>();

export async function findClaudeTranscript(sessionId: string) {
  if (!/^[0-9a-f-]{16,64}$/i.test(sessionId)) return null;
  const hit = paths.get(sessionId);
  if (hit && Date.now() - hit.at < 30000) return hit.file;
  let file: string | null = null;
  try {
    for (const dir of await readdir(ROOT)) {
      const candidate = path.join(ROOT, dir, `${sessionId}.jsonl`);
      try { if ((await stat(candidate)).isFile()) { file = candidate; break; } } catch { /* next */ }
    }
  } catch { /* root missing */ }
  paths.set(sessionId, { at: Date.now(), file });
  return file;
}

export async function indexClaudeTranscript(file: string, deadline: number) {
  const info = await stat(file);
  let index = indexes.get(file);
  if (!index || info.size < index.offset) { index = { offset: 0, size: info.size, recs: [] }; indexes.set(file, index); }
  index.size = info.size;
  if (index.offset >= info.size) return { recs: index.recs, complete: true };
  const handle = await open(file, "r");
  try {
    while (index.offset < info.size && Date.now() < deadline) {
      const length = Math.min(CHUNK, info.size - index.offset);
      const buffer = Buffer.alloc(length);
      const { bytesRead } = await handle.read(buffer, 0, length, index.offset);
      if (!bytesRead) break;
      const text = buffer.subarray(0, bytesRead).toString("utf8");
      const last = text.lastIndexOf("\n");
      if (last < 0) break;
      const whole = text.slice(0, last);
      index.offset += Buffer.byteLength(whole, "utf8") + 1;
      for (const line of whole.split("\n")) {
        const isA = line.includes('"type":"assistant"'), isU = line.includes('"type":"user"'), isD = line.includes("ep_prompt_binding check_id=");
        if (!isA && !isU && !isD) continue;
        try {
          const row = JSON.parse(line); const ts = Date.parse(row.timestamp);
          if (Number.isNaN(ts)) continue;
          if (row.type === "attachment") {
            const m = CHECK.exec(JSON.stringify(row.attachment ?? "")); if (m) index.recs.push({ k: "d", ts, checkId: m[1] });
          } else if (row.type === "assistant") {
            const content = Array.isArray(row.message?.content) ? row.message.content : [];
            index.recs.push({ k: "a", ts, stop: row.message?.stop_reason ?? null, id: row.message?.id ?? null,
              tools: content.filter((p: any) => p?.type === "tool_use").map((p: any) => String(p.name)),
              chars: content.filter((p: any) => p?.type === "text").reduce((n: number, p: any) => n + String(p.text ?? "").length, 0) });
          } else if (row.type === "user" && !row.isMeta) {
            const c = row.message?.content;
            const body = typeof c === "string" ? c : Array.isArray(c) ? c.filter((p: any) => p?.type === "text").map((p: any) => p.text).join(" ") : "";
            if (body.trim()) index.recs.push({ k: "u", ts });
          }
        } catch { /* skip malformed line */ }
      }
    }
  } finally { await handle.close(); }
  return { recs: index.recs, complete: index.offset >= info.size };
}
