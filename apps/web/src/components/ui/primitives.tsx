/**
 * UI primitives.
 *
 * Built directly on semantic HTML rather than pulled from a component library. The set
 * this product needs is small, and the two hard parts — a modal dialog and a disclosure —
 * are handled correctly by native `<dialog>` and `<details>`, which bring focus
 * trapping, Escape handling and screen-reader semantics without a dependency.
 *
 * Everything here is a server component unless it needs state; only `Dialog` and
 * `CopyButton` opt into the client.
 */

import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------- Button */

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-[--radius-control] text-sm font-medium " +
    "transition-colors disabled:pointer-events-none disabled:opacity-50 " +
    "whitespace-nowrap [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-ink hover:bg-accent-hover",
        secondary: "bg-surface text-ink border border-border hover:bg-surface-raised",
        ghost: "text-ink-muted hover:bg-surface-raised hover:text-ink",
        danger: "bg-critical text-white hover:opacity-90",
        link: "text-accent underline-offset-4 hover:underline p-0 h-auto",
      },
      size: {
        sm: "h-8 px-3",
        md: "h-9 px-4",
        lg: "h-10 px-5",
        icon: "h-9 w-9 p-0",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends ComponentPropsWithoutRef<"button">,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

/* ---------------------------------------------------------------------- Card */

export function Card({ className, ...props }: ComponentPropsWithoutRef<"section">) {
  return (
    <section
      className={cn(
        "rounded-[--radius-card] border border-border bg-surface shadow-[--shadow-card]",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 border-b border-border px-5 py-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-sm text-ink-muted">{description}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function CardBody({ className, ...props }: ComponentPropsWithoutRef<"div">) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}

/* --------------------------------------------------------------------- Badge */

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        neutral: "bg-surface-raised text-ink-muted border border-border",
        accent: "bg-accent-soft text-accent",
        outline: "border border-border-strong text-ink-muted",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends ComponentPropsWithoutRef<"span">,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

/* --------------------------------------------------------------- Form fields */

export function Label({ className, ...props }: ComponentPropsWithoutRef<"label">) {
  return (
    <label className={cn("block text-sm font-medium text-ink", className)} {...props} />
  );
}

export function Input({ className, ...props }: ComponentPropsWithoutRef<"input">) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-[--radius-control] border border-border bg-surface px-3 text-sm",
        "text-ink placeholder:text-ink-subtle",
        "focus:border-accent focus:outline-none focus-visible:outline-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "aria-[invalid=true]:border-critical",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: ComponentPropsWithoutRef<"textarea">) {
  return (
    <textarea
      className={cn(
        "w-full rounded-[--radius-control] border border-border bg-surface px-3 py-2 text-sm",
        "text-ink placeholder:text-ink-subtle focus:border-accent focus:outline-none",
        "aria-[invalid=true]:border-critical",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: ComponentPropsWithoutRef<"select">) {
  return (
    <select
      className={cn(
        "h-9 w-full rounded-[--radius-control] border border-border bg-surface px-3 text-sm",
        "text-ink focus:border-accent focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {/* aria-live so a validation message reaches a screen reader when it appears. */}
      {error ? (
        <p id={`${htmlFor}-error`} role="alert" className="text-sm text-critical">
          {error}
        </p>
      ) : hint ? (
        <p id={`${htmlFor}-hint`} className="text-sm text-ink-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ Skeleton */

export function Skeleton({ className, ...props }: ComponentPropsWithoutRef<"div">) {
  return (
    <div
      aria-hidden
      className={cn("skeleton rounded-[--radius-control]", className)}
      {...props}
    />
  );
}

/* --------------------------------------------------------------------- Table */

export function Table({
  caption,
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<"table"> & { caption: string }) {
  return (
    // A horizontally scrollable wrapper: dense tables must never make the page scroll.
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full text-sm", className)} {...props}>
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function Th({ className, ...props }: ComponentPropsWithoutRef<"th">) {
  return (
    <th
      scope="col"
      className={cn(
        "border-b border-border px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wide text-ink-subtle",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: ComponentPropsWithoutRef<"td">) {
  return (
    <td
      className={cn("border-b border-border px-4 py-3 align-middle text-ink", className)}
      {...props}
    />
  );
}

/* ---------------------------------------------------------------- Empty/Error */

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon ? (
        <div className="mb-3 flex size-11 items-center justify-center rounded-full bg-surface-raised text-ink-subtle">
          {icon}
        </div>
      ) : null}
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <p className="mx-auto mt-1 max-w-sm text-sm text-ink-muted">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function Callout({
  tone = "neutral",
  title,
  children,
  icon,
}: {
  tone?: "neutral" | "warning" | "danger" | "info";
  title?: string;
  children: ReactNode;
  icon?: ReactNode;
}) {
  const tones = {
    neutral: "border-border bg-surface-raised text-ink-muted",
    info: "border-accent/30 bg-accent-soft text-ink",
    warning: "border-moderate/40 bg-moderate-soft text-ink",
    danger: "border-critical/40 bg-critical-soft text-ink",
  } as const;

  return (
    <div className={cn("flex gap-3 rounded-[--radius-card] border p-4 text-sm", tones[tone])}>
      {icon ? <div className="mt-0.5 shrink-0">{icon}</div> : null}
      <div className="min-w-0 space-y-1">
        {title ? <p className="font-medium text-ink">{title}</p> : null}
        <div className="text-ink-muted">{children}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Progress */

export function Progress({
  value,
  label,
  className,
}: {
  value: number;
  label: string;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-raised", className)}
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-500"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ Sections */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export { buttonVariants };
