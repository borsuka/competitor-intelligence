import Link from "next/link";

export default function NotFound() {
  return (
    <main
      id="main"
      className="flex min-h-dvh flex-col items-center justify-center px-6 text-center"
    >
      <p className="text-sm font-medium text-accent">404</p>
      <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">
        This page does not exist
      </h1>
      <p className="mt-1 max-w-sm text-sm text-ink-muted">
        The link may be out of date, or the page may have moved.
      </p>
      <Link
        href="/"
        className="mt-6 rounded-[--radius-control] bg-accent px-4 py-2 text-sm font-medium text-accent-ink hover:bg-accent-hover"
      >
        Go to your dashboard
      </Link>
    </main>
  );
}
