/**
 * API contracts.
 *
 * Hand-written rather than generated, and deliberately narrower than the OpenAPI schema:
 * these are the shapes the UI actually consumes. Where the backend distinguishes
 * observed data from AI inference, that distinction is preserved in the type, so a
 * component cannot render one as the other by accident.
 */

export type Role = "owner" | "admin" | "member" | "viewer";
export type CompetitorStatus = "active" | "archived";
export type Importance = "low" | "medium" | "high" | "critical";
export type ThreatLevel = "critical" | "high" | "moderate" | "low" | "unknown";
export type Severity = "low" | "medium" | "high";
export type DataSource = "observed" | "ai_inference";
export type JobStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type AnalysisDepth = "quick" | "standard" | "deep";

export type ScoreDimensionName =
  | "product"
  | "pricing"
  | "features"
  | "positioning"
  | "seo"
  | "marketing"
  | "sentiment"
  | "brand";

export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
}

export interface PageMeta {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface Paginated<T> {
  items: T[];
  meta: PageMeta;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  email_verified_at: string | null;
  last_login_at: string | null;
  created_at: string;
}

export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  plan: string;
  role: Role;
}

export interface Session {
  user: User;
  organizations: OrganizationSummary[];
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  own_company_name: string | null;
  own_company_url: string | null;
  own_company_description: string | null;
  created_at: string;
}

export interface Usage {
  period: string;
  analyses_used: number;
  analyses_limit: number;
  pages_crawled: number;
  pages_limit: number;
  competitors_used: number;
  competitors_limit: number;
  ai_tokens_in: number;
  ai_tokens_out: number;
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  joined_at: string;
}

export interface Competitor {
  id: string;
  name: string;
  website_url: string;
  domain: string;
  favicon_url: string | null;
  category: string | null;
  tags: string[];
  notes: string | null;
  status: CompetitorStatus;
  importance: Importance;
  monitoring_enabled: boolean;
  monitoring_interval_hours: number;
  next_monitor_at: string | null;
  last_analyzed_at: string | null;
  latest_overall_score: number | null;
  created_at: string;
  updated_at: string;
}

export interface CompetitorPage {
  id: string;
  url: string;
  page_type: string;
  title: string | null;
  last_status_code: number | null;
  last_fetched_at: string | null;
}

export interface Insight {
  title: string;
  detail: string;
  evidence?: { quote?: string | null; source_url?: string | null } | null;
}

export interface Recommendation {
  title: string;
  rationale: string;
  priority: "low" | "medium" | "high";
  effort: "low" | "medium" | "high";
}

export interface Analysis {
  id: string;
  competitor_id: string;
  created_at: string;
  provider: string;
  model: string;
  /** True when the development provider produced this. The UI must say so. */
  is_mock: boolean;
  depth: AnalysisDepth;
  summary: string | null;
  positioning: string | null;
  target_audience: string[];
  value_propositions: string[];
  strengths: Insight[];
  weaknesses: Insight[];
  marketing_channels: string[];
  recommendations: Recommendation[];
  key_features: string[];
  confidence: number | null;
  data_completeness: number | null;
  pages_analyzed: number;
  /** Corrections the pipeline made, e.g. a price dropped for not appearing on a page. */
  data_notes: string[];
  /** Pages whose text tried to issue instructions to the model. */
  injection_flags: { url: string; patterns: string[] }[];
  tokens_in: number;
  tokens_out: number;
}

export interface Product {
  id: string;
  name: string;
  description: string | null;
  category: string | null;
  features: string[];
  source_url: string | null;
  source: DataSource;
  is_current: boolean;
  first_seen_at: string;
  last_seen_at: string;
}

export interface PricingPlan {
  id: string;
  name: string;
  amount: string | null;
  currency: string | null;
  billing_period: string;
  is_custom_pricing: boolean;
  is_free: boolean;
  features: string[];
  highlights: string | null;
  source_url: string | null;
  source: DataSource;
  is_current: boolean;
  first_seen_at: string;
  last_seen_at: string;
}

export interface ScoreDimension {
  score: number | null;
  weight: number;
  rationale: string;
  inputs: Record<string, unknown>;
}

export interface Score {
  id: string;
  competitor_id: string;
  overall: number | null;
  dimensions: Record<string, ScoreDimension>;
  threat_level: ThreatLevel;
  confidence: number | null;
  data_completeness: number | null;
  methodology_version: string;
  created_at: string;
}

