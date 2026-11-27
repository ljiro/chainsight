"use client";

import { Check, Copy, KeyRound, Loader2, Trash2 } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { inputClass } from "@/components/auth/login-form";
import { CarrierStatusList } from "@/components/dashboard/carrier-status-list";
import { DataSourcesCard } from "@/components/dashboard/data-sources-card";
import { SystemHealthCard } from "@/components/dashboard/system-health-card";
import { EmptyState, ErrorState, LoadingRows } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import {
  hasPermission,
  useAddMember,
  useApiKeys,
  useAuditLogs,
  useChangePassword,
  useCreateApiKey,
  useMe,
  useMembers,
  useOrg,
  useRemoveMember,
  useRevokeApiKey,
  useUpdateMember,
  useUpdateOrg,
} from "@/lib/queries";
import type { CreatedApiKey, Me, Member, Role } from "@/lib/types";
import { formatAge } from "@/lib/utils";

const ROLES: Role[] = ["viewer", "analyst", "admin", "owner"];
const ROLE_HINTS: Record<Role, string> = {
  viewer: "Read everything in the organization",
  analyst: "Viewer + track and import shipments, run experiments",
  admin: "Analyst + members, API keys, integrations, audit log",
  owner: "Admin + organization settings and the owner role",
};
const th = "py-2 pr-3 text-left text-[11px] font-medium tracking-wide text-muted-foreground uppercase";
const selectClass = "h-8 rounded-md border bg-card px-2 text-xs text-foreground";

function errorText(e: unknown): string | null {
  if (!e) return null;
  return e instanceof ApiError ? e.message : "Request failed";
}

function FormError({ error }: { error: unknown }) {
  const t = errorText(error);
  return t ? <p role="alert" className="text-sm text-destructive">{t}</p> : null;
}

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">{children}</CardContent>
    </Card>
  );
}

export function AccountSection({ me }: { me: Me }) {
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  if (!me.user) return null;
  function submit(e: FormEvent) {
    e.preventDefault();
    setDone(false);
    change.mutate({ current_password: current, new_password: next }, {
      onSuccess: () => { setDone(true); setCurrent(""); setNext(""); },
    }); // prettier-ignore
  }
  return (
    <Section title="Your account" description={`${me.user.email} · ${me.role ?? "—"} in ${me.organization.name}`}>
      <form className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={submit} aria-label="Change password">
        <label className="flex flex-col gap-1 text-sm">Current password
          <input className={inputClass} type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">New password (12+ characters)
          <input className={inputClass} type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <Button type="submit" disabled={change.isPending}>Change password</Button>
      </form>
      <FormError error={change.error} />
      {done ? <p className="text-sm text-success" role="status">Password changed. Your other sessions were signed out.</p> : null}
    </Section>
  ); // prettier-ignore
}

export function OrganizationSection({ me }: { me: Me }) {
  const org = useOrg();
  const update = useUpdateOrg();
  const [name, setName] = useState<string | null>(null);
  const canManage = hasPermission(me, "org:manage");
  if (org.isPending) return <LoadingRows rows={1} />;
  if (org.error) return <ErrorState error={org.error} />;
  const value = name ?? org.data.name;
  return (
    <Section title="Organization" description={`Slug ${org.data.slug} · ${org.data.member_count} members`}>
      {canManage ? (
        <form className="flex flex-wrap items-end gap-2" aria-label="Rename organization"
              onSubmit={(e) => { e.preventDefault(); update.mutate({ name: value }, { onSuccess: () => setName(null) }); }}>
          <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm">Name
            <input className={inputClass} required minLength={2} value={value} onChange={(e) => setName(e.target.value)} />
          </label>
          <Button type="submit" variant="outline" disabled={update.isPending || value === org.data.name}>Save</Button>
        </form>
      ) : (
        <p className="text-sm">{org.data.name}</p>
      )}
      <FormError error={update.error} />
    </Section>
  ); // prettier-ignore
}

function AddMemberForm() {
  const add = useAddMember();
  const [form, setForm] = useState({ email: "", full_name: "", role: "viewer" as Role, password: "" });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  return (
    <form className="grid gap-2 border-t pt-4 md:grid-cols-[1.4fr_1fr_auto_1fr_auto] md:items-end" aria-label="Add member"
          onSubmit={(e) => { e.preventDefault(); add.mutate({ ...form, full_name: form.full_name || undefined },
            { onSuccess: () => setForm({ email: "", full_name: "", role: "viewer", password: "" }) }); }}>
      <label className="flex flex-col gap-1 text-xs">Email<input className={inputClass} type="email" required value={form.email} onChange={set("email")} /></label>
      <label className="flex flex-col gap-1 text-xs">Name<input className={inputClass} value={form.full_name} onChange={set("full_name")} /></label>
      <label className="flex flex-col gap-1 text-xs">Role
        <select className={`${selectClass} h-9`} value={form.role} onChange={set("role")} title={ROLE_HINTS[form.role]}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs">Initial password
        <input className={inputClass} type="password" autoComplete="new-password" required minLength={12} value={form.password} onChange={set("password")} />
      </label>
      <Button type="submit" disabled={add.isPending}>Add</Button>
      <div className="md:col-span-5"><FormError error={add.error} /></div>
    </form>
  ); // prettier-ignore
}

