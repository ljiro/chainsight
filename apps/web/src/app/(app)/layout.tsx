import type { ReactNode } from "react";
import { AppSidebar, MobileNav } from "@/components/app-sidebar";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh">
      <AppSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-3 border-b bg-card/60 px-4 backdrop-blur md:px-6">
          <MobileNav />
          <div className="hidden items-center gap-2 md:flex">
            <Badge tone="info">Phase 3 · Shipment tracking</Badge>
          </div>
          <ThemeToggle />
        </header>
        <main className="flex-1 px-4 py-6 md:px-6">
          <div className="mx-auto flex max-w-7xl flex-col gap-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
