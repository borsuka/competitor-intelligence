"use client";

/**
 * Application shell: sidebar, mobile navigation, account menu.
 *
 * Client-side only because it needs the current path for active state and local state
 * for the mobile drawer. Everything it renders is passed in from the server layout, so
 * no data fetching happens here.
 */

import {
  Bell,
  ChevronDown,
  FileText,
  GitCompare,
  LayoutDashboard,
  LogOut,
  Menu,
  Radar,
  Settings,
  Users,
  Waves,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Button } from "@/components/ui/primitives";
import { api, clientFetch } from "@/lib/api";
import type { OrganizationSummary, User } from "@/lib/types";
import { cn, initials } from "@/lib/utils";

const NAV = [
  { href: "", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/competitors", label: "Competitors", icon: Users },
  { href: "/compare", label: "Compare", icon: GitCompare },
  { href: "/monitoring", label: "Monitoring", icon: Waves },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/reports", label: "Reports", icon: FileText },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

export function AppShell({
  orgId,
  user,
  organizations,
  unreadCount,
  children,
}: {
  orgId: string;
  user: User;
  organizations: OrganizationSummary[];
  unreadCount: number;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Navigating on mobile should close the drawer; leaving it open hides the page the
  // user just asked for.
  useEffect(() => setMobileOpen(false), [pathname]);

  const base = `/${orgId}`;
  const current = organizations.find((organization) => organization.id === orgId);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      {/* ------------------------------------------------------------- sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 w-60 border-r border-border bg-surface transition-transform lg:static lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center gap-2 border-b border-border px-4">
          <span className="flex size-7 items-center justify-center rounded-[--radius-control] bg-accent text-accent-ink">
            <Radar className="size-4" aria-hidden />
          </span>
          <span className="text-sm font-semibold tracking-tight text-ink">Sentinel</span>
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X aria-hidden />
          </Button>
        </div>

        <nav aria-label="Main" className="p-3">
          <ul className="space-y-0.5">
            {NAV.map((item) => {
              const href = `${base}${item.href}`;
              const active =
                "exact" in item && item.exact ? pathname === href : pathname.startsWith(href);
              const Icon = item.icon;

              return (
                <li key={item.label}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2.5 rounded-[--radius-control] px-3 py-2 text-sm transition-colors",
                      active
                        ? "bg-accent-soft font-medium text-accent"
                        : "text-ink-muted hover:bg-surface-raised hover:text-ink",
                    )}
                  >
                    <Icon className="size-4 shrink-0" aria-hidden />
                    <span className="truncate">{item.label}</span>
                    {item.label === "Alerts" && unreadCount > 0 ? (
                      <Badge variant="accent" className="ml-auto tabular">
                        {unreadCount > 99 ? "99+" : unreadCount}
                      </Badge>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <OrgSwitcher orgId={orgId} organizations={organizations} />
      </aside>

      {mobileOpen ? (
        <div
          className="fixed inset-0 z-30 bg-ink/20 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      ) : null}

      {/* ---------------------------------------------------------------- main */}
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-canvas/85 px-4 backdrop-blur lg:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
            aria-expanded={mobileOpen}
          >
            <Menu aria-hidden />
          </Button>

          <span className="truncate text-sm font-medium text-ink">{current?.name}</span>

          <div className="ml-auto flex items-center gap-2">
            <Link
              href={`${base}/alerts`}
              className="relative flex size-9 items-center justify-center rounded-[--radius-control] text-ink-muted hover:bg-surface-raised hover:text-ink"
              aria-label={
                unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"
              }
            >
              <Bell className="size-4" aria-hidden />
              {unreadCount > 0 ? (
                <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-critical ring-2 ring-canvas" />
              ) : null}
            </Link>
            <AccountMenu user={user} />
          </div>
        </header>

        <main id="main" className="min-w-0 flex-1 px-4 py-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}

function OrgSwitcher({
  orgId,
  organizations,
}: {
  orgId: string;
  organizations: OrganizationSummary[];
}) {
  // With a single workspace a switcher is noise, so it only appears when it is useful.
  if (organizations.length < 2) return null;

  return (
    <div className="border-t border-border p-3">
      <label htmlFor="org-switcher" className="sr-only">
        Switch workspace
      </label>
      <select
        id="org-switcher"
        defaultValue={orgId}
        onChange={(event) => {
          window.location.href = `/${event.target.value}`;
        }}
        className="h-9 w-full rounded-[--radius-control] border border-border bg-surface px-2 text-sm text-ink"
      >
        {organizations.map((organization) => (
          <option key={organization.id} value={organization.id}>
            {organization.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function AccountMenu({ user }: { user: User }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  async function signOut() {
    setSigningOut(true);
    try {
      await clientFetch(api.auth.logout, { method: "POST" });
    } finally {
      // Even if the call fails the local session is unusable to the user; send them to
      // the sign-in page rather than leaving them in a half-signed-out state.
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-[--radius-control] px-2 py-1.5 text-sm text-ink hover:bg-surface-raised"
      >
        <span className="flex size-6 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent">
          {initials(user.full_name)}
        </span>
        <span className="hidden max-w-32 truncate sm:inline">{user.full_name}</span>
        <ChevronDown className="size-3.5 text-ink-subtle" aria-hidden />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden />
          <div
            role="menu"
            className="animate-in absolute right-0 z-20 mt-1 w-56 rounded-[--radius-card] border border-border bg-surface p-1 shadow-[--shadow-raised]"
          >
            <div className="border-b border-border px-3 py-2">
              <p className="truncate text-sm font-medium text-ink">{user.full_name}</p>
              <p className="truncate text-xs text-ink-muted">{user.email}</p>
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={signOut}
              disabled={signingOut}
              className="mt-1 flex w-full items-center gap-2 rounded-[--radius-control] px-3 py-2 text-sm text-ink-muted hover:bg-surface-raised hover:text-ink disabled:opacity-60"
            >
              <LogOut className="size-4" aria-hidden />
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
