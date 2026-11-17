import { EP_API_ENV } from "@/lib/ep-state-paths";
import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";

// 获取 Provider 的模型列表：与 ccswitch 一样请求 OpenAI 兼容的 GET {base_url}/models。
// 只访问模型列表接口，不发送任何 EP 记忆内容。

const ENV_PATH = EP_API_ENV;

function friendlyError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error ?? "");
  if (/ECONNREFUSED|ENOTFOUND|EAI_AGAIN|fetch failed|network|timeout|timed out|aborted/i.test(message)) {
    return "获取模型列表失败：无法连接到该 Provider，请检查 Base URL、代理服务和端口。";
  }
  return "获取模型列表失败：Provider 返回了无法识别的响应，请检查配置后重试。";
}

function statusError(status: number) {
  if (status === 401 || status === 403) return "获取模型列表失败：API Key 无效或没有访问权限。";
  if (status === 404) return "获取模型列表失败：Provider 未找到 /models 接口，请检查 Base URL 是否包含正确的 /v1 路径。";
  if (status >= 500) return "获取模型列表失败：Provider 服务暂时不可用，请稍后重试。";
  return "获取模型列表失败：Provider 拒绝了请求，请检查配置。";
}

async function effectiveApiKey() {
  try {
    const text = await readFile(ENV_PATH, "utf8");
    const row = text.split(/\r?\n/).find((line) => line.startsWith("EVOLVING_PROFILE_API_LLM_API_KEY="));
    return row?.slice(row.indexOf("=") + 1) || "";
  } catch {
    return "";
  }
}

// 兼容常见返回格式：{ data: [{ id }] }（OpenAI 兼容）、{ models: [...] }，以及纯字符串数组。
function parseModelIds(body: unknown): string[] {
  const raw: unknown[] = Array.isArray(body)
    ? body
    : Array.isArray((body as any)?.data)
      ? (body as any).data
      : Array.isArray((body as any)?.models)
        ? (body as any).models
        : [];
  const ids = raw
    .map((item) => (typeof item === "string" ? item : (item as any)?.id ?? (item as any)?.name ?? ""))
    .map((id) => String(id).trim())
    .filter((id) => id.length > 0);
  return [...new Set(ids)].sort((a, b) => a.localeCompare(b));
}

export async function POST(request: Request) {
  try {
    const value = await request.json();
    const baseUrl = String(value.base_url || "").replace(/\/$/, "");
    if (!baseUrl) return NextResponse.json({ ok: false, error: "需要填写 Base URL" }, { status: 400 });
    const headers: Record<string, string> = { "content-type": "application/json" };
    const suppliedKey = String(value.api_key || "");
    const apiKey = suppliedKey.startsWith("••••") ? await effectiveApiKey() : suppliedKey;
    if (apiKey && [...apiKey].some((character) => character.charCodeAt(0) > 255)) {
      return NextResponse.json(
        { ok: false, error: "API Key 含有非 ASCII 字符，请填写真实 API Key；页面中的掩码不能直接发送。" },
        { status: 400 }
      );
    }
    if (apiKey) headers.authorization = `Bearer ${apiKey}`;
    const response = await fetch(`${baseUrl}/models`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) {
      return NextResponse.json({ ok: false, status: response.status, error: statusError(response.status) }, { status: 502 });
    }
    const models = parseModelIds(await response.json());
    if (models.length === 0) {
      return NextResponse.json({ ok: false, error: "获取模型列表失败：接口没有返回任何模型。" }, { status: 502 });
    }
    return NextResponse.json({ ok: true, models }, { status: 200 });
  } catch (error) {
    return NextResponse.json({ ok: false, error: friendlyError(error) }, { status: 502 });
  }
}
