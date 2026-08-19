import { Radar } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Sign-in shell.
 *
 * Two columns on desktop — form on the left, a short statement of what the product does
 * on the right. The right column is decorative and is hidden below `lg`, where the form
 * is the only thing that matters.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      <main id="main" className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2">
            <span className="flex size-8 items-center justify-center rounded-[--radius-control] bg-accent text-accent-ink">
              <Radar className="size-4" aria-hidden />
            </span>
            <span className="text-base font-semibold tracking-tight text-ink">Sentinel</span>
          </div>
          {children}
        </div>
      </main>

      <aside
        aria-hidden
        className="hidden border-l border-border bg-surface-raised lg:flex lg:items-center lg:px-14"
      >
        <div className="max-w-md">
          <p className="text-2xl font-semibold leading-snug tracking-tight text-ink">
            Know what your competitors changed, before your customers tell you.
          </p>
          <ul className="mt-8 space-y-4 text-sm text-ink-muted">
            <li className="flex gap-3">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
              <span>
                Add a competitor URL. Their public site is crawled, structured and scored.
              </span>
            </li>
            <li className="flex gap-3">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
              <span>
                Every fact is labelled <strong className="text-ink">observed</strong> or{" "}
                <strong className="text-ink">AI inference</strong>, so you know what you
                can act on.
              </span>
            </li>
            <li className="flex gap-3">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
              <span>
                Price moves, new products and repositioning raise alerts. Cosmetic page
                edits do not.
              </span>
            </li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
