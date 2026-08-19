import { Card, CardBody, Skeleton } from "@/components/ui/primitives";

export default function CompetitorLoading() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-4 w-32" />

      <div className="flex items-start gap-3">
        <Skeleton className="size-10 rounded" />
        <div className="space-y-2">
          <Skeleton className="h-6 w-52" />
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-5 w-24 rounded-full" />
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {Array.from({ length: 3 }).map((_, index) => (
            <Card key={index}>
              <CardBody className="space-y-3">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
                <Skeleton className="h-4 w-3/4" />
              </CardBody>
            </Card>
          ))}
        </div>

        <div className="space-y-6">
          <Card>
            <CardBody className="flex flex-col items-center gap-4">
              <Skeleton className="size-24 rounded-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </CardBody>
          </Card>
        </div>
      </div>

      <span className="sr-only" role="status">
        Loading competitor profile
      </span>
    </div>
  );
}