function MemberRow({ m, me, canManage }: { m: Member; me: Me; canManage: boolean }) {
  const update = useUpdateMember();
  const remove = useRemoveMember();
  const self = m.user_id === me.user?.id;
  const editable = canManage && !self && (m.role !== "owner" || me.role === "owner");
  const assignable = ROLES.filter((r) => r !== "owner" || me.role === "owner");
  return (
    <tr data-testid={`member-${m.email}`}>
      <td className="py-2 pr-3">
        <p className="font-medium">{m.full_name ?? m.email}</p>
        {m.full_name ? <p className="text-xs text-muted-foreground">{m.email}</p> : null}
      </td>
      <td className="py-2 pr-3">
        {editable ? (
          <select aria-label={`Role for ${m.email}`} className={selectClass} value={m.role} disabled={update.isPending}
                  onChange={(e) => update.mutate({ userId: m.user_id, role: e.target.value as Role })}>
            {assignable.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        ) : (
          <Badge tone={m.role === "owner" ? "info" : "neutral"}>{m.role}{self ? " (you)" : ""}</Badge>
        )}
      </td>
      <td className="py-2 pr-3">{m.is_active ? <Badge tone="success">active</Badge> : <Badge tone="warning">deactivated</Badge>}</td>
      <td className="py-2 pr-3 text-xs text-muted-foreground">{formatAge(m.last_login_at)}</td>
      <td className="py-2 text-right">
        {editable ? (
          <div className="flex justify-end gap-1">
            <Button size="sm" variant="outline" disabled={update.isPending}
                    onClick={() => update.mutate({ userId: m.user_id, is_active: !m.is_active })}>
              {m.is_active ? "Deactivate" : "Reactivate"}
            </Button>
            <Button size="sm" variant="ghost" aria-label={`Remove ${m.email}`} disabled={remove.isPending}
                    onClick={() => { if (window.confirm(`Remove ${m.email} from the organization?`)) remove.mutate(m.user_id); }}>
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          </div>
        ) : null}
        <FormError error={update.error ?? remove.error} />
      </td>
    </tr>
  ); // prettier-ignore
}

export function MembersSection({ me }: { me: Me }) {
  const members = useMembers();
  const canManage = hasPermission(me, "members:manage");
  return (
    <Section title="Members" description="Roles nest: viewer < analyst < admin < owner. Deactivating a member signs them out everywhere.">
      {members.isPending ? <LoadingRows rows={3} /> : members.error ? <ErrorState error={members.error} /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Members">
            <thead><tr><th className={th}>Member</th><th className={th}>Role</th><th className={th}>Status</th><th className={th}>Last sign-in</th><th className={th} /></tr></thead>
            <tbody className="divide-y">{members.data.map((m) => <MemberRow key={m.user_id} m={m} me={me} canManage={canManage} />)}</tbody>
          </table>
        </div>
      )}
      {canManage ? <AddMemberForm /> : null}
    </Section>
  ); // prettier-ignore
}

