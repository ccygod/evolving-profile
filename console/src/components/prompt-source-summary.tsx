import type {ReactNode} from "react";
import {useLocale,useTranslations} from "next-intl";

export type PromptSourceCounts={natural:number|null;audit:number|null;verification?:"partial"|"complete"|null;pending?:number|null;scope?:string|null};

const knownCount=(value:unknown):value is number=>typeof value==="number" && Number.isSafeInteger(value) && value>=0;
const countLabel=(value:unknown)=>knownCount(value)?value:"—";

export function PromptOriginBadge({kind,status}:{kind?:string;status?:string}){
  const text=useTranslations('releaseUi');
  const keys:Record<string,string>={human:'promptSourceNatural',subagent:'promptSourceSubagent',automation:'promptSourceAutomation','memory-maintenance':'promptSourceMaintenance'};
  const key=status==='verified' && Object.hasOwn(keys,kind || '') ? keys[kind!] : 'promptSourceUnknown';
  return <span className="rounded border px-1.5 py-0.5">{text(key)}</span>;
}

export function PromptSourceEmpty({counts,source,onBrowse}:{counts:PromptSourceCounts;source:string;onBrowse:()=>void}){
  const text=useTranslations('releaseUi');
  const pending=source==='natural' && ((knownCount(counts.pending) && counts.pending>0) ||
    (knownCount(counts.audit) && counts.audit>0 && counts.verification==='partial'));
  if(pending)return <div role="status" className="space-y-3 p-3 text-sm text-muted-foreground">
    <p>{text('promptSourcePendingEmpty')}</p>
    <button type="button" className="rounded border bg-background px-3 py-2 text-foreground hover:bg-muted" onClick={onBrowse}>{text('promptSourceBrowseAudit')}</button>
  </div>;
  return <div className="p-3 text-sm text-muted-foreground">{text(source==='natural'?'promptSourceEmptyNatural':'promptSourceEmptyFiltered')}</div>;
}

export function PromptSourceSummary({counts,children}:{counts:PromptSourceCounts;children?:ReactNode}){
  const text=useTranslations("releaseUi");
  const english=useLocale().toLowerCase().startsWith("en");
  const fallback=counts.scope==="local_prompt_ingress_fallback";
  const status=counts.verification==="partial" || (knownCount(counts.pending) && counts.pending>0) ? "partial"
    : counts.verification==="complete" && counts.pending===0 && knownCount(counts.natural) && knownCount(counts.audit) ? "complete":"unknown";
  return <div className="max-w-full rounded-xl border bg-background px-3 py-1.5 text-xs text-muted-foreground shadow-sm" data-source-verification={status} role="status">
    <div className="flex flex-wrap items-center gap-x-2">
      <span>{text("promptSourceAuditCounts")}: {countLabel(counts.natural)} / {countLabel(counts.audit)}</span>
      {children}
    </div>
    <p className="mt-1 max-w-prose leading-4">{text("promptSourceScope")}</p>
    {fallback ? <p className="mt-1 max-w-prose leading-4" data-source-scope="local-fallback">{english?"Data source: local hook-log fallback (status service does not serve the prompt list). Claude/Trae origin is verified against their own transcripts when available.":"数据源：本地 Hook 日志兜底（状态服务未提供 Prompt 列表接口）。Claude 来源按其原生会话记录核验，找不到记录的显示为未知。"}</p> : null}
    {status==="partial" ? <p className="mt-1 max-w-prose leading-4">{text("promptSourceCoveragePartial",{pending:countLabel(counts.pending)})}</p>
      : status==="unknown" ? <p className="mt-1 max-w-prose leading-4">{text("promptSourceCoverageUnknown")}</p>:null}
  </div>;
}
