import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
const state = vi.hoisted(()=>({ value: null as any, error: null as string|null }));
vi.mock("@/lib/use-windowed-graph",()=>({useWindowedGraph:()=>({data:state.value,error:state.error,loading:!state.value&&!state.error,hasMore:!!state.value?.page?.nextOffset,loadingMore:false,loadMore:()=>{},refresh:()=>{}})}));
import { ContextMemoryView } from "@/components/context-memory-view";
const render = () => renderToStaticMarkup(createElement(NextIntlClientProvider,{locale:"en",messages:en,timeZone:"Asia/Shanghai",children:createElement(ContextMemoryView,{bankId:"fixture-bank"})}));
it("consumes the metadata-only graph envelope and shows the facts-style showing-M-of-N load-more line",()=>{
 state.error=null;
 state.value={version:"fixture-version",context:{status:"ready",sessionCount:2,projectCount:0,pendingReview:2},page:{offset:0,limit:240,total:300,indexedTotal:300,nextOffset:240},
  graph:{nodes:[{id:"session:a",type:"session",label:"A"},{id:"session:b",type:"session",label:"B"}],edges:[],timeline:[],bankRecordLinks:{available:false,linked:0,reason:"no snapshot"}}};
 const markup=render();
 expect(markup).toContain("Showing 2 of 300 records");
 expect(markup).toContain("Load more");
 expect(markup).toContain("Find a node by name or ID");
 expect(markup).not.toContain("Loading scenario data");
});
it("an unread or expired graph shows loading or refresh feedback without presenting a zero-node success",()=>{
 state.value=null;state.error=null;
 expect(render()).toContain("Loading scenario data…");
 state.error="stale";
 const markup=render();
 expect(markup).toContain("The source snapshot changed.");
 expect(markup).toContain("Refresh snapshot");
 expect(markup).not.toContain("Showing 0 of");
});
