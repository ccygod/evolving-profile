import { NextResponse } from "next/server";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { epStatePath } from "@/lib/ep-state-paths";

export const dynamic = "force-dynamic";

function candidateRegistryPaths(): string[] {
  const paths = [epStatePath("runtime", "config", "host-integrations.v1.json")];
  const sourceRoot = process.env.EVOLVING_PROFILE_HOST_SOURCE_ROOT ?? path.resolve(process.cwd(), "..");
  paths.push(path.join(sourceRoot, "config", "host-integrations.v1.json"));
  return paths;
}

async function discoverObservedHostIds(): Promise<string[]> {
  const discovered = new Set<string>();
  const dirs = [
    epStatePath("guidance-v1", "instruction-receipts"),
    epStatePath("guidance-v1", "receipts"),
  ];
  for (const dir of dirs) {
    try {
      const files = await readdir(dir);
      for (const file of files.slice(-60)) {
        if (!file.endsWith(".json")) continue;
        try {
          const obj = JSON.parse(await readFile(path.join(dir, file), "utf8"));
          const h = String(obj?.host_id || obj?.host || "").trim();
          if (h && h !== "unknown") discovered.add(h);
        } catch {
          // ignore malformed receipt
        }
      }
    } catch {
      // directory may not exist
    }
  }
  return Array.from(discovered);
}

export async function GET() {
  try {
    let registry: any = null;
    for (const candidate of candidateRegistryPaths()) {
      try {
        registry = JSON.parse(await readFile(candidate, "utf8"));
        if (Array.isArray(registry?.profiles) && registry.profiles.length) break;
      } catch {
        // try next path
      }
    }

    const profiles: Array<Record<string, any>> = Array.isArray(registry?.profiles)
      ? registry.profiles.map((profile: any) => ({
          id: String(profile.id),
          labels: profile.labels ?? { en: String(profile.id), "zh-CN": String(profile.id) },
          documented: profile.documented ?? {},
          platforms: profile.platforms ?? [],
          transports: profile.transports ?? [],
          source_parser: profile.source_parser ?? null,
        }))
      : [];

    const seen = new Set(profiles.map((p) => p.id));
    for (const hostId of await discoverObservedHostIds()) {
      if (!seen.has(hostId)) {
        seen.add(hostId);
        profiles.push({
          id: hostId,
          labels: { en: hostId, "zh-CN": hostId },
          documented: {},
          platforms: ["macos"],
          transports: ["stdio"],
          source_parser: null,
        });
      }
    }

    return NextResponse.json(
      { schema: "ep.host-catalog.v1", revision: registry?.revision ?? null, profiles },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { schema: "ep.host-catalog.v1", revision: null, profiles: [] },
      { headers: { "Cache-Control": "no-store" } },
    );
  }
}
