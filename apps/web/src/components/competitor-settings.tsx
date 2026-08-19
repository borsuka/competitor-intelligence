"use client";

/**
 * Editing, archiving and deleting a competitor.
 *
 * Archive and delete are deliberately different actions with different weights. Archiving
 * stops the crawling — which is what a user usually means — and is reversible from the
 * archived list. Deleting is the destructive one, so it asks, says what it does, and
 * requires the competitor's name to be typed.
 */

import { Archive, Loader2, Settings2, Trash2, Undo2 } from "lucide-react";
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
import type { Competitor } from "@/lib/types";

function useMutation() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** ``navigates`` skips the reload for actions that route somewhere else instead. */
  async function run(
    action: () => Promise<unknown>,
    after?: () => void,
    { navigates = false }: { navigates?: boolean } = {},
  ) {
    setBusy(true);
    setError(null);
    try {
      await action();
      after?.();
      // See the note in forms.tsx: router.refresh() left stale state on screen often
      // enough that a mutation could not be trusted to show its own result.
      if (!navigates) window.location.reload();
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

export function CompetitorSettings({
  orgId,
  competitor,
}: {
  orgId: string;
  competitor: Competitor;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { busy, error, run } = useMutation();

  function submit(formData: FormData) {
    const tags = String(formData.get("tags") ?? "")
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);

    void run(
      () =>
        clientFetch(api.org(orgId).competitor(competitor.id), {
          method: "PATCH",
          body: {
            name: String(formData.get("name") ?? "").trim(),
            category: String(formData.get("category") ?? "").trim() || null,
            notes: String(formData.get("notes") ?? "").trim() || null,
            tags,
            importance: String(formData.get("importance") ?? competitor.importance),
            monitoring_enabled: formData.get("monitoring_enabled") === "on",
          },
        }),
      () => dialogRef.current?.close(),
    );
  }

  return (
    <>
      <Button variant="secondary" onClick={() => dialogRef.current?.showModal()}>
        <Settings2 aria-hidden />
        Edit
      </Button>

      <dialog
        ref={dialogRef}
        aria-labelledby="competitor-settings-title"
        className="w-[min(32rem,calc(100vw-2rem))] rounded-[--radius-card] border border-border bg-surface p-0 text-ink shadow-[--shadow-raised] backdrop:bg-ink/30"
      >
        <form action={submit}>
          <div className="border-b border-border px-5 py-4">
            <h2 id="competitor-settings-title" className="text-sm font-semibold text-ink">
              Edit {competitor.name}
            </h2>
            <p className="mt-0.5 text-sm text-ink-muted">
              The website URL cannot be changed. Track a different site by adding it as a
              new competitor, so this one keeps its history.
            </p>
          </div>

          <div className="space-y-4 px-5 py-4">
            {error ? <Callout tone="danger">{error}</Callout> : null}

            <Field label="Name" htmlFor="edit-name">
              <Input id="edit-name" name="name" defaultValue={competitor.name} required />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Category" htmlFor="edit-category">
                <Input
                  id="edit-category"
                  name="category"
                  defaultValue={competitor.category ?? ""}
                />
              </Field>

              <Field
                label="Importance"
                htmlFor="edit-importance"
                hint="Sets the re-crawl interval."
              >
                <Select
                  id="edit-importance"
                  name="importance"
                  defaultValue={competitor.importance}
                >
                  <option value="critical">Critical — every 12 hours</option>
                  <option value="high">High — daily</option>
                  <option value="medium">Medium — every 3 days</option>
                  <option value="low">Low — weekly</option>
                </Select>
              </Field>
            </div>

            <Field
              label="Tags"
              htmlFor="edit-tags"
              hint="Comma separated. Up to 12."
            >
              <Input
                id="edit-tags"
                name="tags"
                defaultValue={competitor.tags.join(", ")}
                placeholder="direct, enterprise"
              />
            </Field>

            <Field label="Notes" htmlFor="edit-notes">
              <Textarea id="edit-notes" name="notes" rows={3} defaultValue={competitor.notes ?? ""} />
            </Field>

            <label className="flex items-start gap-2 text-sm text-ink-muted">
              <input
                type="checkbox"
                name="monitoring_enabled"
                defaultChecked={competitor.monitoring_enabled}
                className="mt-0.5 size-4 rounded border-border"
              />
              <span>
                Keep monitoring this competitor. Turning it off stops scheduled crawls and
                the spend that comes with them; you can still analyse manually.
              </span>
            </label>
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
              Save changes
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}

export function ArchiveCompetitorButton({
  orgId,
  competitor,
}: {
  orgId: string;
  competitor: Competitor;
}) {
  const { busy, run } = useMutation();
  const archived = competitor.status === "archived";

  return (
    <Button
      variant="secondary"
      disabled={busy}
      onClick={() =>
        void run(() =>
          clientFetch(api.org(orgId).competitor(competitor.id), {
            method: "PATCH",
            body: { status: archived ? "active" : "archived" },
          }),
        )
      }
    >
      {archived ? <Undo2 aria-hidden /> : <Archive aria-hidden />}
      {archived ? "Restore" : "Archive"}
    </Button>
  );
}

export function DeleteCompetitorButton({
  orgId,
  competitor,
}: {
  orgId: string;
  competitor: Competitor;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { busy, error, run, router } = useMutation();
  const [confirmation, setConfirmation] = useState("");

  // Typing the name is not ceremony: it is the difference between a slip and a decision,
  // and this removes months of collected history from the user's view.
  const canDelete = confirmation.trim() === competitor.name;

  return (
    <>
      <Button variant="ghost" onClick={() => dialogRef.current?.showModal()}>
        <Trash2 aria-hidden />
        Delete
      </Button>

      <dialog
        ref={dialogRef}
        aria-labelledby="delete-competitor-title"
        className="w-[min(28rem,calc(100vw-2rem))] rounded-[--radius-card] border border-border bg-surface p-0 text-ink shadow-[--shadow-raised] backdrop:bg-ink/30"
      >
        <div className="border-b border-border px-5 py-4">
          <h2 id="delete-competitor-title" className="text-sm font-semibold text-ink">
            Delete {competitor.name}?
          </h2>
        </div>

        <div className="space-y-4 px-5 py-4">
          {error ? <Callout tone="danger">{error}</Callout> : null}

          <p className="text-sm text-ink-muted">
            This removes the competitor and everything collected about them — analyses,
            pricing history and detected changes — from your workspace.
          </p>
          <Callout tone="neutral">
            If you only want to stop crawling them, <strong className="text-ink">archive</strong>{" "}
            instead. Archived competitors keep their history and can be restored.
          </Callout>

          <Field
            label={`Type "${competitor.name}" to confirm`}
            htmlFor="delete-confirm"
          >
            <Input
              id="delete-confirm"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="off"
            />
          </Field>
        </div>

        <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button
            variant="secondary"
            onClick={() => {
              setConfirmation("");
              dialogRef.current?.close();
            }}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={!canDelete || busy}
            onClick={() =>
              void run(
                () =>
                  clientFetch(api.org(orgId).competitor(competitor.id), {
                    method: "DELETE",
                  }),
                () => {
                  dialogRef.current?.close();
                  router.push(`/${orgId}/competitors`);
                },
                { navigates: true },
              )
            }
          >
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
            Delete permanently
          </Button>
        </div>
      </dialog>
    </>
  );
}
