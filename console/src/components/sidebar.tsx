"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useBank } from "@/lib/bank-context";
import { bankRoute } from "@/lib/bank-url";
import {
  Search,
  Database,
  FileText,
  ChevronLeft,
  ChevronRight,
  Settings,
  GitBranch,
  Bot,
  Activity,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import Link from "next/link";

import { inlineUiText } from "@/lib/inline-i18n";
type NavItem = "recall" | "data" | "documents" | "agent-memory" | "flow" | "quality" | "profile";

interface SidebarProps {
  currentTab: NavItem;
  onTabChange: (tab: NavItem) => void;
}

export function Sidebar({ currentTab, onTabChange }: SidebarProps) {
  const t = useTranslations("bank.sidebar");
  const tBank = useTranslations("bank");
  const locale = useLocale();
  const { currentBank } = useBank();
  const [isCollapsed, setIsCollapsed] = useState(false);

  if (!currentBank) {
    return null;
  }

  const isZh = locale.startsWith("zh");
  const navGroups: Array<{
    title: string;
    items: Array<{ id: NavItem; label: string; icon: typeof Database }>;
  }> = [
    {
      title: isZh ? inlineUiText("记忆平面") : "Memory Planes",
      items: [
        { id: "data", label: t("memories"), icon: Database },
        { id: "agent-memory", label: t("agentMemory"), icon: Bot },
      ],
    },
    {
      title: isZh ? inlineUiText("检索与来源") : "Retrieval & Sources",
      items: [
        { id: "recall", label: t("recall"), icon: Search },
        { id: "documents", label: t("documents"), icon: FileText },
      ],
    },
    {
      title: isZh ? inlineUiText("可观测与治理") : "Observability & Ops",
      items: [
        { id: "flow", label: isZh ? inlineUiText("链路") : "Flow", icon: GitBranch },
        { id: "quality", label: isZh ? inlineUiText("质量引擎") : "Quality Engine", icon: Activity },
        { id: "profile", label: tBank("bankConfiguration"), icon: Settings },
      ],
    },
  ];

  return (
    <aside
      className={cn(
        "bg-sidebar border-r border-sidebar-border flex flex-col transition-[width] duration-200 select-none shrink-0",
        isCollapsed ? "w-14 sm:w-16" : "w-56 sm:w-[232px]"
      )}
    >
      <nav className="flex-1 overflow-y-auto px-2.5 py-4 space-y-5">
        {navGroups.map((group) => (
          <div key={group.title}>
            {!isCollapsed && (
              <div className="px-2.5 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/75">
                {group.title}
              </div>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = currentTab === item.id;
                const href = bankRoute(currentBank, `?view=${item.id}`);

                return (
                  <li key={item.id}>
                    <Link
                      href={href}
                      onClick={(e) => {
                        if (e.button === 0 && !e.ctrlKey && !e.metaKey) {
                          e.preventDefault();
                          onTabChange(item.id);
                        }
                      }}
                      className={cn(
                        "group relative w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13.5px] font-medium transition-all",
                        isActive
                          ? "bg-card text-foreground shadow-xs ring-1 ring-border/80 dark:bg-sky-500/12 dark:text-sky-300 dark:ring-sky-400/30"
                          : "text-sidebar-foreground hover:bg-sidebar-accent/70 hover:text-foreground",
                        isCollapsed && "justify-center px-0"
                      )}
                      title={isCollapsed ? item.label : undefined}
                    >
                      {isActive && !isCollapsed && (
                        <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-primary dark:bg-sky-400" />
                      )}
                      <Icon
                        className={cn(
                          "w-4 h-4 flex-shrink-0 transition-colors",
                          isActive
                            ? "text-foreground dark:text-sky-300"
                            : "text-muted-foreground group-hover:text-foreground"
                        )}
                      />
                      {!isCollapsed && <span className="truncate">{item.label}</span>}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer status + collapse */}
      <div className="p-2.5 border-t border-sidebar-border space-y-1.5">
        {!isCollapsed && (
          <div className="flex items-center justify-between rounded-lg bg-card/70 dark:bg-card/40 border border-border/60 px-2.5 py-1.5 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1.5 font-medium text-foreground/85">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
              EP 6.2
            </span>
            <span className="font-mono text-[10px] opacity-75">local-first</span>
          </div>
        )}
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className={cn(
            "w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-muted-foreground hover:bg-sidebar-accent/70 hover:text-foreground transition-colors",
            isCollapsed && "justify-center px-0"
          )}
          title={isCollapsed ? t("expandSidebar") : t("collapseSidebar")}
        >
          {isCollapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <>
              <ChevronLeft className="w-4 h-4" />
              <span>{t("collapse")}</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