export interface Job {
  id: string;
  competitor_id: string | null;
  job_type: string;
  status: JobStatus;
  depth: AnalysisDepth;
  progress: number;
  stage: string | null;
  attempts: number;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface CompetitorDetail {
  competitor: Competitor;
  analysis: Analysis | null;
  score: Score | null;
  products: Product[];
  pricing: PricingPlan[];
  pages: CompetitorPage[];
  running_job: Job | null;
}

export interface Change {
  id: string;
  competitor_id: string;
  change_type: string;
  severity: Severity;
  title: string;
  description: string | null;
  entity_key: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  magnitude: number | null;
  source_url: string | null;
  detected_at: string;
  acknowledged_at: string | null;
}

export interface AlertRule {
  id: string;
  name: string;
  competitor_id: string | null;
  is_active: boolean;
  change_types: string[];
  min_severity: Severity;
  channels: string[];
  webhook_url: string | null;
  created_at: string;
}

export interface Notification {
  id: string;
  change_id: string | null;
  channel: string;
  status: string;
  title: string;
  body: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
}

export interface ComparisonMatrix {
  competitors: {
    id: string;
    name: string;
    domain: string;
    favicon_url: string | null;
    threat_level: ThreatLevel;
    confidence: number | null;
    analyzed: boolean;
  }[];
  /** dimension -> competitor name -> score, or null for "insufficient data". */
  dimensions: Record<string, Record<string, number | null>>;
  overall: Record<string, number | null>;
  methodology_version: string | null;
}

export interface ComparisonInsights {
  summary: string;
  strongest_competitor: string | null;
  strongest_reason: string | null;
  biggest_threat: string | null;
  biggest_threat_reason: string | null;
  biggest_opportunity: string | null;
  weakest_area_across_market: string | null;
  differentiation_opportunities: string[];
}

export interface Comparison {
  id: string;
  name: string | null;
  competitor_ids: string[];
  matrix: ComparisonMatrix;
  insights: ComparisonInsights | null;
  provider: string | null;
  is_mock: boolean;
  created_at: string;
}

export type ReportSection =
  | { kind: "text"; title: string; body: string; provenance?: DataSource; is_mock?: boolean }
  | {
      kind: "metrics";
      title: string;
      metrics: { label: string; value: string | number | null; suffix?: string }[];
    }
  | {
      kind: "table";
      title: string;
      columns: string[];
      rows: (string | number | null)[][];
      provenance?: DataSource;
    }
  | {
      kind: "timeline";
      title: string;
      events: {
        at: string;
        title: string;
        detail?: string | null;
        severity?: Severity;
        type?: string;
        competitor?: string;
      }[];
      provenance?: DataSource;
    }
  | {
      kind: "list";
      title: string;
      items: { title?: string; detail?: string }[];
      provenance?: DataSource;
    }
  | {
      kind: "score_matrix";
      title: string;
      dimensions: Record<string, ScoreDimension>;
      overall: number | null;
      methodology_version: string;
      confidence: number | null;
      provenance?: DataSource;
    };

export interface ReportContent {
  subject: Record<string, unknown>;
  period: { start: string; end: string };
  generated_at: string;
  is_mock: boolean;
  sections: ReportSection[];
}

export interface Report {
  id: string;
  report_type: string;
  status: JobStatus;
  title: string;
  params: Record<string, unknown>;
  content: ReportContent | null;
  period_start: string | null;
  period_end: string | null;
  generated_at: string | null;
  created_at: string;
}

export interface DashboardOverview {
  competitors_tracked: number;
  competitors_analyzed: number;
  competitors_archived: number;
  average_score: number | null;
  threat_distribution: Record<string, number>;
  overall_threat_level: ThreatLevel;
  changes_last_7_days: number;
  high_severity_changes: number;
  running_jobs: number;
  failed_jobs: number;
  /** True when any recent analysis came from the development provider. */
  uses_mock_ai: boolean;
  recent_changes: {
    id: string;
    competitor_id: string;
    competitor_name: string;
    favicon_url: string | null;
    change_type: string;
    severity: Severity;
    title: string;
    detected_at: string;
    acknowledged: boolean;
  }[];
  recent_analyses: {
    id: string;
    competitor_id: string;
    competitor_name: string;
    created_at: string;
    confidence: number | null;
    is_mock: boolean;
    pages_analyzed: number;
  }[];
  landscape: {
    id: string;
    name: string;
    domain: string;
    favicon_url: string | null;
    overall: number | null;
    threat_level: ThreatLevel;
    importance: Importance;
    last_analyzed_at: string | null;
    dimensions: Record<string, ScoreDimension>;
  }[];
  opportunities: {
    dimension: string;
    market_average: number;
    competitors_measured: number;
    title: string;
    detail: string;
  }[];
}

export interface SearchResult {
  query: string;
  hits: {
    competitor_id: string;
    competitor_name: string;
    content: string;
    source_url: string | null;
    similarity: number;
  }[];
  provider: string;
  /** True when the offline hashing embedder produced the index: lexical, not semantic. */
  is_lexical: boolean;
}
