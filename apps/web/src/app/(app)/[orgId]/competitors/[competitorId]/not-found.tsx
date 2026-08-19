import Link from "next/link";

import { Button, Card, EmptyState } from "@/components/ui/primitives";

export default function CompetitorNotFound() {
  return (
    <Card>
      <EmptyState
        title="Competitor not found"
        description="It may have been deleted, or it belongs to a different workspace."
        action={
          <Link href="/">
            <Button variant="secondary">Back to your dashboard</Button>
          </Link>
        }
      />
    </Card>
  );
}
