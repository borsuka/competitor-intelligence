"use client";

/**
 * Interactive forms for alerts, reports and settings.
 *
 * Grouped in one module because they share the same shape: a small form, an optimistic
 * disabled state, an inline error from the API, and `router.refresh()` on success so the
 * server components re-render with real data instead of a client-side cache.
 */

import { Loader2, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import {
  Button,
  Callout,
  Field,
  Input,
  Select,
  Textarea,
} from "@/components/ui/primitives";
import { ApiClientError, api, clientFetch } from "@/lib/api";
import type { Competitor, Report } from "@/lib/types";

const CHANGE_TYPES = [
  { value: "price_increased", label: "Price increased" },
  { value: "price_decreased", label: "Price decreased" },
  { value: "plan_added", label: "Plan added" },
  { value: "plan_removed", label: "Plan removed" },
  { value: "product_added", label: "Product added" },
  { value: "product_removed", label: "Product removed" },
  { value: "feature_added", label: "Feature added" },
  { value: "positioning_changed", label: "Positioning changed" },
  { value: "page_added", label: "New page published" },
  { value: "content_changed", label: "Content changed" },
] as const;

function useSubmit() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, onDone?: () => void) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onDone?.();
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiClientError ? caught.displayMessage : "Something went wrong.",
      );
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, run, router };
}

/* -------------------------------------------------------------- alert rules */

