import { Card, CardBody, Skeleton } from "@/components/ui/primitives";

/**
 * Overview skeleton.
 *
 * Mirrors the real layout — four metric cards, then the two-column body — so the page
 * does not jump when the data arrives. A centred spinner would be less work and a worse
 * experience.
 */
export default function OverviewLoading() {
  return (
    <div className="space-y-6">
      <div>
        <Skeleton className="h-7 w-40" />
        <Skeleton className="mt-2 h-4 w-72" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Card key={index}>
            <CardBody>
              <Skeleton className="h-3 w-24" />
              <Skeleton className="mt-3 h-8 w-16" />
              <Skeleton className="mt-2 h-3 w-28" />
            </CardBody>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardBody className="space-y-4">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="flex items-center gap-3">
                <Skeleton className="size-5 rounded" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="h-1.5 w-40" />
              </div>
            ))}
          </CardBody>
        </Card>

        <Card>
          <CardBody className="space-y-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="space-y-1.5">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-3 w-28" />
              </div>
            ))}
          </CardBody>
        </Card>
      </div>

      <span className="sr-only" role="status">
        Loading your dashboard
      </span>
    </div>
  );
}
