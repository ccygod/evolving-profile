"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Search, Database, ShieldCheck } from "lucide-react";

type ExhaustiveResult = {
  source_coverage?: {
    work_ledger_hosts_in_window: Array<{
      host: string;
      turns: number;
      sessions: number;
      first_at: string;
      last_at: string;
    }>;
    memory_units_with_occurred_start: number;
    memory_units_total: number;
    occurred_start_ratio: number;
    entity_classifications_ready: boolean;
    project_aliases_ready: boolean;
  };
  work_ledger?: {
    total_matched: number;
    returned: number;
    groups: Array<{
      group: string;
      turn_count: number;
      session_count: number;
      first_at: string;
      last_at: string;
      hosts: string[];
    }>;
  };
  memory_units?: {
    total_matched: number;
    returned: number;
    by_fact_type: Record<string, number>;
    items: Array<{
      id: string;
      fact_type: string;
      document_id: string;
      effective_at: string | null;
      occurred_at: string | null;
      mentioned_at: string | null;
      text: string;
      context: string;
    }>;
  };
};

export function ExhaustiveSearchPanel() {
  const [startDate, setStartDate] = useState("2026-08-01");
  const [endDate, setEndDate] = useState("2026-10-31");
  const [entityCategory, setEntityCategory] = useState("client_org");
  const [timeMode, setTimeMode] = useState("either");
  const [groupBy, setGroupBy] = useState("project");
  const [keywords, setKeywords] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ExhaustiveResult | null>(null);

  async function runSearch() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/evolving-profile/exhaustive", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          entity_category: entityCategory,
          time_mode: timeMode,
          group_by: groupBy,
          keywords,
          limit: 50,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || "查询失败");
      setData(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : "查询失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-xl border bg-card p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            全量时间线与客户检索（无 12s / Top-K 截断）
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            直连 Postgres 中文全文索引与实体分类表，返回精确 total_matched 与宿主覆盖清单。
          </p>
        </div>
        <Button onClick={runSearch} disabled={loading} size="sm">
          <Search className="h-3.5 w-3.5 mr-1.5" />
          {loading ? "全量检索中..." : "执行全量检索"}
        </Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
        <label className="space-y-1">
          <span className="text-muted-foreground">起始日期</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5"
          />
        </label>
        <label className="space-y-1">
          <span className="text-muted-foreground">结束日期</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5"
          />
        </label>
        <label className="space-y-1">
          <span className="text-muted-foreground">实体分类</span>
          <select
            value={entityCategory}
            onChange={(e) => setEntityCategory(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5"
          >
            <option value="">全部实体</option>
            <option value="client_org">客户与机构 (client_org)</option>
            <option value="project">项目 (project)</option>
            <option value="person">人物 (person)</option>
            <option value="location">地点 (location)</option>
            <option value="product_tech">产品技术 (product_tech)</option>
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-muted-foreground">时间口径</span>
          <select
            value={timeMode}
            onChange={(e) => setTimeMode(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5"
          >
            <option value="either">优先发生时间 (either)</option>
            <option value="occurred">仅实际发生时间 (occurred)</option>
            <option value="mentioned">对话提及时间 (mentioned)</option>
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-muted-foreground">台账分组</span>
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5"
          >
            <option value="project">按清洗后项目 (project)</option>
            <option value="month">按月 (month)</option>
            <option value="week">按周 (week)</option>
            <option value="host">按宿主 (host)</option>
          </select>
        </label>
      </div>

      <input
        type="text"
        value={keywords}
        onChange={(e) => setKeywords(e.target.value)}
        placeholder="可选关键词（空格/逗号分隔多词 OR 匹配，如：天职师大 农学院 宣传册）"
        className="w-full rounded-md border bg-background px-3 py-1.5 text-xs"
      />

      {error && <div className="rounded-md bg-rose-500/10 text-rose-600 px-3 py-2 text-xs">{error}</div>}

      {data && (
        <div className="space-y-4 pt-2 border-t text-xs">
          <div className="rounded-lg bg-muted/50 p-3 space-y-1.5">
            <div className="font-medium flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
              数据源覆盖清单（诚实统计）
            </div>
            <div className="flex flex-wrap gap-2 text-muted-foreground">
              <span>记忆单元全量命中：<strong className="text-foreground">{data.memory_units?.total_matched ?? 0}</strong> 条</span>
              <span>· 含实际发生时间：<strong className="text-foreground">{Math.round((data.source_coverage?.occurred_start_ratio ?? 0) * 100)}%</strong></span>
              <span>· 台账命中：<strong className="text-foreground">{data.work_ledger?.total_matched ?? 0}</strong> 轮（{data.work_ledger?.groups?.length ?? 0} 组）</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(data.source_coverage?.work_ledger_hosts_in_window || []).map((h) => (
                <span key={h.host} className="rounded bg-background border px-2 py-0.5">
                  {h.host}: {h.sessions} 会话 / {h.turns} 轮
                </span>
              ))}
            </div>
          </div>

          {!!data.work_ledger?.groups?.length && (
            <div>
              <div className="font-medium mb-1.5">项目 / 时间分组全览（共 {data.work_ledger.groups.length} 组，展示前 15 组）</div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                {data.work_ledger.groups.slice(0, 15).map((g) => (
                  <div key={g.group} className="rounded border p-2 bg-background/60">
                    <div className="font-medium truncate" title={g.group}>{g.group}</div>
                    <div className="text-muted-foreground mt-0.5">
                      {g.session_count} 会话 · {g.turn_count} 轮 · {g.hosts.join(", ")}
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      {g.first_at?.slice(0, 10)} ~ {g.last_at?.slice(0, 10)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!!data.memory_units?.items?.length && (
            <div>
              <div className="font-medium mb-1.5">记忆单元结果（展示前 {data.memory_units.returned} 条 / 共 {data.memory_units.total_matched} 条）</div>
              <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                {data.memory_units.items.map((m) => (
                  <div key={m.id} className="rounded border p-2 bg-background/60">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                      <span className="font-mono">{m.fact_type} · {m.effective_at?.slice(0, 16) || "—"}</span>
                      <span className="truncate max-w-[200px]">{m.document_id}</span>
                    </div>
                    <div className="text-foreground leading-relaxed">{m.text}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
