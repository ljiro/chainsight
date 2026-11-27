"use client";

import { Anchor, Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { useLogin } from "@/lib/queries";

/** Only same-site relative paths are followed after sign-in (no open redirects). */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/dashboard";
  if (next.startsWith("/login")) return "/dashboard";
  return next;
}

export const inputClass = "h-9 w-full rounded-md border bg-card px-3 text-sm text-foreground";

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => router.replace(safeNext(params.get("next"))) });
  }

  const error = login.error instanceof ApiError ? login.error : null;
  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Anchor className="size-5 text-info" aria-hidden />
          Sign in to ChainSight
        </CardTitle>
        <CardDescription>Use the account your organization&apos;s administrator created for you.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-3" onSubmit={submit} aria-label="Sign in">
          <label className="flex flex-col gap-1 text-sm">
            Email
            <input className={inputClass} type="email" autoComplete="username" required value={email}
                   onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input className={inputClass} type="password" autoComplete="current-password" required value={password}
                   onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error.status === 429 ? "Too many attempts. Wait a minute and try again." : error.message}
            </p>
          ) : login.error ? (
            <p role="alert" className="text-sm text-destructive">Cannot reach the ChainSight API.</p>
          ) : null}
          <Button type="submit" disabled={login.isPending}>
            {login.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            Sign in
          </Button>
        </form>
      </CardContent>
    </Card>
  ); // prettier-ignore
}
