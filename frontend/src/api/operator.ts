// Operator capability adapter — admin console, chat sessions, roadmap ledgers,
// audit log, alerts, seeding and source-extraction surfaces.
//
// Route ownership is the generated table in ../contracts/capabilities.ts;
// functions here call only routes that table assigns to 'operator'.

import type { components } from '../contracts/schema';
import type {
  CostReport,
  NeuronScoreResponse,
} from '../types';
import { json } from './http';

// ── Chat sessions (persistent) ──
export type SessionSummary = components['schemas']['SessionSummary'];

export interface SessionMessage {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  neurons_activated: number;
  neuron_scores: NeuronScoreResponse[] | null;
  created_at: string;
}

// Kept handwritten: the generated schema under-specifies this shape
// (dict-typed fields on the backend model). See record 05 measured addendum.
export interface SessionDetail {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages: SessionMessage[];
}

export function createSession(): Promise<{ id: number; created_at: string }> {
  return json('/chat/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
}

export function listSessions(limit = 20): Promise<SessionSummary[]> {
  return json(`/chat/sessions?limit=${limit}`);
}

export function getSession(id: number): Promise<SessionDetail> {
  return json(`/chat/sessions/${id}`);
}

export function appendMessage(sessionId: number, msg: {
  role: string; text: string; model?: string;
  input_tokens?: number; output_tokens?: number; cost?: number;
  neurons_activated?: number; neuron_scores?: NeuronScoreResponse[];
}): Promise<{ id: number; created_at: string }> {
  return json(`/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(msg),
  });
}

export function updateSessionTitle(id: number, title: string): Promise<{ ok: boolean }> {
  return json(`/chat/sessions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
}

export function deleteSession(id: number): Promise<{ ok: boolean }> {
  return json(`/chat/sessions/${id}`, { method: 'DELETE' });
}

// ── Project roadmap ledgers ──
export type RoadmapStatus =
  | 'done' | 'active' | 'in-progress' | 'planned' | 'proposed' | 'unblocked'
  | 'bug' | 'deprioritized' | 'polish' | 'conceptual' | 'cancelled';

export type RoadmapHorizon =
  | 'thesis' | 'horizon-3' | 'horizon-2' | 'horizon-1' | 'active';

export type RoadmapAssumptionStatus =
  | 'standing' | 'supported' | 'challenged' | 'invalidated';

export type RoadmapReviewCadence =
  | 'monthly' | 'quarterly' | 'semiannual' | 'annual' | 'event' | 'manual';

export interface RoadmapAssumption {
  id: string;
  statement: string;
  status: RoadmapAssumptionStatus;
  confidence: number;
  evidenceFor?: string[];
  evidenceAgainst?: string[];
  invalidationTrigger?: string;
  consequence?: string;
  lastValidatedAt?: string;
}

export interface RoadmapSection extends Record<string, unknown> {
  id: string;
  label: string;
  color: string;
}

export interface RoadmapNode extends Record<string, unknown> {
  id: string;
  section: string;
  label: string;
  status: RoadmapStatus;
  summary?: string;
  prompt?: string;
  verification?: string[];
  prereqs?: string[];
  completedAt?: string;
  horizon?: RoadmapHorizon;
  assumptions?: RoadmapAssumption[];
  reviewCadence?: RoadmapReviewCadence;
  nextReviewAt?: string | null;
  lastReviewedAt?: string;
  reviewHistory?: Array<Record<string, unknown>>;
  reconciliationHistory?: RoadmapReconciliation[];
  deferred?: boolean;
  deferredOrder?: number;
}

export interface RoadmapEdge extends Record<string, unknown> {
  from: string;
  to: string;
  type?: string;
}

export interface RoadmapMilestone extends Record<string, unknown> {
  id: string;
  label: string;
  summary?: string;
  prereqs?: string[];
  color?: string;
}

export interface RoadmapState extends Record<string, unknown> {
  version: number;
  updatedAt: string;
  sections: RoadmapSection[];
  nodes: RoadmapNode[];
  edges: RoadmapEdge[];
  milestones?: RoadmapMilestone[];
}

export interface RoadmapSummary {
  records: number;
  in_scope: number;
  out_of_scope: number;
  done: number;
  moving: number;
  completion: number;
  assumptions: number;
  challenged_assumptions: number;
  reviews_due: number;
  reviews_upcoming: number;
  horizons: Record<RoadmapHorizon, number>;
  unclassified_horizon: number;
}

export interface RoadmapLedgerSummary {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  project_path: string | null;
  revision: number;
  summary: RoadmapSummary;
  updated_at: string;
}

export interface RoadmapLedger extends RoadmapLedgerSummary {
  state: RoadmapState;
  created_at: string;
}

export interface RoadmapReconciliation {
  schema: 'corvus.roadmap-reconciliation/v1';
  acceptedAt: string;
  acceptedBy: string;
  verifier: string;
  ledgerRevision: number;
  disposition: 'complete' | 'partial' | 'failed' | 'blocked';
  verificationPassed: boolean;
  confidence: number;
  claims: string[];
  limitations: string[];
  disclosures: string[];
  evidence: string[];
  nextAction: string | null;
}

export interface RoadmapAdmissionEvent {
  ts: string;
  event: 'PlanningAdmission' | 'PlanningReturn';
  session_id: string;
  ledger_slug: string;
  ledger_revision: number;
  record_id: string | null;
  record_label?: string | null;
  mode?: 'bound' | 'off-ledger';
  admission_mode?: 'bound' | 'off-ledger';
  reason?: string | null;
  harness?: string | null;
  material_tool_count?: number;
  tools?: string[];
  status?: 'reconcile-required';
}

export function listRoadmapLedgers(): Promise<RoadmapLedgerSummary[]> {
  return json('/roadmap-ledgers');
}

export function getRoadmapLedger(slug: string): Promise<RoadmapLedger> {
  return json(`/roadmap-ledgers/${encodeURIComponent(slug)}`);
}

export function createRoadmapLedger(payload: {
  name: string;
  slug?: string;
  description?: string;
  project_path?: string;
}): Promise<RoadmapLedger> {
  return json('/roadmap-ledgers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function saveRoadmapLedger(
  slug: string, expectedRevision: number, state: RoadmapState,
): Promise<RoadmapLedger> {
  return json(`/roadmap-ledgers/${encodeURIComponent(slug)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: expectedRevision, state }),
  });
}

export function listRoadmapAdmissions(
  slug: string, limit = 50,
): Promise<RoadmapAdmissionEvent[]> {
  return json(`/roadmap-ledgers/${encodeURIComponent(slug)}/admissions?limit=${limit}`);
}

export function reconcileRoadmapNode(
  slug: string, nodeId: string, payload: {
    expected_revision: number;
    disposition: 'complete' | 'partial' | 'failed' | 'blocked';
    result_recap: string;
    verification_passed: boolean;
    confidence: number;
    claims: string[];
    limitations: string[];
    disclosures: string[];
    evidence: string[];
    verifier: string;
    accepted_by: string;
    next_action?: string;
  },
): Promise<RoadmapLedger> {
  return json(`/roadmap-ledgers/${encodeURIComponent(slug)}/nodes/${encodeURIComponent(nodeId)}/reconcile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function generateSessionTitle(id: number): Promise<{ title: string; cost_usd: number }> {
  return json(`/chat/sessions/${id}/generate-title`, { method: 'POST' });
}

export interface ConceptNeuron {
  id: number;
  label: string;
  summary: string | null;
  content: string | null;
  invocations: number;
  avg_utility: number;
  instantiation_edges: number;
  is_active: boolean;
}

export function fetchConceptNeurons(): Promise<ConceptNeuron[]> {
  return json<ConceptNeuron[]>('/admin/concept-neurons');
}

export function fetchCostReport(): Promise<CostReport> {
  return json<CostReport>('/admin/cost-report');
}

export type CheckpointResponse = components['schemas']['CheckpointResponse'];

export function createCheckpoint(): Promise<CheckpointResponse> {
  return json<CheckpointResponse>('/admin/checkpoint', { method: 'POST' });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function fetchPerformance(): Promise<any> {
  return json<unknown>('/admin/performance');
}

export interface StageStat {
  stage: string; label: string; order: number; n: number;
  mean: number; stddev: number; cov: number;
  p50: number; p90: number; p95: number; p99: number; min: number; max: number;
  estimate_ms: number | null; ratio_p50_vs_estimate: number | null; share_pct: number;
}

export interface StageTrendPoint { bucket: string; stage: string; n: number; p50: number; p95: number }

export interface StageTelemetryReport {
  error?: string;
  meta: {
    queries_with_telemetry: number; excluded_incident_queries: number; total_samples: number;
    pipeline_total_p50_ms: number; date_range: [string | null, string | null];
  };
  stages: StageStat[];
  trend: StageTrendPoint[];
}

export function fetchStageTelemetry(): Promise<StageTelemetryReport> {
  return json<StageTelemetryReport>('/admin/performance/stage-telemetry');
}

export interface SignalStats {
  mean: number;
  stddev: number;
  min: number;
  max: number;
  count: number;
}

export interface SignalHealth {
  baseline: SignalStats;
  recent: SignalStats;
  baseline_query_means: SignalStats;
  recent_query_means: SignalStats;
  z_score: number;
  drifted: boolean;
}

export interface DriftAlert {
  signal: string;
  direction: string;
  z_score: number;
  baseline_mean: number;
  recent_mean: number;
  message: string;
}

export interface ScoringHealthResponse {
  status: string;
  queries_analyzed: number;
  queries_available?: number;
  baseline_window: number;
  recent_window: number;
  can_detect_drift: boolean;
  drift_threshold: number;
  signals: Record<string, SignalHealth>;
  drift_alerts: DriftAlert[];
  per_query_timeline: Record<string, number | string | null>[];
  // mind-reference-class: headline signals cover the experiential
  // population only; Library (document-ingested) is reported separately.
  segmentation?: {
    reference_neurons: number;
    library: {
      queries_with_reference_hits: number;
      signals: Record<string, SignalHealth>;
      note: string;
    } | null;
  };
}

export function fetchScoringHealth(): Promise<ScoringHealthResponse> {
  return json<ScoringHealthResponse>('/admin/scoring-health');
}

// ── Health Check & Alerts ──
export interface SystemAlertOut {
  id: number;
  type: string;
  severity: string;
  signal: string | null;
  message: string;
  detail?: Record<string, unknown> | null;
  acknowledged: boolean;
  created_at: string | null;
}

export interface HealthCheckResponse {
  status: string;
  circuit_breaker_tripped: boolean;
  reasons: string[];
  avg_eval_overall: number | null;
  avg_user_rating: number | null;
  eval_count: number;
  rating_count: number;
  model_versions: string[];
  model_version_changed: boolean;
  drift_alerts_count: number;
  active_alerts: SystemAlertOut[];
  new_alerts: { type: string; signal?: string; message: string }[];
  thresholds: Record<string, number>;
}

export function fetchHealthCheck(): Promise<HealthCheckResponse> {
  return json<HealthCheckResponse>('/admin/health-check');
}

export function fetchAlerts(includeAcknowledged = false): Promise<SystemAlertOut[]> {
  const params = includeAcknowledged ? '?include_acknowledged=true' : '';
  return json<SystemAlertOut[]>(`/admin/alerts${params}`);
}

export function acknowledgeAlert(alertId: number): Promise<{ status: string }> {
  return json<{ status: string }>(`/admin/alerts/${alertId}/acknowledge`, { method: 'POST' });
}

export function acknowledgeAllAlerts(): Promise<{ status: string; count: number }> {
  return json<{ status: string; count: number }>('/admin/alerts/acknowledge-all', { method: 'POST' });
}

// ── Compliance Audit ──
export interface PiiScanResult {
  findings: { neuron_id: number; neuron_label: string; department: string | null; field: string; pii_type: string; match_count: number; excerpt: string }[];
  total_findings: number;
  neurons_with_pii: number;
  clean: boolean;
}

export interface DeptCoverage {
  department: string;
  neuron_count: number;
  pct_of_total: number;
  total_invocations: number;
  avg_utility: number;
}

export interface EvalDisaggregation {
  mode: string;
  count: number;
  avg_accuracy: number;
  avg_completeness: number;
  avg_clarity: number;
  avg_faithfulness: number;
  avg_overall: number;
}

export interface SignalBaseline {
  count: number;
  mean: number;
  stddev: number;
  min: number;
  max: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
}

export interface ConfidenceInterval {
  mean: number;
  ci_lower: number;
  ci_upper: number;
  n: number;
  stderr: number;
}

export interface CrossValidation {
  folds: number;
  n: number;
  fold_means: number[];
  fold_cv: number;
  stable: boolean;
  message: string;
}

export interface RemediationItem {
  type: string;
  severity: string;
  department: string;
  message: string;
  action: string;
}

export interface ComplianceAuditResponse {
  total_neurons: number;
  pii_scan: PiiScanResult;
  bias_assessment: {
    department_coverage: DeptCoverage[];
    department_count: number;
    coverage_cv: number;
    coverage_imbalanced: boolean;
    layer_distribution: Record<string, number>;
    eval_disaggregation: EvalDisaggregation[];
  };
  scoring_baselines: {
    queries_analyzed: number;
    signals: Record<string, SignalBaseline>;
    metric_rationale: Record<string, string>;
  };
  provenance_audit: {
    source_type_distribution: Record<string, number>;
    missing_citations: { neuron_id: number; label: string; department: string | null; source_type: string }[];
    missing_citations_count: number;
    missing_source_urls: { neuron_id: number; label: string; department: string | null; source_type: string }[];
    missing_source_urls_count: number;
    stale_neurons: { neuron_id: number; label: string; department: string | null; source_type: string; last_verified: string; days_since_verified: number }[];
    stale_neurons_count: number;
  };
  validity_reliability: {
    confidence_intervals: Record<string, Record<string, ConfidenceInterval>>;
    cross_validation: Record<string, CrossValidation>;
    signal_robustness: Record<string, { cv: number; robust: boolean; n: number }>;
    total_evals: number;
  };
  fairness_analysis: {
    department_eval_quality: { department: string; answer_mode: string; eval_count: number; avg_overall: number; avg_faithfulness: number }[];
    invocation_disparity_ratio: number | null;
    utility_range: number;
    coverage_cv: number;
    remediation_plan: RemediationItem[];
    remediation_count: number;
    fairness_pass: boolean;
  };
}

// ── Governance Dashboard ──
export interface GovernanceDashboardResponse {
  totals: {
    neurons: number;
    queries: number;
    evaluations: number;
    refinements: number;
    departments: number;
    rated_queries: number;
  };
  kpis: {
    avg_eval_overall: number | null;
    avg_faithfulness: number | null;
    avg_user_rating: number | null;
    avg_cost_per_query: number;
    total_cost_usd: number;
    cost_per_1m_tokens: number | null;
    run_cost_per_1m: number | null;
    zero_hit_pct: number;
    parity_index: number | null;
    value_score: number | null;
    avg_opus_eval: number | null;
    avg_neuron_eval: number | null;
    opus_cost_per_1m: number | null;
    coverage_cv: number;
  };
  change_activity: {
    refinements_30d: number;
    autopilot_runs_30d: number;
    recent_changes: {
      id: number;
      action: string;
      field: string | null;
      reason: string;
      neuron_id: number;
      created_at: string | null;
    }[];
  };
  active_alerts: number;
}

export function fetchGovernanceDashboard(): Promise<GovernanceDashboardResponse> {
  return json<GovernanceDashboardResponse>('/admin/governance-dashboard');
}

// ── Emergent Queue ──
export interface EmergentQueueEntry {
  id: number;
  citation_pattern: string;
  domain: string;
  family: string | null;
  detection_count: number;
  first_detected_at: string | null;
  last_detected_at: string | null;
  detected_in_neuron_ids: number[];
  detected_in_query_ids: number[];
  status: string;
  resolved_neuron_id: number | null;
  resolved_at: string | null;
  notes: string | null;
}

export interface EmergentQueueResponse {
  total: number;
  entries: EmergentQueueEntry[];
}

export interface ScanReferencesResponse {
  neurons_scanned: number;
  neurons_with_references: number;
  total_references_found: number;
  resolved: number;
  unresolved: number;
  new_queue_entries: number;
  existing_queue_entries_incremented: number;
  top_unresolved_families: { family: string; count: number }[];
}

export function fetchEmergentQueue(status?: string): Promise<EmergentQueueResponse> {
  const params = status ? `?status=${encodeURIComponent(status)}` : '';
  return json<EmergentQueueResponse>(`/admin/emergent-queue${params}`);
}

export function dismissEmergentEntry(entryId: number, notes?: string): Promise<{ status: string; id: number }> {
  return json<{ status: string; id: number }>(`/admin/emergent-queue/${entryId}/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes: notes || '' }),
  });
}

export function scanReferences(): Promise<ScanReferencesResponse> {
  return json<ScanReferencesResponse>('/admin/scan-references', { method: 'POST' });
}

export interface IngestProposal {
  layer: number;
  node_type: string;
  label: string;
  content: string;
  summary: string;
  reason: string;
  department: string | null;
  role_key: string | null;
  parent_id: number | null;
  source_type: string;
  citation: string;
  source_url: string | null;
  effective_date: string | null;
}

export interface IngestSourceResponse {
  proposals: IngestProposal[];
  count: number;
  citation: string;
  source_type: string;
  department: string | null;
  role_key: string | null;
  parent_id: number | null;
  parent_label: string | null;
  queue_entry_id: number | null;
  llm_cost: { input_tokens: number; output_tokens: number; cost_usd: number };
}

export interface IngestApplyResponse {
  status: string;
  neurons_created: number;
  neuron_ids: number[];
  edges_created: number;
  queue_entry_resolved: boolean;
}

export function ingestSource(body: {
  source_text: string;
  citation: string;
  source_type: string;
  source_url?: string;
  effective_date?: string;
  department?: string;
  role_key?: string;
  queue_entry_id?: number;
}): Promise<IngestSourceResponse> {
  return json<IngestSourceResponse>('/admin/ingest-source', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export interface ExtractSourceResponse {
  text: string;
  char_count: number;
  total_pages: number;
  source_info: string;
}

export async function extractSourceFromFile(file: File, pageStart?: number, pageEnd?: number): Promise<ExtractSourceResponse> {
  const form = new FormData();
  form.append('file', file);
  if (pageStart) form.append('page_start', String(pageStart));
  if (pageEnd) form.append('page_end', String(pageEnd));
  const res = await fetch('/admin/extract-source', { method: 'POST', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function extractSourceFromUrl(url: string, pageStart?: number, pageEnd?: number): Promise<ExtractSourceResponse> {
  const params = new URLSearchParams({ url });
  if (pageStart) params.set('page_start', String(pageStart));
  if (pageEnd) params.set('page_end', String(pageEnd));
  const res = await fetch(`/admin/extract-source?${params}`, { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface BatchIngestStartResponse {
  job_id: string;
  total_chunks: number;
  total_chars: number;
  status: string;
}

export interface BatchIngestStatusResponse {
  job_id: string;
  status: string;
  step: string;
  total_chunks: number;
  current_chunk: number;
  proposals_count: number;
  proposals: IngestProposal[];
  errors: string[];
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  citation: string;
  department: string | null;
  role_key: string | null;
  parent_id: number | null;
  parent_label: string | null;
  queue_entry_id: number | null;
}

export function startBatchIngest(body: {
  source_text: string;
  citation: string;
  source_type: string;
  source_url?: string;
  effective_date?: string;
  department?: string;
  role_key?: string;
  queue_entry_id?: number;
  model?: string;
  chunk_size?: number;
}): Promise<BatchIngestStartResponse> {
  return json<BatchIngestStartResponse>('/admin/ingest-source/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function pollBatchIngest(jobId: string): Promise<BatchIngestStatusResponse> {
  return json<BatchIngestStatusResponse>(`/admin/ingest-source/batch/${jobId}`);
}

export function cancelBatchIngest(jobId: string): Promise<{ status: string }> {
  return json<{ status: string }>(`/admin/ingest-source/batch/${jobId}/cancel`, { method: 'POST' });
}

export function resumeBatchIngest(jobId: string): Promise<{ job_id: string; status: string; resuming_from_chunk: number; total_chunks: number; existing_proposals: number }> {
  return json(`/admin/ingest-source/batch/${jobId}/resume`, { method: 'POST' });
}

export interface BatchJobSummary {
  job_id: string;
  status: string;
  step: string;
  total_chunks: number;
  current_chunk: number;
  proposals_count: number;
  errors: string[];
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  citation: string;
  queue_entry_id: number | null;
}

export function listBatchJobs(): Promise<{ jobs: BatchJobSummary[]; active_count: number }> {
  return json<{ jobs: BatchJobSummary[]; active_count: number }>('/admin/ingest-source/batch');
}

export function applyIngestSource(body: {
  proposals: IngestProposal[];
  queue_entry_id?: number;
}): Promise<IngestApplyResponse> {
  return json<IngestApplyResponse>('/admin/ingest-source/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── Audit Log ──
export interface AuditLogEntry {
  id: number;
  timestamp: string;
  action: string;
  endpoint: string;
  status_code: number;
  user_agent: string | null;
  client_ip: string | null;
  request_body_summary: string | null;
  response_time_ms: number | null;
  error_detail: string | null;
}

export interface AuditLogSummary {
  total_records: number;
  by_action: Record<string, number>;
  error_count: number;
  latest_entry: string | null;
  top_endpoints: { endpoint: string; count: number }[];
}

export function fetchAuditLog(opts?: { action?: string; endpoint_filter?: string; since?: string; status_code_min?: number; limit?: number; offset?: number }): Promise<AuditLogEntry[]> {
  const params = new URLSearchParams();
  if (opts?.action) params.set('action', opts.action);
  if (opts?.endpoint_filter) params.set('endpoint_filter', opts.endpoint_filter);
  if (opts?.since) params.set('since', opts.since);
  if (opts?.status_code_min) params.set('status_code_min', String(opts.status_code_min));
  if (opts?.limit) params.set('limit', String(opts.limit));
  if (opts?.offset) params.set('offset', String(opts.offset));
  const qs = params.toString();
  return json<AuditLogEntry[]>(`/admin/audit-log${qs ? '?' + qs : ''}`);
}

export function fetchAuditLogSummary(since?: string): Promise<AuditLogSummary> {
  const qs = since ? `?since=${encodeURIComponent(since)}` : '';
  return json<AuditLogSummary>(`/admin/audit-log/summary${qs}`);
}

// Named for compliance, owned by operator. /admin/compliance-audit is declared
// in routers/admin.py, NOT in the compliance context record 04a retired on
// 2026-08-01, so it survived that retirement along with the provenance data it
// returns — missing citations, source-type distribution, stale neurons. The
// name is the trap: match these on route ownership, not on the word.
export function fetchComplianceAudit(): Promise<ComplianceAuditResponse> {
  return json<ComplianceAuditResponse>('/admin/compliance-audit');
}

// ── System Use Banner (AC-8) ──
export interface SystemBannerResponse {
  enabled: boolean;
  banner_text: string;
  session_timeout_minutes: number;
}

export function fetchSystemBanner(): Promise<SystemBannerResponse> {
  return json<SystemBannerResponse>('/admin/system-banner');
}
