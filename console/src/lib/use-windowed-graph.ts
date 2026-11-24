"use client";
import { useEffect, useState } from "react";

type GraphLike = { nodes?: any[]; edges?: any[]; links?: any[]; timeline?: any[] };

const nodeKey = (n: any) => String(n?.id ?? JSON.stringify(n));
const edgeKey = (e: any) => String(e?.id ?? `${e?.source}|${e?.target}|${e?.type ?? ""}`);

function mergeList(a: any[] | undefined, b: any[] | undefined, key: (x: any) => string) {
  if (!a) return b;
  if (!b) return a;
  const seen = new Set(a.map(key));
  return [...a, ...b.filter((x) => !seen.has(key(x)))];
}

function mergeGraph(a: GraphLike | undefined, b: GraphLike | undefined): GraphLike | undefined {
  if (!a || !b) return a ?? b;
  return {
    ...a,
    nodes: mergeList(a.nodes, b.nodes, nodeKey),
    edges: mergeList(a.edges, b.edges, edgeKey),
    links: mergeList(a.links, b.links, edgeKey),
    timeline: mergeList(a.timeline, b.timeline, nodeKey),
  };
}

/** Append the next window onto what is already shown (same as the facts page "load more"). */
export function mergeWindows(previous: any, next: any) {
  const merged = { ...previous };
  if (previous.graph || next.graph) merged.graph = mergeGraph(previous.graph, next.graph);
  if (previous.context?.graph || next.context?.graph) {
    merged.context = { ...previous.context, graph: mergeGraph(previous.context?.graph, next.context?.graph) };
  }
  if (Array.isArray(previous.edges) || Array.isArray(next.edges)) merged.edges = mergeList(previous.edges, next.edges, edgeKey);
  merged.page = { ...next.page, offset: 0 };
  merged.version = next.version ?? previous.version;
  return merged;
}

/**
 * Graph data fetched in windows. The first window loads on mount; `loadMore()`
 * fetches the next window (pinned to the same data version) and appends it, so
 * the view grows like the facts list instead of paging.
 */
export function useWindowedGraph(endpoint: string, query = "", enabled = true) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: string; value: any }>({ key: "", value: null });
  const [failure, setFailure] = useState<{ key: string; error: string } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const key = `${endpoint}?${query}`;
  const requestKey = `${key}:${revision}`;

  const fetchWindow = async (offset: number, version: string, signal?: AbortSignal) => {
    const params = new URLSearchParams(query);
    params.set("offset", String(offset));
    if (version) params.set("version", version);
    const response = await fetch(`${endpoint}?${params}`, { cache: "no-store", signal });
    const value = await response.json();
    if (!response.ok) throw new Error(response.status === 409 ? "stale" : response.status === 403 ? "scope" : "failed");
    return value;
  };

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setFailure(null);
    void fetchWindow(0, "", controller.signal)
      .then((value) => { if (!controller.signal.aborted) setResult({ key: requestKey, value }); })
      .catch((error) => { if (!controller.signal.aborted) { setResult({ key: requestKey, value: null }); setFailure({ key: requestKey, error: error.message }); } });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint, query, revision, enabled]);

  const data = enabled && result.key === requestKey ? result.value : null;
  const error = failure?.key === requestKey ? failure.error : null;
  const hasMore = data?.page?.nextOffset != null;

  const loadMore = async () => {
    if (!data || !hasMore || loadingMore) return;
    const startedFor = requestKey;
    setLoadingMore(true);
    setFailure(null);
    try {
      const next = await fetchWindow(data.page.nextOffset, data.version || "");
      setResult((previous) => previous.key === startedFor && previous.value ? { key: startedFor, value: mergeWindows(previous.value, next) } : previous);
    } catch (e) {
      // Keyed by request: a failure from an outdated query is never shown.
      setFailure({ key: startedFor, error: e instanceof Error ? e.message : "failed" });
    } finally {
      setLoadingMore(false);
    }
  };

  return {
    data,
    error,
    loading: enabled && !data && !error,
    loadingMore,
    hasMore,
    loadMore,
    refresh: () => setRevision((value) => value + 1),
  };
}