export function CreateAlertRule({
  orgId,
  competitors,
}: {
  orgId: string;
  competitors: Competitor[];
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { busy, error, run } = useSubmit();
  const [types, setTypes] = useState<string[]>([]);

  function submit(formData: FormData) {
    const name = String(formData.get("name") ?? "").trim();
    const competitorId = String(formData.get("competitor_id") ?? "");
    const minSeverity = String(formData.get("min_severity") ?? "medium");

    void run(
      () =>
        clientFetch(api.org(orgId).alerts, {
          method: "POST",
          body: {
            name: name || "Alert",
            competitor_id: competitorId || null,
            change_types: types,
            min_severity: minSeverity,
            channels: ["in_app"],
          },
        }),
      () => {
        dialogRef.current?.close();
        setTypes([]);
      },
    );
  }

  return (
    <>
      <Button onClick={() => dialogRef.current?.showModal()}>
        <Plus aria-hidden />
        New alert
      </Button>

      <dialog
        ref={dialogRef}
        aria-labelledby="alert-rule-title"
        className="w-[min(30rem,calc(100vw-2rem))] rounded-[--radius-card] border border-border bg-surface p-0 text-ink shadow-[--shadow-raised] backdrop:bg-ink/30"
      >
        <form action={submit}>
          <div className="border-b border-border px-5 py-4">
            <h2 id="alert-rule-title" className="text-sm font-semibold text-ink">
              New alert rule
            </h2>
            <p className="mt-0.5 text-sm text-ink-muted">
              You are notified in-app when a matching change is detected.
            </p>
          </div>

          <div className="space-y-4 px-5 py-4">
            {error ? <Callout tone="danger">{error}</Callout> : null}

            <Field label="Name" htmlFor="alert-name">
              <Input id="alert-name" name="name" placeholder="Pricing moves" required />
            </Field>

            <Field
              label="Competitor"
              htmlFor="alert-competitor"
              hint="Leave as All to watch every competitor, including ones added later."
            >
              <Select id="alert-competitor" name="competitor_id" defaultValue="">
                <option value="">All competitors</option>
                {competitors.map((competitor) => (
                  <option key={competitor.id} value={competitor.id}>
                    {competitor.name}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Minimum severity"
              htmlFor="alert-severity"
              hint="Severity is derived from the size of the change — a 20% price rise is high."
            >
              <Select id="alert-severity" name="min_severity" defaultValue="medium">
                <option value="low">Low and above (everything)</option>
                <option value="medium">Medium and above</option>
                <option value="high">High only</option>
              </Select>
            </Field>

            <fieldset>
              <legend className="text-sm font-medium text-ink">Change types</legend>
              <p className="mt-0.5 text-sm text-ink-subtle">
                Select none to be alerted about every type.
              </p>
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {CHANGE_TYPES.map((type) => (
                  <label key={type.value} className="flex items-center gap-2 text-sm text-ink-muted">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-border"
                      checked={types.includes(type.value)}
                      onChange={() =>
                        setTypes((current) =>
                          current.includes(type.value)
                            ? current.filter((value) => value !== type.value)
                            : [...current, type.value],
                        )
                      }
                    />
                    {type.label}
                  </label>
                ))}
              </div>
            </fieldset>
          </div>

          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <Button
              type="button"
              variant="secondary"
              onClick={() => dialogRef.current?.close()}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
              Create rule
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}

export function ToggleAlertRule({
  orgId,
  ruleId,
  isActive,
}: {
  orgId: string;
  ruleId: string;
  isActive: boolean;
}) {
  const { busy, run } = useSubmit();

  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={busy}
      onClick={() =>
        void run(() =>
          clientFetch(api.org(orgId).alert(ruleId), {
            method: "PATCH",
            body: { is_active: !isActive },
          }),
        )
      }
    >
      {isActive ? "Pause" : "Resume"}
    </Button>
  );
}

export function DeleteAlertRule({ orgId, ruleId }: { orgId: string; ruleId: string }) {
  const { busy, run } = useSubmit();
  const [confirming, setConfirming] = useState(false);

  if (!confirming) {
    return (
      <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
        Delete
      </Button>
    );
  }

  return (
    <span className="flex items-center gap-1">
      <Button
        variant="danger"
        size="sm"
        disabled={busy}
        onClick={() =>
          void run(() =>
            clientFetch(api.org(orgId).alert(ruleId), { method: "DELETE" }),
          )
        }
      >
        Confirm
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
        Cancel
      </Button>
    </span>
  );
}

export function MarkNotificationsRead({ orgId }: { orgId: string }) {
  const { busy, run } = useSubmit();

  return (
    <Button
      variant="secondary"
      size="sm"
      disabled={busy}
      onClick={() =>
        void run(() =>
          clientFetch(api.org(orgId).markRead, { method: "POST", body: {} }),
        )
      }
    >
      Mark all read
    </Button>
  );
}

/* ------------------------------------------------------------------ reports */

export function GenerateReport({
  orgId,
  competitors,
}: {
  orgId: string;
  competitors: Competitor[];
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { busy, error, run, router } = useSubmit();
  const [reportType, setReportType] = useState("weekly_intelligence");

  const needsCompetitor = reportType === "competitor_overview";
  const needsMany = reportType === "comparison";

  function submit(formData: FormData) {
    const ids = formData.getAll("competitor_ids").map(String).filter(Boolean);

    void run(
      async () => {
        const report = await clientFetch<Report>(api.org(orgId).reports, {
          method: "POST",
          body: {
            report_type: reportType,
            competitor_ids: ids,
            period_days: Number(formData.get("period_days") ?? 30),
          },
        });
        dialogRef.current?.close();
        router.push(`/${orgId}/reports/${report.id}`);
      },
    );
  }

  return (
    <>
      <Button onClick={() => dialogRef.current?.showModal()}>
        <Plus aria-hidden />
        Generate report
      </Button>

      <dialog
        ref={dialogRef}
        aria-labelledby="report-title"
        className="w-[min(30rem,calc(100vw-2rem))] rounded-[--radius-card] border border-border bg-surface p-0 text-ink shadow-[--shadow-raised] backdrop:bg-ink/30"
      >
        <form action={submit}>
          <div className="border-b border-border px-5 py-4">
            <h2 id="report-title" className="text-sm font-semibold text-ink">
              Generate a report
            </h2>
            <p className="mt-0.5 text-sm text-ink-muted">
              Built from stored analyses. Nothing is re-crawled, so this is instant and free.
            </p>
          </div>

          <div className="space-y-4 px-5 py-4">
            {error ? <Callout tone="danger">{error}</Callout> : null}

            <Field label="Report type" htmlFor="report-type">
              <Select
                id="report-type"
                value={reportType}
                onChange={(event) => setReportType(event.target.value)}
              >
                <option value="weekly_intelligence">Weekly intelligence</option>
                <option value="monthly_competitive">Monthly competitive report</option>
                <option value="competitor_overview">Single competitor overview</option>
                <option value="comparison">Comparison</option>
              </Select>
            </Field>

            {needsCompetitor || needsMany ? (
              <Field
                label={needsMany ? "Competitors" : "Competitor"}
                htmlFor="report-competitors"
                hint={needsMany ? "Select two or more (Ctrl or Cmd to multi-select)." : undefined}
              >
                <select
                  id="report-competitors"
                  name="competitor_ids"
                  multiple={needsMany}
                  size={needsMany ? 6 : undefined}
                  required
                  className="w-full rounded-[--radius-control] border border-border bg-surface px-3 py-2 text-sm text-ink"
                >
                  {competitors.map((competitor) => (
                    <option key={competitor.id} value={competitor.id}>
                      {competitor.name}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}

            <Field label="Period" htmlFor="report-period">
              <Select id="report-period" name="period_days" defaultValue="30">
                <option value="7">Last 7 days</option>
                <option value="30">Last 30 days</option>
                <option value="90">Last 90 days</option>
              </Select>
            </Field>
          </div>

          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <Button type="button" variant="secondary" onClick={() => dialogRef.current?.close()}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
              Generate
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}

/* ----------------------------------------------------------------- settings */

export function CompanyProfileForm({
  orgId,
  defaults,
}: {
  orgId: string;
  defaults: {
    name: string;
    own_company_name: string | null;
    own_company_url: string | null;
    own_company_description: string | null;
  };
}) {
  const { busy, error, run } = useSubmit();
  const [saved, setSaved] = useState(false);

  function submit(formData: FormData) {
    setSaved(false);
    void run(
      () =>
        clientFetch(api.org(orgId).root, {
          method: "PATCH",
          body: {
            name: String(formData.get("name") ?? "").trim(),
            own_company_name: String(formData.get("own_company_name") ?? "").trim() || null,
            own_company_url: String(formData.get("own_company_url") ?? "").trim() || null,
            own_company_description:
              String(formData.get("own_company_description") ?? "").trim() || null,
          },
        }),
      () => setSaved(true),
    );
  }

  return (
    <form action={submit} className="space-y-4">
      {error ? <Callout tone="danger">{error}</Callout> : null}
      {saved ? <Callout tone="info">Saved.</Callout> : null}

      <Field label="Workspace name" htmlFor="org-name">
        <Input id="org-name" name="name" defaultValue={defaults.name} required />
      </Field>

      <div className="border-t border-border pt-4">
        <h3 className="text-sm font-medium text-ink">Your company</h3>
        <p className="mt-0.5 text-sm text-ink-muted">
          Recommendations compare each competitor against you. Without this, the AI has
          nothing concrete to advise on and the recommendations panel stays empty by design.
        </p>
      </div>

      <Field label="Company name" htmlFor="own-name">
        <Input
          id="own-name"
          name="own_company_name"
          defaultValue={defaults.own_company_name ?? ""}
        />
      </Field>

      <Field label="Website" htmlFor="own-url">
        <Input
          id="own-url"
          name="own_company_url"
          type="url"
          placeholder="https://yourcompany.com"
          defaultValue={defaults.own_company_url ?? ""}
        />
      </Field>

      <Field
        label="What you do"
        htmlFor="own-description"
        hint="Two or three sentences: what you sell, who buys it, and how you position yourself."
      >
        <Textarea
          id="own-description"
          name="own_company_description"
          rows={4}
          defaultValue={defaults.own_company_description ?? ""}
        />
      </Field>

      <Button type="submit" disabled={busy}>
        {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
        Save changes
      </Button>
    </form>
  );
}

export function InviteMemberForm({ orgId }: { orgId: string }) {
  const { busy, error, run } = useSubmit();
  const [inviteToken, setInviteToken] = useState<string | null>(null);

  function submit(formData: FormData) {
    setInviteToken(null);
    void run(async () => {
      const result = await clientFetch<{ invite_token: string | null }>(
        api.org(orgId).invitations,
        {
          method: "POST",
          body: {
            email: String(formData.get("email") ?? "").trim(),
            role: String(formData.get("role") ?? "member"),
          },
        },
      );
      // Outside production the API returns the token, because there is no mail transport
      // configured to deliver it.
      setInviteToken(result.invite_token);
    });
  }

  return (
    <form action={submit} className="space-y-4">
      {error ? <Callout tone="danger">{error}</Callout> : null}
      {inviteToken ? (
        <Callout tone="info" title="Invitation created">
          <p>No email transport is configured, so send this token to them yourself:</p>
          <code className="mt-1 block break-all rounded bg-surface px-2 py-1 font-mono text-xs">
            {inviteToken}
          </code>
        </Callout>
      ) : null}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <Field label="Email" htmlFor="invite-email">
            <Input id="invite-email" name="email" type="email" required />
          </Field>
        </div>
        <div className="sm:w-40">
          <Field label="Role" htmlFor="invite-role">
            <Select id="invite-role" name="role" defaultValue="member">
              <option value="admin">Admin</option>
              <option value="member">Member</option>
              <option value="viewer">Viewer</option>
            </Select>
          </Field>
        </div>
        <Button type="submit" disabled={busy}>
          {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
          Invite
        </Button>
      </div>
    </form>
  );
}