function NewKeyNotice({ created, onDone }: { created: CreatedApiKey; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="status" className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm" data-testid="new-api-key">
      <p className="font-medium">Copy this key now. It will not be shown again.</p>
      <div className="mt-2 flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded bg-card px-2 py-1 font-mono text-xs">{created.key}</code>
        <Button size="sm" variant="outline" onClick={() => { void navigator.clipboard?.writeText(created.key); setCopied(true); }}>
          {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Done</Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">Send it as <code className="font-mono">X-API-Key: …</code> or <code className="font-mono">Authorization: Bearer …</code>.</p>
    </div>
  ); // prettier-ignore
}

export function ApiKeysSection({ me }: { me: Me }) {
  const allowed = hasPermission(me, "api_keys:manage");
  const keys = useApiKeys(allowed);
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [expires, setExpires] = useState("90");
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  if (!allowed) return null;
  return (
    <Section title="API keys" description="For scripts and integrations. Only a SHA-256 hash is stored; a key cannot hold more access than its creator, nor the owner role.">
      {created ? <NewKeyNotice created={created} onDone={() => setCreated(null)} /> : null}
      <form className="flex flex-wrap items-end gap-2" aria-label="Create API key"
            onSubmit={(e) => { e.preventDefault(); create.mutate({ name, role, expires_in_days: expires ? Number(expires) : null },
              { onSuccess: (k) => { setCreated(k); setName(""); } }); }}>
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs">Name<input className={inputClass} required minLength={2} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="flex flex-col gap-1 text-xs">Access
          <select className={`${selectClass} h-9`} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.filter((r) => r !== "owner").map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">Expires
          <select className={`${selectClass} h-9`} value={expires} onChange={(e) => setExpires(e.target.value)}>
            <option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option><option value="">never</option>
          </select>
        </label>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <KeyRound className="size-4" aria-hidden />}
          Create key
        </Button>
      </form>
      <FormError error={create.error ?? revoke.error} />
      {keys.isPending ? <LoadingRows rows={2} /> : keys.error ? <ErrorState error={keys.error} /> : keys.data.length === 0 ? (
        <EmptyState title="No API keys" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="API keys">
            <thead><tr><th className={th}>Name</th><th className={th}>Prefix</th><th className={th}>Scopes</th><th className={th}>Last used</th><th className={th}>Status</th><th className={th} /></tr></thead>
            <tbody className="divide-y">
              {keys.data.map((k) => {
                const expired = k.expires_at != null && Date.parse(k.expires_at) < Date.now();
                return (
                  <tr key={k.id}>
                    <td className="py-2 pr-3 font-medium">{k.name}<p className="text-xs font-normal text-muted-foreground">by {k.created_by ?? "—"}</p></td>
                    <td className="py-2 pr-3 font-mono text-xs">cs_{k.key_prefix}_…</td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">{k.scopes.join(", ")}</td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">{formatAge(k.last_used_at)}</td>
                    <td className="py-2 pr-3">
                      {k.revoked_at ? <Badge tone="danger">revoked</Badge> : expired ? <Badge tone="warning">expired</Badge> : <Badge tone="success">active</Badge>}
                    </td>
                    <td className="py-2 text-right">
                      {!k.revoked_at ? (
                        <Button size="sm" variant="outline" disabled={revoke.isPending}
                                onClick={() => { if (window.confirm(`Revoke "${k.name}"? Clients using it stop working at once.`)) revoke.mutate(k.id); }}>
                          Revoke
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  ); // prettier-ignore
}

export function AuditLogSection({ me }: { me: Me }) {
  const allowed = hasPermission(me, "audit:read");
  const [offset, setOffset] = useState(0);
  const logs = useAuditLogs(offset, allowed);
  if (!allowed) return null;
  return (
    <Section title="Audit log" description="Append-only: entries cannot be edited or deleted, even by the database user.">
      {logs.isPending ? <LoadingRows rows={4} /> : logs.error ? <ErrorState error={logs.error} /> : logs.data.items.length === 0 ? (
        <EmptyState title="No audit entries yet" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Audit log">
              <thead><tr><th className={th}>When</th><th className={th}>Action</th><th className={th}>Actor</th><th className={th}>Details</th><th className={th}>IP</th></tr></thead>
              <tbody className="divide-y">
                {logs.data.items.map((a) => (
                  <tr key={a.id}>
                    <td className="py-1.5 pr-3 text-xs whitespace-nowrap text-muted-foreground" title={a.occurred_at}>{formatAge(a.occurred_at)}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs">{a.action}</td>
                    <td className="py-1.5 pr-3 text-xs">{a.actor_email ?? (a.actor_api_key ? `key: ${a.actor_api_key}` : "—")}</td>
                    <td className="max-w-md truncate py-1.5 pr-3 font-mono text-[11px] text-muted-foreground" title={JSON.stringify(a.changes)}>
                      {Object.keys(a.changes).length ? JSON.stringify(a.changes) : "—"}
                    </td>
                    <td className="py-1.5 font-mono text-[11px] text-muted-foreground">{a.ip_address ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{offset + 1}–{offset + logs.data.items.length} of {logs.data.total}</span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Newer</Button>
              <Button size="sm" variant="outline" disabled={offset + 25 >= logs.data.total} onClick={() => setOffset(offset + 25)}>Older</Button>
            </div>
          </div>
        </>
      )}
    </Section>
  ); // prettier-ignore
}

export function SettingsView() {
  const me = useMe();
  if (me.isPending) return <LoadingRows rows={6} />;
  if (me.error) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <OrganizationSection me={me.data} />
        <AccountSection me={me.data} />
      </div>
      <MembersSection me={me.data} />
      <ApiKeysSection me={me.data} />
      <Section title="Carrier integrations" description="Credentials are configured on the server through environment variables and are never sent to the browser.">
        <CarrierStatusList />
      </Section>
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2"><DataSourcesCard /></div>
        <SystemHealthCard />
      </div>
      <AuditLogSection me={me.data} />
    </div>
  ); // prettier-ignore
}
