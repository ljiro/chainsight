"use client";

import { Anchor } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS, isActive } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function AppSidebar() {
  const pathname = usePathname() ?? "";
  return (
    <aside className="hidden w-60 shrink-0 flex-col bg-sidebar text-sidebar-foreground md:flex">
      <div className="flex h-14 items-center gap-2 px-5">
        <Anchor className="size-5 text-info" aria-hidden />
        <span className="text-[15px] font-semibold tracking-tight text-white">ChainSight</span>
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-3 py-2">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-sidebar-accent font-medium text-white"
                  : "text-sidebar-muted hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </Link>
          );
        })}
      </nav>
      <p className="px-5 py-4 text-[11px] leading-relaxed text-sidebar-muted">
        Open-source maritime intelligence. Data provenance and freshness are shown everywhere.
      </p>
    </aside>
  );
}

/** Compact horizontal nav for small screens. */
export function MobileNav() {
  const pathname = usePathname() ?? "";
  return (
    <nav aria-label="Main mobile" className="flex gap-1 overflow-x-auto md:hidden">
      {NAV_ITEMS.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          aria-current={isActive(pathname, href) ? "page" : undefined}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs whitespace-nowrap",
            isActive(pathname, href) ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground",
          )}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
