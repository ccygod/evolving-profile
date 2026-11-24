"use client";
import { useTranslations } from "next-intl";

type Windowed = {
  data: any;
  error: string | null;
  loading: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
  refresh: () => void;
};

/** Same wording and link style as the facts page: "显示 N 条记录中的 M 条 · 加载更多". */
export function WindowLoadMore({ shown, total, windowed }: { shown: number; total: number; windowed: Windowed }) {
  const t = useTranslations("releaseUi");
  return <span>
    {windowed.hasMore ? t("windowShowing", { shown, total }) : t("windowTotal", { total: Math.max(total, shown) })}
    {windowed.hasMore ? <button type="button" className="ml-2 text-primary hover:underline disabled:opacity-50" disabled={windowed.loadingMore} onClick={() => windowed.loadMore?.()}>
      {windowed.loadingMore ? t("windowLoadingMore") : t("windowLoadMore")}
    </button> : null}
  </span>;
}

export function GraphWindowControls({ windowed }: { windowed: Windowed }) {
  const t = useTranslations("releaseUi");
  const data = windowed.data;
  return <div className="mb-3 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
    {windowed.loading ? <p role="status">{t("scenarioLoading")}</p> : null}
    {windowed.error ? <p role="alert">{t(windowed.error === "stale" ? "scenarioStaleVersion" : "scenarioLoadError")}</p> : null}
    {data ? <WindowLoadMore shown={data.graph.nodes.length} total={data.page.total} windowed={windowed} /> : null}
    <button type="button" className="text-xs text-muted-foreground hover:text-foreground hover:underline" onClick={windowed.refresh}>{t("scenarioRefresh")}</button>
  </div>;
}
