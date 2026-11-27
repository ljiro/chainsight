"use client";

import { LogOut, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useLogout, useMe } from "@/lib/queries";

export function UserMenu() {
  const router = useRouter();
  const { data } = useMe();
  const logout = useLogout();
  if (!data) return null;
  const who = data.user?.email ?? "API key";
  return (
    <div className="flex items-center gap-2 text-xs" data-testid="user-menu">
      <UserRound className="size-4 text-muted-foreground" aria-hidden />
      <span className="hidden max-w-48 truncate sm:inline" title={who}>{who}</span>
      {data.role ? <Badge tone="neutral">{data.role}</Badge> : null}
      <span className="hidden text-muted-foreground lg:inline">· {data.organization.name}</span>
      <Button size="sm" variant="ghost" aria-label="Sign out" disabled={logout.isPending}
              onClick={() => logout.mutate(undefined, { onSettled: () => router.replace("/login") })}>
        <LogOut className="size-3.5" aria-hidden />
      </Button>
    </div>
  ); // prettier-ignore
}
