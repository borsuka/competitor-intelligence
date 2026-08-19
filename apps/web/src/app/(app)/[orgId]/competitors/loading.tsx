import { Card, Skeleton } from "@/components/ui/primitives";

export default function CompetitorsLoading() {
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <Skeleton className="h-7 w-44" />
          <Skeleton className="mt-2 h-4 w-64" />
        </div>
        <Skeleton className="h-9 w-36" />
      </div>

      <Skeleton className="h-9 w-full" />

      <Card>
        <div className="divide-y divide-border">
          {Array.from({ length: 8 }).map((_, index) => (
            <div key={index} className="flex items-center gap-3 px-4 py-3.5">
              <Skeleton className="size-5 rounded" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-3 w-32" />
              </div>
              <Skeleton className="h-4 w-10" />
              <Skeleton className="hidden h-5 w-20 rounded-full md:block" />
            </div>
          ))}
        </div>
      </Card>

      <span className="sr-only" role="status">
        Loading competitors
      </span>
    </div>
  );
}
