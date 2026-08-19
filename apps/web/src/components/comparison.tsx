"use client";

/**
 * Comparison view: pick competitors, build a matrix, read the chart.
 *
 * A radar chart is the right shape here — the question is "who is stronger where", and a
 * radar makes each competitor's profile legible as a single outline. It is paired with
 * the table below it, because a radar is bad at exact values and unreadable to a screen
 * reader; the table is the accessible source of truth.
 */

import { Loader2 } from "lucide-react";
import { useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import {
  Badge,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { ApiClientError, api, clientFetch } from "@/lib/api";
import type { Comparison, Competitor } from "@/lib/types";
import { cn, formatScore, humanize } from "@/lib/utils";

const MIN = 2;
const MAX = 6;

// Distinguishable in both themes and, importantly, distinguishable from each other for
// the most common forms of colour vision deficiency.
const SERIES_COLORS = [
  "oklch(52% 0.16 258)",
  "oklch(58% 0.15 30)",
  "oklch(55% 0.13 155)",
  "oklch(60% 0.14 300)",
  "oklch(62% 0.13 85)",
  "oklch(50% 0.11 200)",
];

export function ComparisonBuilder({
  orgId,
  competitors,
}: {
  orgId: string;
  competitors: Competitor[];
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : current.length >= MAX
          ? current
          : [...current, id],
    );
  }

  async function compare() {
    setLoading(true);
    setError(null);
    try {
      const result = await clientFetch<Comparison>(api.org(orgId).comparisons, {
        method: "POST",
        body: { competitor_ids: selected, with_insights: true },
      });
      setComparison(result);
    } catch (caught) {
      setError(
        caught instanceof ApiClientError
          ? caught.displayMessage
          : "Could not build the comparison.",
      );
    } finally {
      setLoading(false);
    }
  }

  const analysable = competitors.filter((competitor) => competitor.last_analyzed_at);

  if (analysable.length < MIN) {
    return (
      <Card>
        <EmptyState
          title="Not enough analysed competitors"
          description={`Comparison needs at least ${MIN} competitors with a completed analysis. Analyse a few and come back.`}
        />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Select competitors"
          description={`Choose between ${MIN} and ${MAX}. Only analysed competitors can be compared.`}
          action={
            <Button onClick={compare} disabled={selected.length < MIN || loading}>
              {loading ? <Loader2 className="animate-spin" aria-hidden /> : null}
              {loading ? "Comparing…" : "Compare"}
            </Button>
          }
        />
        <CardBody>
          {error ? (
            <div className="mb-4">
              <Callout tone="danger">{error}</Callout>
            </div>
          ) : null}

          <fieldset>
            <legend className="sr-only">Competitors to compare</legend>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {analysable.map((competitor) => {
                const checked = selected.includes(competitor.id);
                const atLimit = !checked && selected.length >= MAX;

                return (
                  <label
                    key={competitor.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-3 rounded-[--radius-control] border p-3 transition-colors",
                      checked
                        ? "border-accent bg-accent-soft"
                        : "border-border hover:bg-surface-raised",
                      atLimit && "cursor-not-allowed opacity-50",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="size-4 rounded border-border"
                      checked={checked}
                      disabled={atLimit}
                      onChange={() => toggle(competitor.id)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-ink">
                        {competitor.name}
                      </span>
                      <span className="block truncate text-xs text-ink-subtle">
                        {competitor.domain}
                      </span>
                    </span>
                    <span className="tabular text-sm font-semibold text-ink">
                      {formatScore(competitor.latest_overall_score)}
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        </CardBody>
      </Card>

      {comparison ? <ComparisonResult comparison={comparison} /> : null}
    </div>
  );
}

function ComparisonResult({ comparison }: { comparison: Comparison }) {
  const names = comparison.matrix.competitors.map((competitor) => competitor.name);
  const dimensions = Object.keys(comparison.matrix.dimensions);

  // Recharts needs one row per axis. Dimensions where nobody has data are dropped:
  // plotting a ring of zeros would read as "everyone is terrible at this".
  const chartData = dimensions
    .filter((dimension) =>
      names.some((name) => comparison.matrix.dimensions[dimension]?.[name] != null),
    )
    .map((dimension) => {
      const row: Record<string, string | number> = { dimension: humanize(dimension) };
      for (const name of names) {
        row[name] = comparison.matrix.dimensions[dimension]?.[name] ?? 0;
      }
      return row;
    });

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Score profile"
            description="Computed from observed data. Dimensions nobody could be measured on are omitted."
          />
          <CardBody>
            {chartData.length === 0 ? (
              <p className="py-8 text-center text-sm text-ink-subtle">
                No dimension has enough data to plot.
              </p>
            ) : (
              <div className="h-80 w-full" role="img" aria-label="Radar chart of competitor scores by dimension. The same data appears in the table below.">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={chartData} outerRadius="72%">
                    <PolarGrid stroke="var(--color-border)" />
                    <PolarAngleAxis
                      dataKey="dimension"
                      tick={{ fill: "var(--color-ink-muted)", fontSize: 11 }}
                    />
                    <PolarRadiusAxis
                      domain={[0, 100]}
                      tick={{ fill: "var(--color-ink-subtle)", fontSize: 10 }}
                      tickCount={5}
                    />
                    {names.map((name, index) => (
                      <Radar
                        key={name}
                        name={name}
                        dataKey={name}
                        stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
                        fill={SERIES_COLORS[index % SERIES_COLORS.length]}
                        fillOpacity={0.12}
                        strokeWidth={2}
                      />
                    ))}
                    <Tooltip
                      contentStyle={{
                        background: "var(--color-surface)",
                        border: "1px solid var(--color-border)",
                        borderRadius: "0.5rem",
                        fontSize: 12,
                      }}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            )}

            <ul className="mt-3 flex flex-wrap gap-3">
              {names.map((name, index) => (
                <li key={name} className="flex items-center gap-1.5 text-xs text-ink-muted">
                  <span
                    className="size-2.5 rounded-sm"
                    style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }}
                    aria-hidden
                  />
                  {name}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="AI comparison"
            description="Interpretation of the matrix. The numbers themselves are not AI-generated."
            action={comparison.is_mock ? <Badge>Development provider</Badge> : null}
          />
          <CardBody className="space-y-4 text-sm">
            {comparison.insights ? (
              <>
                <p className="leading-relaxed text-ink-muted">{comparison.insights.summary}</p>

                {comparison.insights.strongest_competitor ? (
                  <Insight
                    label="Strongest"
                    value={comparison.insights.strongest_competitor}
                    detail={comparison.insights.strongest_reason}
                  />
                ) : null}
                {comparison.insights.biggest_threat ? (
                  <Insight
                    label="Biggest threat"
                    value={comparison.insights.biggest_threat}
                    detail={comparison.insights.biggest_threat_reason}
                  />
                ) : null}
                {comparison.insights.biggest_opportunity ? (
                  <Insight label="Opportunity" value={comparison.insights.biggest_opportunity} />
                ) : null}

                {comparison.insights.differentiation_opportunities.length > 0 ? (
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                      Where you could differentiate
                    </p>
                    <ul className="mt-1.5 space-y-1 text-ink-muted">
                      {comparison.insights.differentiation_opportunities.map((item) => (
                        <li key={item} className="flex gap-2">
                          <span className="mt-1.5 size-1 shrink-0 rounded-full bg-ink-subtle" />
                          {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </>
            ) : (
              <Callout tone="neutral">
                The narrative could not be generated. The score matrix below is unaffected —
                it is computed deterministically and does not depend on the AI provider.
              </Callout>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Score matrix"
          description="The authoritative view. Blank cells mean the dimension could not be measured."
        />
        <CardBody className="p-0">
          <Table caption="Competitor scores by dimension">
            <thead>
              <tr>
                <Th>Dimension</Th>
                {names.map((name) => (
                  <Th key={name} className="text-right">
                    {name}
                  </Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dimensions.map((dimension) => (
                <tr key={dimension}>
                  <Td className="font-medium">{humanize(dimension)}</Td>
                  {names.map((name) => {
                    const value = comparison.matrix.dimensions[dimension]?.[name];
                    return (
                      <Td key={name} className="tabular text-right">
                        {value == null ? (
                          <span className="text-xs text-ink-subtle">Insufficient data</span>
                        ) : (
                          formatScore(value)
                        )}
                      </Td>
                    );
                  })}
                </tr>
              ))}
              <tr className="bg-surface-raised">
                <Td className="font-semibold">Overall</Td>
                {names.map((name) => (
                  <Td key={name} className="tabular text-right font-semibold">
                    {comparison.matrix.overall[name] == null ? (
                      <span className="text-xs font-normal text-ink-subtle">—</span>
                    ) : (
                      formatScore(comparison.matrix.overall[name])
                    )}
                  </Td>
                ))}
              </tr>
            </tbody>
          </Table>
        </CardBody>
      </Card>
    </div>
  );
}

function Insight({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string | null;
}) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">{label}</p>
      <p className="mt-0.5 font-medium text-ink">{value}</p>
      {detail ? <p className="mt-0.5 text-ink-muted">{detail}</p> : null}
    </div>
  );
}
