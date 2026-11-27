import { open, readdir, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';

// Independent witness for Claude Code / Claude desktop prompts: Claude's own
// session transcript records origin.kind per user turn. The EP hook's default
// `prompt_origin: user_direct` is never used as proof.
type Entry = { ts: number; text: string; kind: string; sidechain: boolean; external: boolean };
type FileIndex = { path: string; offset: number; size: number; mtimeMs: number; entries: Entry[] };

const CLAUDE_ROOT = process.env.EVOLVING_PROFILE_CLAUDE_PROJECTS_ROOT || path.join(homedir(), '.claude/projects');
const indexes = new Map<string, FileIndex>();
const norm = (value: unknown) => String(value ?? '').replace(/\s+/g, ' ').trim();
const CHUNK = 8 * 1024 * 1024;

async function findTranscript(sessionId: string): Promise<string | null> {
  if (!/^[0-9a-f-]{16,64}$/i.test(sessionId)) return null;
  let dirs: string[] = [];
  try { dirs = await readdir(CLAUDE_ROOT); } catch { return null; }
  for (const dir of dirs) {
    const candidate = path.join(CLAUDE_ROOT, dir, `${sessionId}.jsonl`);
    try { if ((await stat(candidate)).isFile()) return candidate; } catch { /* next */ }
  }
  return null;
}

// Incremental, append-only index. Returns true once the whole file is indexed.
async function advance(file: string, deadline: number): Promise<{ index: FileIndex; complete: boolean }> {
  const info = await stat(file);
  let index = indexes.get(file);
  if (!index || info.size < index.offset) { index = { path: file, offset: 0, size: info.size, mtimeMs: info.mtimeMs, entries: [] }; indexes.set(file, index); }
  index.size = info.size; index.mtimeMs = info.mtimeMs;
  if (index.offset >= info.size) return { index, complete: true };
  const handle = await open(file, 'r');
  try {
    let carry = '';
    while (index.offset < info.size && Date.now() < deadline) {
      const length = Math.min(CHUNK, info.size - index.offset);
      const buffer = Buffer.alloc(length);
      const { bytesRead } = await handle.read(buffer, 0, length, index.offset);
      if (!bytesRead) break;
      const text = carry + buffer.subarray(0, bytesRead).toString('utf8');
      const lastNewline = text.lastIndexOf('\n');
      const complete = lastNewline < 0 ? '' : text.slice(0, lastNewline);
      const rest = lastNewline < 0 ? text : text.slice(lastNewline + 1);
      // Only whole lines advance the offset; the unfinished tail is re-read next time.
      index.offset += Buffer.byteLength(complete, 'utf8') + (lastNewline < 0 ? 0 : 1);
      carry = '';
      void rest;
      for (const line of complete.split('\n')) {
        if (!line.includes('"type":"user"')) continue;
        try {
          const row = JSON.parse(line);
          if (row.type !== 'user' || row.isMeta) continue;
          const content = row.message?.content;
          const body = typeof content === 'string' ? content : Array.isArray(content) ? content.filter((part: any) => part?.type === 'text').map((part: any) => part.text).join(' ') : '';
          const ts = Date.parse(row.timestamp);
          if (!body || Number.isNaN(ts)) continue;
          index.entries.push({ ts, text: norm(body), kind: String(row.origin?.kind ?? row.turnOrigin ?? 'unknown'), sidechain: row.isSidechain === true, external: row.userType === 'external' });
        } catch { /* skip malformed line */ }
      }
    }
  } finally { await handle.close(); }
  return { index, complete: index.offset >= info.size };
}

export async function verifyClaudeNativePrompt(row: any, deadline: number) {
  const evidence = (reason: string, extra: Record<string, unknown> = {}) => ({ reason, boundary: 'claude_native_transcript_origin_not_hook_default', ...extra });
  const sessionId = String(row.session_id || '');
  const file = await findTranscript(sessionId);
  if (!file) return { origin_kind: 'unknown', origin_status: 'unknown', origin_evidence: evidence('claude_native_transcript_missing') };
  let state;
  try { state = await advance(file, deadline); } catch { return { origin_kind: 'unknown', origin_status: 'unknown', origin_evidence: evidence('claude_native_transcript_unreadable') }; }
  const prompt = norm(row.prompt_preview);
  const at = Date.parse(row.at);
  const matches = state.index.entries.filter(entry => prompt && entry.text.startsWith(prompt.slice(0, 200)) && (Number.isNaN(at) || Math.abs(entry.ts - at) < 5 * 60 * 1000));
  if (!matches.length) return { origin_kind: 'unknown', origin_status: 'unknown', origin_evidence: evidence(state.complete ? 'claude_native_prompt_not_found' : 'native_prompt_source_scan_incomplete', { source_path: file, indexed_bytes: state.index.offset, source_bytes: state.index.size }) };
  const best = matches.sort((a, b) => Math.abs(a.ts - at) - Math.abs(b.ts - at))[0];
  const kind = best.sidechain ? 'subagent' : best.kind === 'human' && best.external ? 'human' : ['task-notification', 'automation', 'scheduled', 'cron'].includes(best.kind) ? 'automation' : 'unknown';
  return { origin_kind: kind, origin_status: kind === 'unknown' ? 'unknown' : 'verified', origin_evidence: evidence(kind === 'unknown' ? 'claude_native_origin_unrecognized' : 'claude_native_turn_origin', { source_path: file, native_origin_kind: best.kind, sidechain: best.sidechain }) };
}
