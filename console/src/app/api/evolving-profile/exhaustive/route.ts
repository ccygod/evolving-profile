import { NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { EP_RUNTIME_PYTHON, epStatePath } from "@/lib/ep-state-paths";

const execFileAsync = promisify(execFile);
const PYTHON_BIN = EP_RUNTIME_PYTHON;
const ADAPTER_DIR = epStatePath("runtime/host-adapter");

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const payload = {
      start_date: typeof body?.start_date === "string" && body.start_date.trim() ? body.start_date.trim() : null,
      end_date: typeof body?.end_date === "string" && body.end_date.trim() ? body.end_date.trim() : null,
      keywords: Array.isArray(body?.keywords)
        ? body.keywords.map((k: unknown) => String(k || "").trim()).filter(Boolean)
        : typeof body?.keywords === "string"
          ? body.keywords.split(/[\s,，]+/).map((k: string) => k.trim()).filter(Boolean)
          : null,
      project: typeof body?.project === "string" && body.project.trim() ? body.project.trim() : null,
      host: typeof body?.host === "string" && body.host.trim() ? body.host.trim() : null,
      entity_category:
        typeof body?.entity_category === "string" && body.entity_category.trim()
          ? body.entity_category.trim()
          : null,
      time_mode: typeof body?.time_mode === "string" ? body.time_mode : "either",
      group_by: typeof body?.group_by === "string" ? body.group_by : "project",
      limit: Math.min(Math.max(Number(body?.limit || 50), 1), 500),
      offset: Math.max(Number(body?.offset || 0), 0),
    };

    const pyCode = [
      "import json, sys",
      `sys.path.insert(0, ${JSON.stringify(ADAPTER_DIR)})`,
      "from lib.exhaustive_query import exhaustive_search",
      "args = json.loads(sys.argv[1])",
      "print(json.dumps(exhaustive_search(**args), ensure_ascii=False))",
    ].join("\n");

    const { stdout } = await execFileAsync(PYTHON_BIN, ["-c", pyCode, JSON.stringify(payload)], {
      timeout: 30000,
      maxBuffer: 16 * 1024 * 1024,
    });
    return NextResponse.json(JSON.parse(stdout));
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "exhaustive search failed" },
      { status: 500 }
    );
  }
}
