import { Bell, BellOff } from "lucide-react";
import type { Metadata } from "next";

import { SeverityBadge } from "@/components/intelligence";
import {
  CreateAlertRule,
  DeleteAlertRule,
  MarkNotificationsRead,
  ToggleAlertRule,
} from "@/components/forms";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  PageHeader,
} from "@/components/ui/primitives";
import { api, serverFetch, withQuery } from "@/lib/api";
import type { AlertRule, Competitor, Notification, Paginated } from "@/lib/types";
import { formatRelative, humanize } from "@/lib/utils";

export const metadata: Metadata = { title: "Alerts" };

export default async function AlertsPage({
  params,
}: {
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;

  const [rules, notifications, competitors] = await Promise.all([
    serverFetch<AlertRule[]>(api.org(orgId).alerts),
    serverFetch<Paginated<Notification>>(
      withQuery(api.org(orgId).notifications, { limit: 50 }),
    ),
    serverFetch<Paginated<Competitor>>(
      withQuery(api.org(orgId).competitors, { status: "active", limit: 100 }),
    ),
  ]);

  const nameById = new Map(competitors.items.map((c) => [c.id, c.name]));
  const unread = notifications.items.filter((item) => !item.read_at).length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Alerts"
        description="Decide which competitor changes are worth interrupting you about."
        actions={<CreateAlertRule orgId={orgId} competitors={competitors.items} />}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Rules"
            description="A change must match a rule and meet its severity threshold to notify you."
          />
          <CardBody className="p-0">
            {rules.length === 0 ? (
              <EmptyState
                icon={<BellOff className="size-5" aria-hidden />}
                title="No alert rules"
                description="Without a rule, changes are still detected and listed under Monitoring — you just will not be notified about them."
              />
            ) : (
              <ul className="divide-y divide-border">
                {rules.map((rule) => (
                  <li key={rule.id} className="px-5 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-medium text-ink">{rule.name}</p>
                          {!rule.is_active ? <Badge>Paused</Badge> : null}
                        </div>
                        <p className="mt-1 text-xs text-ink-subtle">
                          {rule.competitor_id
                            ? (nameById.get(rule.competitor_id) ?? "One competitor")
                            : "All competitors"}{" "}
                          · {rule.min_severity} severity and above ·{" "}
                          {rule.change_types.length === 0
                            ? "any change type"
                            : `${rule.change_types.length} change type${rule.change_types.length === 1 ? "" : "s"}`}
                        </p>
                        {rule.change_types.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {rule.change_types.map((type) => (
                              <Badge key={type} variant="outline">
                                {humanize(type)}
                              </Badge>
                            ))}
                          </div>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center">
                        <ToggleAlertRule orgId={orgId} ruleId={rule.id} isActive={rule.is_active} />
                        <DeleteAlertRule orgId={orgId} ruleId={rule.id} />
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Notifications"
            description={unread > 0 ? `${unread} unread` : "You are up to date."}
            action={unread > 0 ? <MarkNotificationsRead orgId={orgId} /> : null}
          />
          <CardBody className="p-0">
            {notifications.items.length === 0 ? (
              <EmptyState
                icon={<Bell className="size-5" aria-hidden />}
                title="No notifications"
                description="Notifications appear here when a detected change matches one of your rules."
              />
            ) : (
              <ul className="max-h-[32rem] divide-y divide-border overflow-y-auto">
                {notifications.items.map((notification) => (
                  <li
                    key={notification.id}
                    className={notification.read_at ? "px-5 py-3" : "bg-accent-soft/40 px-5 py-3"}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm leading-snug text-ink">{notification.title}</p>
                        {notification.body ? (
                          <p className="mt-1 text-sm text-ink-muted">{notification.body}</p>
                        ) : null}
                        <p className="mt-1 text-xs text-ink-subtle">
                          <time dateTime={notification.created_at}>
                            {formatRelative(notification.created_at)}
                          </time>
                        </p>
                      </div>
                      {typeof notification.payload.severity === "string" ? (
                        <SeverityBadge severity={notification.payload.severity} />
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
