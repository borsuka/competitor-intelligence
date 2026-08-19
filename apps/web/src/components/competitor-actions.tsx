"use client";

/**
 * Interactive competitor controls: add, re-analyse, and live job progress.
 *
 * The add form uses a native `<dialog>`, so focus trapping, Escape to close and the
 * backdrop come from the platform rather than from several hundred lines of our own
 * focus management.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Plus, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  Callout,
  Field,
  Input,
  Progress,
  Select,
  Textarea,
} from "@/components/ui/primitives";
import { ApiClientError, api, clientFetch } from "@/lib/api";
import type { Competitor, Job } from "@/lib/types";
import { humanize } from "@/lib/utils";

const schema = z.object({
  website_url: z
    .string()
    .min(3, "Enter the competitor's website.")
    .refine(
      (value) => /^[^\s]+\.[^\s]{2,}$/.test(value.replace(/^https?:\/\//, "")),
      "That does not look like a website address.",
    ),
  name: z.string().max(160).optional(),
  category: z.string().max(80).optional(),
  importance: z.enum(["low", "medium", "high", "critical"]),
  notes: z.string().max(5000).optional(),
  analyze_now: z.boolean(),
});

type Values = z.infer<typeof schema>;

export function AddCompetitorButton({ orgId }: { orgId: string }) {
  const router = useRouter();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { importance: "medium", analyze_now: true },
  });

  const close = useCallback(() => {
    dialogRef.current?.close();
    setFormError(null);
    reset();
  }, [reset]);

  async function onSubmit(values: Values) {
    setFormError(null);
    try {
      await clientFetch<Competitor>(api.org(orgId).competitors, {
        method: "POST",
        body: {
          ...values,
          name: values.name || undefined,
          category: values.category || undefined,
          notes: values.notes || undefined,
        },
      });
      close();
      router.refresh();
    } catch (error) {
      if (error instanceof ApiClientError) {
        // The API's messages are written for users — quota, duplicate domain, unsafe
        // URL all say something actionable — so they are shown as-is.
        setFormError(error.displayMessage);
      } else {
        setFormError("Could not reach the server. Try again.");
      }
    }
  }

  return (
    <>
      <Button onClick={() => dialogRef.current?.showModal()}>
        <Plus aria-hidden />
        Add competitor
      </Button>

      <dialog
        ref={dialogRef}
        onClose={close}
        aria-labelledby="add-competitor-title"
        className="w-[min(32rem,calc(100vw-2rem))] rounded-[--radius-card] border border-border bg-surface p-0 text-ink shadow-[--shadow-raised] backdrop:bg-ink/30 backdrop:backdrop-blur-[1px]"
      >
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <div className="border-b border-border px-5 py-4">
            <h2 id="add-competitor-title" className="text-sm font-semibold text-ink">
              Add a competitor
            </h2>
            <p className="mt-0.5 text-sm text-ink-muted">
              Their public website is crawled and analysed. Only pages anyone can reach are
              read.
            </p>
          </div>

          <div className="space-y-4 px-5 py-4">
            {formError ? <Callout tone="danger">{formError}</Callout> : null}

            <Field
              label="Website URL"
              htmlFor="website_url"
              hint="For example: stripe.com"
              error={errors.website_url?.message}
            >
              <Input
                id="website_url"
                placeholder="competitor.com"
                autoFocus
                aria-invalid={Boolean(errors.website_url)}
                {...register("website_url")}
              />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Name"
                htmlFor="name"
                hint="Defaults to the domain."
                error={errors.name?.message}
              >
                <Input id="name" {...register("name")} />
              </Field>

              <Field label="Category" htmlFor="category" error={errors.category?.message}>
                <Input id="category" placeholder="Analytics" {...register("category")} />
              </Field>
            </div>

            <Field
              label="Importance"
              htmlFor="importance"
              hint="Drives how often they are re-checked: critical every 12h, low weekly."
            >
              <Select id="importance" {...register("importance")}>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </Select>
            </Field>

            <Field label="Notes" htmlFor="notes" error={errors.notes?.message}>
              <Textarea id="notes" rows={2} {...register("notes")} />
            </Field>

            <label className="flex items-start gap-2 text-sm text-ink-muted">
              <input
                type="checkbox"
                className="mt-0.5 size-4 rounded border-border"
                {...register("analyze_now")}
              />
              <span>
                Analyse immediately. This crawls up to 25 pages and counts against your
                monthly quota.
              </span>
            </label>
          </div>

          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <Button type="button" variant="secondary" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? <Loader2 className="animate-spin" aria-hidden /> : null}
              {isSubmitting ? "Adding…" : "Add competitor"}
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}

export function AnalyzeButton({
  orgId,
  competitorId,
  disabled,
}: {
  orgId: string;
  competitorId: string;
  disabled?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      await clientFetch<Job>(api.org(orgId).analyze(competitorId), {
        method: "POST",
        body: { depth: "standard" },
      });
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiClientError
          ? caught.displayMessage
          : "Could not start the analysis.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="secondary" onClick={analyze} disabled={busy || disabled}>
        <RefreshCw className={busy ? "animate-spin" : undefined} aria-hidden />
        {busy ? "Starting…" : "Refresh analysis"}
      </Button>
      {error ? (
        <p role="alert" className="text-xs text-critical">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Live job progress.
 *
 * Polls the job endpoint while a job is active. Polling rather than a socket: a crawl
 * takes minutes and finishes once, so a persistent connection per viewer would cost more
 * than it saves. The interval backs off as the job ages.
 */
export function JobProgress({
  orgId,
  job,
}: {
  orgId: string;
  job: Job;
}) {
  const router = useRouter();
  const [current, setCurrent] = useState(job);

  useEffect(() => {
    if (current.status !== "pending" && current.status !== "running") return;

    let cancelled = false;
    let attempts = 0;

    const tick = async () => {
      try {
        const updated = await clientFetch<Job>(api.org(orgId).job(current.id));
        if (cancelled) return;
        setCurrent(updated);
        if (updated.status === "completed" || updated.status === "failed") {
          router.refresh();
          return;
        }
      } catch {
        // A transient failure should not stop the poll; the next tick tries again.
      }
      attempts += 1;
      if (!cancelled) {
        // 2s at first, easing to 10s: most of the wait is the crawl, not the queue.
        timer = window.setTimeout(tick, Math.min(2000 + attempts * 1000, 10_000));
      }
    };

    let timer = window.setTimeout(tick, 2000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [current.id, current.status, orgId, router]);

  if (current.status === "completed") return null;

  if (current.status === "failed") {
    return (
      <Callout tone="danger" title="The last analysis failed">
        {current.error_message ?? "The analysis could not be completed."}
      </Callout>
    );
  }

  return (
    <div className="rounded-[--radius-card] border border-border bg-surface-raised p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-ink">
          {current.stage ? humanize(current.stage) : "Queued"}
        </p>
        <span className="tabular text-sm text-ink-muted">{current.progress}%</span>
      </div>
      <Progress value={current.progress} label="Analysis progress" className="mt-2" />
      <p className="mt-2 text-xs text-ink-subtle">
        Crawling and analysing usually takes one to three minutes. You can leave this page.
      </p>
    </div>
  );
}
