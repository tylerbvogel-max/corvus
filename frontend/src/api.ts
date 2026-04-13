import type {
  TreeNode,
  NeuronDetail,
  NeuronScores,
  NeuronStats,
  CostReport,
  QueryResponse,
  QuerySummary,
  QueryDetail,
  EvalResponse,
  RatingResponse,
  RefineResponse,
  ApplyRefineResponse,
  NeuronRefinementEntry,
  AutopilotConfig,
  AutopilotRun,
  AutopilotTickResponse,
  AutopilotChange,
  DeptChordEntry,
  EgoGraphResponse,
  SpreadTrailResponse,
  ObservationSummary,
  ObservationDetail,
  ObservationEvalResponse,
  ObservationApplyResponse,
  NeuronScoreResponse,
  IntegrityDashboard,
  IntegrityScanSummary,
  IntegrityScanDetail,
  IntegrityScanResponse,
  IntegrityFinding,
  IntegrityFindingDetail,
  IntegrityApplyResult,
  IntegrityBulkResolveResult,
  IntegrityProposeResult,
} from './types';

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const { getAuthHeaders } = await import('./auth');
  const authHeaders = getAuthHeaders();
  const mergedInit: RequestInit = {
    ...init,
    headers: { ...authHeaders, ...(init?.headers || {}) },
  };
  const res = await fetch(url, mergedInit);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (detail?.message) {
        const flags = detail.flags as Array<{ description: string; severity: string; pattern?: string }>;
        if (flags?.length) {
          const reasons = flags.map((f: { description: string; pattern?: string }) =>
            f.description + (f.pattern ? ` — "${f.pattern}"` : '')).join('; ');
          message = `${detail.message}: ${reasons}`;
        } else {
          message = detail.message;
        }
      } else if (typeof detail === 'string') {
        message = detail;
      }
    } catch {
      // Response body wasn't JSON — use status text
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

// ── Available LLM models ──

export interface ModelOption {
  display_name: string;
  provider: string;
  api_id: string;
  tier: string;
  input_price: number;
  output_price: number;
}

export function fetchAvailableModels(): Promise<ModelOption[]> {
  return json<ModelOption[]>('/models');
}

// ── Simple chat (no neuron pipeline) ──

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

export interface ChatResponse {
  response: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

// ── Chat sessions (persistent) ──

export interface SessionSummary {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

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

export function generateSessionTitle(id: number): Promise<{ title: string; cost_usd: number }> {
  return json(`/chat/sessions/${id}/generate-title`, { method: 'POST' });
}

export function sendChat(message: string, model: string = 'haiku', history: ChatMessage[] = []): Promise<ChatResponse> {
  return json<ChatResponse>('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, model, history }),
  });
}

export async function sendNeuronChat(message: string, model: string = 'haiku'): Promise<ChatResponse & { neurons_activated: number; neuron_scores: NeuronScoreResponse[] }> {
  const slot: SlotSpec = { mode: `${model}_neuron`, token_budget: 2048, top_k: 12 };
  const res = await submitQuery(message, [slot], 'conversational');
  const slotResult = res.slots[0];
  return {
    response: slotResult?.response ?? '',
    model,
    input_tokens: (res.classify_input_tokens || 0) + (slotResult?.input_tokens || 0),
    output_tokens: (res.classify_output_tokens || 0) + (slotResult?.output_tokens || 0),
    cost_usd: res.total_cost || 0,
    neurons_activated: res.neurons_activated,
    neuron_scores: res.neuron_scores,
  };
}

export function fetchTree(department?: string, maxDepth?: number): Promise<TreeNode[]> {
  const parts: string[] = [];
  if (department) parts.push(`department=${encodeURIComponent(department)}`);
  if (maxDepth != null) parts.push(`max_depth=${maxDepth}`);
  const params = parts.length ? `?${parts.join('&')}` : '';
  return json<TreeNode[]>(`/neurons/tree${params}`);
}

export interface ChildNode {
  id: number;
  layer: number;
  node_type: string;
  label: string;
  department: string | null;
  role_key: string | null;
  invocations: number;
  avg_utility: number;
  parent_id: number | null;
  child_count: number;
}

export function fetchChildren(parentId: number | null, limit = 200): Promise<ChildNode[]> {
  const params = parentId != null ? `?parent_id=${parentId}&limit=${limit}` : `?limit=${limit}`;
  return json<ChildNode[]>(`/neurons/children${params}`);
}

export function fetchNeuron(id: number): Promise<NeuronDetail> {
  return json<NeuronDetail>(`/neurons/${id}`);
}

export function fetchScores(id: number): Promise<NeuronScores> {
  return json<NeuronScores>(`/neurons/${id}/scores`);
}

export function fetchStats(): Promise<NeuronStats> {
  return json<NeuronStats>('/neurons/stats');
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

export interface Graph3DNode {
  id: number; label: string; department: string; layer: number;
  node_type: string; role_key: string | null; invocations: number;
  avg_utility: number; parent_id: number | null;
}
export interface Graph3DEdge {
  source: number; target: number; weight: number; co_fire_count: number;
}
export interface Graph3DResponse { neurons: Graph3DNode[]; edges: Graph3DEdge[]; }

export function fetchGraph3D(minWeight = 0.3, maxEdges = 2000): Promise<Graph3DResponse> {
  return json<Graph3DResponse>(`/neurons/graph-3d?min_weight=${minWeight}&max_edges=${maxEdges}`);
}

export function fetchCostReport(): Promise<CostReport> {
  return json<CostReport>('/admin/cost-report');
}

export function fetchQueryHistory(): Promise<QuerySummary[]> {
  return json<QuerySummary[]>('/queries');
}

export function fetchQueryDetail(id: number): Promise<QueryDetail> {
  return json<QueryDetail>(`/queries/${id}`);
}

export function fetchQueryRunCounts(texts: string[]): Promise<Record<string, number>> {
  return json<Record<string, number>>('/queries/run-counts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(texts),
  });
}

export interface SlotSpec {
  mode: string;
  token_budget: number;
  top_k: number;
  max_output_tokens?: number;
  label?: string;
}

export interface GraphCapacity {
  active_neurons: number;
  total_content_tokens: number;
  total_summary_tokens: number;
  total_tokens: number;
}

export function fetchGraphCapacity(): Promise<GraphCapacity> {
  return json<GraphCapacity>('/neurons/capacity');
}

export function submitQuery(message: string, slots: SlotSpec[], chat_style?: string): Promise<QueryResponse> {
  return json<QueryResponse>('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, slots_v2: slots, chat_style }),
  });
}

export interface StageEvent {
  stage: string;
  status: 'done' | 'skipped' | 'active';
  detail?: Record<string, unknown>;
}

export function submitQueryStream(
  message: string,
  onStage?: (event: StageEvent) => void,
  prior_neuron_ids?: number[],
  slots?: SlotSpec[],
): { promise: Promise<QueryResponse>; abort: () => void } {
  const controller = new AbortController();

  const promise = (async () => {
    const body: Record<string, unknown> = { message };
    if (slots && slots.length > 0) {
      body.slots = slots;
    }
    if (prior_neuron_ids && prior_neuron_ids.length > 0) {
      body.prior_neuron_ids = prior_neuron_ids;
    }
    const res = await fetch('/query/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult: QueryResponse | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE frames
      const parts = buffer.split('\n\n');
      buffer = parts.pop()!; // keep incomplete frame

      for (const part of parts) {
        if (!part.trim()) continue;
        let eventType = '';
        let dataStr = '';
        for (const line of part.split('\n')) {
          if (line.startsWith('event: ')) eventType = line.slice(7);
          else if (line.startsWith('data: ')) dataStr = line.slice(6);
        }
        if (!eventType || !dataStr) continue;
        const parsed = JSON.parse(dataStr);
        if (eventType === 'stage') {
          if (onStage) onStage(parsed as StageEvent);
        } else if (eventType === 'result') {
          finalResult = parsed as QueryResponse;
        } else if (eventType === 'error') {
          throw new Error(parsed.message);
        }
      }
    }

    if (!finalResult) throw new Error('Stream ended without result');
    return finalResult;
  })();

  return { promise, abort: () => controller.abort() };
}

export function evaluateQuery(queryId: number, model: string): Promise<EvalResponse> {
  return json<EvalResponse>(`/query/${queryId}/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  });
}

export function submitRating(queryId: number, utility: number): Promise<RatingResponse> {
  return json<RatingResponse>(`/query/${queryId}/rate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ utility }),
  });
}

export function refineQuery(queryId: number, model: string, maxTokens: number = 4096, userContext?: string): Promise<RefineResponse> {
  const body: Record<string, string | number> = { model, max_tokens: maxTokens };
  if (userContext?.trim()) body.user_context = userContext.trim();
  return json<RefineResponse>(`/query/${queryId}/refine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function applyRefinements(queryId: number, updateIds: number[], newNeuronIds: number[]): Promise<ApplyRefineResponse> {
  return json<ApplyRefineResponse>(`/query/${queryId}/refine/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ update_ids: updateIds, new_neuron_ids: newNeuronIds }),
  });
}

// ── Synaptic Learning ──

export function fetchLearningAnalytics(): Promise<import('./types').LearningAnalytics> {
  return json<import('./types').LearningAnalytics>('/learning-analytics');
}

export function fetchDeptChord(layer = 1, minWeight = 0.15): Promise<DeptChordEntry[]> {
  return json<DeptChordEntry[]>(`/neurons/edges/department-chord?layer=${layer}&min_weight=${minWeight}`);
}

export interface LayerFlowNode {
  key: string;
  layer: number;
  department: string;
  neuron_count?: number;
}

export interface LayerFlowLink {
  source: string;
  target: string;
  total_weight: number;
  edge_count: number;
}

export interface LayerFlowResponse {
  nodes: LayerFlowNode[];
  links: LayerFlowLink[];
}

export function fetchLayerFlow(minWeight = 0.15): Promise<LayerFlowResponse> {
  return json<LayerFlowResponse>(`/neurons/edges/layer-flow?min_weight=${minWeight}`);
}

export function fetchNeuronEdges(id: number, limit = 15): Promise<EgoGraphResponse> {
  return json<EgoGraphResponse>(`/neurons/${id}/edges?limit=${limit}`);
}

export function fetchSpreadTrail(queryId: number): Promise<SpreadTrailResponse> {
  return json<SpreadTrailResponse>(`/neurons/edges/spread-trail?query_id=${queryId}`);
}

export interface SpreadLogEntry {
  query_id: number;
  user_message: string;
  created_at: string | null;
  promoted_count: number;
  avg_boost: number;
  max_boost: number;
  cross_dept: boolean;
  promoted_neurons: { neuron_id: number; label: string; department: string; boost: number }[];
}

export interface SpreadLogResponse {
  total_queries: number;
  queries_with_spread: number;
  spread_rate: number;
  entries: SpreadLogEntry[];
  top_neurons: { neuron_id: number; label: string; department: string; spread_count: number }[];
  top_corridors: { pair: string; count: number }[];
}

export function fetchSpreadLog(limit = 100): Promise<SpreadLogResponse> {
  return json<SpreadLogResponse>(`/neurons/edges/spread-log?limit=${limit}`);
}

export function fetchRefinementHistory(params?: {
  action?: string;
  field?: string;
  neuron_id?: number;
  since?: string;
  until?: string;
  limit?: number;
}): Promise<NeuronRefinementEntry[]> {
  const q = new URLSearchParams();
  if (params?.action) q.set('action', params.action);
  if (params?.field) q.set('field', params.field);
  if (params?.neuron_id) q.set('neuron_id', String(params.neuron_id));
  if (params?.since) q.set('since', params.since);
  if (params?.until) q.set('until', params.until);
  if (params?.limit) q.set('limit', String(params.limit));
  const qs = q.toString();
  return json<NeuronRefinementEntry[]>(`/neurons/refinements${qs ? '?' + qs : ''}`);
}

export interface CheckpointResponse {
  status: string;
  filename: string;
  neuron_count: number;
  commit_sha: string;
}

export function createCheckpoint(): Promise<CheckpointResponse> {
  return json<CheckpointResponse>('/admin/checkpoint', { method: 'POST' });
}

// Autopilot
export function fetchAutopilotConfig(): Promise<AutopilotConfig> {
  return json<AutopilotConfig>('/admin/autopilot/config');
}

export function updateAutopilotConfig(update: Partial<AutopilotConfig>): Promise<AutopilotConfig> {
  return json<AutopilotConfig>('/admin/autopilot/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
}

export function triggerAutopilotTick(): Promise<AutopilotTickResponse> {
  return json<AutopilotTickResponse>('/admin/autopilot/tick', { method: 'POST' });
}

export function triggerAutopilotRunNow(): Promise<AutopilotTickResponse> {
  return json<AutopilotTickResponse>('/admin/autopilot/run-now', { method: 'POST' });
}

export function fetchAutopilotRuns(): Promise<AutopilotRun[]> {
  return json<AutopilotRun[]>('/admin/autopilot/runs');
}

export function fetchAutopilotRunChanges(runId: number): Promise<AutopilotChange[]> {
  return json<AutopilotChange[]>(`/admin/autopilot/runs/${runId}/changes`);
}

export function cancelAutopilotTick(): Promise<AutopilotTickResponse> {
  return json<AutopilotTickResponse>('/admin/autopilot/cancel', { method: 'POST' });
}

export function fetchAutopilotStatus(): Promise<{ running: boolean; step: string; detail: string }> {
  return json<{ running: boolean; step: string; detail: string }>('/admin/autopilot/status');
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function fetchPerformance(): Promise<any> {
  return json<unknown>('/admin/performance');
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

export function fetchComplianceAudit(): Promise<ComplianceAuditResponse> {
  return json<ComplianceAuditResponse>('/admin/compliance-audit');
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

// ── Management Reviews ──

export interface ManagementReviewOut {
  id: number;
  review_type: string;
  reviewer: string;
  review_date: string;
  findings: string;
  decisions: string;
  action_items: { description: string; due_date?: string; completed?: boolean }[];
  status: string;
  compliance_snapshot_id: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ReviewCadenceItem {
  review_type: string;
  cadence_days: number;
  last_review_date: string | null;
  next_due_date: string | null;
  is_overdue: boolean;
  days_until_due: number | null;
}

export function fetchReviews(reviewType?: string): Promise<ManagementReviewOut[]> {
  const params = reviewType ? `?review_type=${encodeURIComponent(reviewType)}` : '';
  return json<ManagementReviewOut[]>(`/admin/reviews${params}`);
}

export function createReview(body: {
  review_type: string;
  reviewer: string;
  review_date: string;
  findings: string;
  decisions: string;
  action_items?: { description: string; due_date?: string; completed?: boolean }[];
  status?: string;
}): Promise<ManagementReviewOut> {
  return json<ManagementReviewOut>('/admin/reviews', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updateReview(id: number, body: Record<string, unknown>): Promise<ManagementReviewOut> {
  return json<ManagementReviewOut>(`/admin/reviews/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function fetchReviewCadence(): Promise<ReviewCadenceItem[]> {
  return json<ReviewCadenceItem[]>('/admin/reviews/cadence');
}

// ── Compliance Snapshots ──

export interface ComplianceSnapshotSummary {
  id: number;
  snapshot_date: string;
  pii_clean: boolean;
  coverage_cv: number;
  fairness_pass: boolean;
  missing_citations_count: number;
  stale_neurons_count: number;
  total_neurons: number;
  total_evals: number;
  trigger: string;
  created_at: string | null;
}

export interface ComplianceSnapshotDetail extends ComplianceSnapshotSummary {
  snapshot_data: ComplianceAuditResponse | null;
  diff_summary: Record<string, { prev: unknown; current: unknown; delta?: number }> | null;
}

export function fetchSnapshots(limit = 50): Promise<ComplianceSnapshotSummary[]> {
  return json<ComplianceSnapshotSummary[]>(`/admin/compliance-snapshots?limit=${limit}`);
}

export function createSnapshot(trigger = 'manual'): Promise<ComplianceSnapshotSummary> {
  return json<ComplianceSnapshotSummary>(`/admin/compliance-snapshots?trigger=${trigger}`, { method: 'POST' });
}

export function fetchSnapshotDetail(id: number): Promise<ComplianceSnapshotDetail> {
  return json<ComplianceSnapshotDetail>(`/admin/compliance-snapshots/${id}`);
}

// ── Evidence Mapping ──

export interface EvidenceMappingOut {
  id: number;
  framework: string;
  requirement_id: string;
  requirement_name: string;
  status: string;
  evidence_type: string;
  evidence_location: string;
  verification_query: string | null;
  last_verified: string | null;
  last_verified_by: string | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export function fetchEvidenceMap(framework?: string): Promise<EvidenceMappingOut[]> {
  const params = framework ? `?framework=${encodeURIComponent(framework)}` : '';
  return json<EvidenceMappingOut[]>(`/admin/evidence-map${params}`);
}

export function updateEvidence(id: number, body: Record<string, unknown>): Promise<EvidenceMappingOut> {
  return json<EvidenceMappingOut>(`/admin/evidence-map/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function verifyAllEvidence(): Promise<{ passed: number; failed: number; total: number }> {
  return json<{ passed: number; failed: number; total: number }>('/admin/evidence-map/verify-all', { method: 'POST' });
}

export function seedEvidenceMap(): Promise<{ status: string; count: number }> {
  return json<{ status: string; count: number }>('/admin/evidence-map/seed', { method: 'POST' });
}

// ── Evidence Content Viewer ──

export interface EvidenceContentResponse {
  path: string;
  language: string;
  content: string;
  size: number;
}

export function fetchEvidenceContent(path: string): Promise<EvidenceContentResponse> {
  return json<EvidenceContentResponse>(`/admin/evidence-content?path=${encodeURIComponent(path)}`);
}

// ── Compliance Report ──

export function fetchComplianceReport(framework?: string): Promise<unknown> {
  const params = framework ? `?framework=${encodeURIComponent(framework)}` : '';
  return json<unknown>(`/admin/compliance-report${params}`);
}

// ── Security Compliance Frameworks (FedRAMP, SOC 2, CMMC) ──

export interface FrameworkSummary {
  framework: string;
  total_controls?: number;
  total_criteria?: number;
  total_practices?: number;
  status_counts: Record<string, number>;
  families?: { id: string; name: string; total: number; [key: string]: unknown }[];
  categories?: Record<string, Record<string, number>>;
}

export interface FrameworkControl {
  id: string;
  family?: string;
  family_name?: string;
  category?: string;
  title: string;
  status: string;
  detail: string;
  points_of_focus?: string[];
}

export function fetchFrameworksSummary(): Promise<{ frameworks: FrameworkSummary[] }> {
  return json<{ frameworks: FrameworkSummary[] }>('/admin/frameworks');
}

export function fetchFedRAMPControls(family?: string, status?: string): Promise<{ summary: FrameworkSummary; controls: FrameworkControl[] }> {
  const params = new URLSearchParams();
  if (family) params.set('family', family);
  if (status) params.set('status', status);
  const qs = params.toString();
  return json(`/admin/frameworks/fedramp${qs ? '?' + qs : ''}`);
}

export function fetchSOC2Criteria(category?: string, status?: string): Promise<{ summary: FrameworkSummary; criteria: FrameworkControl[] }> {
  const params = new URLSearchParams();
  if (category) params.set('category', category);
  if (status) params.set('status', status);
  const qs = params.toString();
  return json(`/admin/frameworks/soc2${qs ? '?' + qs : ''}`);
}

export function fetchCMMCPractices(family?: string, status?: string): Promise<{ summary: FrameworkSummary; practices: FrameworkControl[] }> {
  const params = new URLSearchParams();
  if (family) params.set('family', family);
  if (status) params.set('status', status);
  const qs = params.toString();
  return json(`/admin/frameworks/cmmc${qs ? '?' + qs : ''}`);
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

// ── Provenance (Knowledge Governance) ──

export interface SourceDocumentOut {
  id: number;
  canonical_id: string;
  family: string;
  version: string | null;
  status: string;
  authority_level: string | null;
  issuing_body: string | null;
  effective_date: string | null;
  url: string | null;
  notes: string | null;
  superseded_by_id: number | null;
  created_at: string | null;
}

export interface AuthoritySummaryItem {
  authority_level: string | null;
  count: number;
}

export interface StaleProvenanceNeuron {
  neuron_id: number;
  label: string;
  layer: number;
  department: string;
  stale_sources: {
    link_id: number;
    source_canonical_id: string;
    source_family: string;
    source_status: string;
    flagged_at: string | null;
  }[];
}

export function fetchSourceDocuments(): Promise<SourceDocumentOut[]> {
  return json<SourceDocumentOut[]>('/admin/source-documents');
}

export function fetchAuthoritySummary(): Promise<AuthoritySummaryItem[]> {
  return json<AuthoritySummaryItem[]>('/admin/provenance/authority-summary');
}

export function fetchProvenanceStale(): Promise<StaleProvenanceNeuron[]> {
  return json<StaleProvenanceNeuron[]>('/admin/provenance/stale');
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

// ── Observation Review Pipeline ──

export function fetchObservations(status?: string, limit = 50): Promise<ObservationSummary[]> {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  params.set('limit', String(limit));
  return json<ObservationSummary[]>(`/ingest/observations?${params}`);
}

export function fetchObservationDetail(obsId: number): Promise<ObservationDetail> {
  return json<ObservationDetail>(`/ingest/observations/${obsId}`);
}

export function evaluateObservation(obsId: number, model: string): Promise<ObservationEvalResponse> {
  return json<ObservationEvalResponse>(`/ingest/observations/${obsId}/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  });
}

export function evaluateObservationBatch(ids: number[], model: string): Promise<ObservationEvalResponse[]> {
  return json<ObservationEvalResponse[]>('/ingest/observations/evaluate-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ observation_ids: ids, model }),
  });
}

export function applyObservation(obsId: number, updateIndices: number[], newNeuronIndices: number[]): Promise<ObservationApplyResponse> {
  return json<ObservationApplyResponse>(`/ingest/observations/${obsId}/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ update_indices: updateIndices, new_neuron_indices: newNeuronIndices }),
  });
}

export function approveObservation(obsId: number): Promise<{ observation_id: number; neuron_id: number }> {
  return json<{ observation_id: number; neuron_id: number }>(`/ingest/observations/${obsId}/approve`, { method: 'POST' });
}

export function rejectObservation(obsId: number): Promise<{ observation_id: number; status: string }> {
  return json<{ observation_id: number; status: string }>(`/ingest/observations/${obsId}/reject`, { method: 'POST' });
}

// ── Compliance Suite (Unified Audit) ──

export interface ComplianceSuiteDashboardResponse {
  frameworks: Record<string, {
    total: number;
    passed: number;
    failed: number;
    partial: number;
    attested: number;
    untested: number;
    compliance_pct: number;
  }>;
  latest_run: {
    id: number;
    started_at: string;
    passed: number;
    failed: number;
    skipped: number;
    duration_ms: number;
  } | null;
  total_providers: number;
  total_controls: number;
  expiring_attestations: { provider_id: string; attested_by: string; re_attestation_due: string }[];
}

export interface ComplianceSuiteControl {
  framework: string;
  control_id: string;
  title: string;
  family: string;
  description: string;
  external_ref: string;
  provider_count: number;
  provider_ids: string[];
  evidence_types: string[];
}

export interface ComplianceSuiteRunSummary {
  id: number;
  started_at: string | null;
  completed_at: string | null;
  framework_filter: string | null;
  total_providers: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
  triggered_by: string;
}

export interface ControlDetailResponse {
  control: {
    framework: string;
    control_id: string;
    title: string;
    family: string;
    description: string;
    external_ref: string;
  };
  providers: {
    id: string;
    title: string;
    evidence_type: string;
    code_refs: string[];
    rationale: string | null;
  }[];
  history: {
    provider_id: string;
    passed: boolean;
    detail: Record<string, unknown>;
    duration_ms: number;
    collected_at: string | null;
    run_id: number;
  }[];
}

export interface SuiteProgressEvent {
  stage: string;
  completed: number;
  total: number;
  count?: number;
  run_id?: number;
}

export function fetchComplianceSuiteDashboard(): Promise<ComplianceSuiteDashboardResponse> {
  return json<ComplianceSuiteDashboardResponse>('/admin/compliance/dashboard');
}

export function fetchComplianceSuiteControls(framework?: string): Promise<ComplianceSuiteControl[]> {
  const params = framework ? `?framework=${encodeURIComponent(framework)}` : '';
  return json<ComplianceSuiteControl[]>(`/admin/compliance/controls${params}`);
}

export function fetchComplianceSuiteRuns(limit = 50): Promise<ComplianceSuiteRunSummary[]> {
  return json<ComplianceSuiteRunSummary[]>(`/admin/compliance/runs?limit=${limit}`);
}

export function fetchComplianceSuiteControlDetail(framework: string, controlId: string): Promise<ControlDetailResponse> {
  return json<ControlDetailResponse>(`/admin/compliance/controls/${encodeURIComponent(framework)}/${encodeURIComponent(controlId)}`);
}

export function submitComplianceAttestation(providerId: string, attestedBy: string, notes = '', days = 90): Promise<{ status: string }> {
  const params = new URLSearchParams({
    provider_id: providerId,
    attested_by: attestedBy,
    notes,
    re_attestation_days: String(days),
  });
  return json<{ status: string }>(`/admin/compliance/attest?${params}`, { method: 'POST' });
}

export function runComplianceSuite(
  framework: string | undefined,
  onProgress: (event: SuiteProgressEvent) => void,
  providerIds?: string[],
): Promise<void> {
  return new Promise(async (resolve, reject) => {
    try {
      const params = framework ? `?framework=${encodeURIComponent(framework)}` : '';
      const hasBody = providerIds && providerIds.length > 0;
      const res = await fetch(`/admin/compliance/run-suite${params}`, {
        method: 'POST',
        headers: hasBody ? { 'Content-Type': 'application/json' } : undefined,
        body: hasBody ? JSON.stringify({ provider_ids: providerIds }) : undefined,
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split('\n\n');
        buffer = parts.pop()!;

        for (const part of parts) {
          if (!part.trim()) continue;
          let eventType = '';
          let dataStr = '';
          for (const line of part.split('\n')) {
            if (line.startsWith('event: ')) eventType = line.slice(7);
            else if (line.startsWith('data: ')) dataStr = line.slice(6);
          }
          if (!eventType || !dataStr) continue;
          const parsed = JSON.parse(dataStr);
          if (eventType === 'progress') {
            onProgress(parsed as SuiteProgressEvent);
          } else if (eventType === 'result') {
            resolve();
            return;
          } else if (eventType === 'error') {
            reject(new Error(parsed.message));
            return;
          }
        }
      }
      resolve();
    } catch (e) {
      reject(e);
    }
  });
}


// ── Proposal Queue ──────────────────────────────────────────────────

export interface ProposalSummary {
  id: number;
  autopilot_run_id: number | null;
  query_id: number | null;
  state: string;
  gap_source: string | null;
  gap_description: string | null;
  priority_score: number;
  llm_model: string | null;
  eval_overall: number;
  reviewed_by: string | null;
  reviewed_at: string | null;
  applied_at: string | null;
  applied_by: string | null;
  item_count: number;
  origin: string; // "autopilot" | "integrity" | "document" | "manual"
  is_autopilot: boolean;
  created_at: string | null;
}

export interface GapEvidence {
  signal: string;
  description: string;
  metric_value: number;
  threshold: number;
  neuron_ids: number[];
  query_ids: number[];
}

export interface DocumentEvidence {
  source: string;
  document: string;
  section: string;
  section_id: string;
  job_id: string;
}

export interface ProposalItem {
  id: number;
  action: string;
  target_neuron_id: number | null;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  neuron_spec_json: string | null;
  reason: string | null;
  created_neuron_id: number | null;
  refinement_id: number | null;
}

export interface ProposalDetail {
  id: number;
  autopilot_run_id: number | null;
  query_id: number | null;
  state: string;
  gap_source: string | null;
  gap_description: string | null;
  gap_evidence: (GapEvidence | DocumentEvidence | Record<string, unknown>)[];
  priority_score: number;
  llm_reasoning: string | null;
  llm_model: string | null;
  prompt_hash: string | null;
  eval_overall: number;
  eval_text: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  applied_at: string | null;
  applied_by: string | null;
  items: ProposalItem[];
  created_at: string | null;
  updated_at: string | null;
}

export interface ProposalStats {
  proposed: number;
  approved: number;
  rejected: number;
  applied: number;
  total: number;
}

export function fetchProposals(
  state?: string, gapSource?: string, origin?: string,
): Promise<ProposalSummary[]> {
  const parts: string[] = [];
  if (state) parts.push(`state=${state}`);
  if (gapSource) parts.push(`gap_source=${gapSource}`);
  if (origin) parts.push(`origin=${origin}`);
  const qs = parts.length ? `?${parts.join('&')}` : '';
  return json<ProposalSummary[]>(`/admin/proposals/${qs}`);
}

export function fetchProposalDetail(id: number): Promise<ProposalDetail> {
  return json<ProposalDetail>(`/admin/proposals/${id}`);
}

export function reviewProposal(id: number, action: 'approve' | 'reject', reviewer: string, notes: string = ''): Promise<ProposalDetail> {
  return json<ProposalDetail>(`/admin/proposals/${id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, reviewer, notes }),
  });
}

export function applyProposal(id: number, appliedBy: string): Promise<ProposalDetail> {
  return json<ProposalDetail>(`/admin/proposals/${id}/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ applied_by: appliedBy }),
  });
}

export function fetchProposalStats(): Promise<ProposalStats> {
  return json<ProposalStats>('/admin/proposals/stats');
}

export interface Whoami { user_id: string; role: string; source: string; }
export function fetchWhoami(): Promise<Whoami> {
  return json<Whoami>('/admin/proposals/whoami');
}

export function fetchNeuronProvenance(neuronId: number): Promise<Record<string, unknown>> {
  return json<Record<string, unknown>>(`/admin/neurons/${neuronId}/provenance`);
}

// ── Document Ingest ──

export interface DocumentIngestJob {
  id: string;
  status: string;
  step: string;
  filename: string;
  file_format: string;
  file_size_bytes: number;
  total_pages: number | null;
  title: string;
  source_type: string;
  authority_level: string;
  citation: string;
  source_url: string | null;
  department: string | null;
  role_key: string | null;
  total_sections: number;
  current_section: number;
  proposal_ids: number[];
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  model: string;
  duplicates_flagged: number;
  errors: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface DocumentStructure {
  title: string;
  total_pages: number | null;
  sections: Array<{
    id: string;
    title: string;
    level: number;
    char_start: number;
    char_end: number;
    page_start: number | null;
    page_end: number | null;
    parent_section_id: string | null;
  }>;
}

export async function uploadDocument(
  file: File,
  metadata: {
    title?: string;
    source_type?: string;
    authority_level?: string;
    citation?: string;
    source_url?: string;
    department?: string;
    role_key?: string;
    model?: string;
  },
): Promise<DocumentIngestJob> {
  const { getAuthHeaders } = await import('./auth');
  const authHeaders = getAuthHeaders();

  const form = new FormData();
  form.append('file', file);
  if (metadata.title) form.append('title', metadata.title);
  if (metadata.source_type) form.append('source_type', metadata.source_type);
  if (metadata.authority_level) form.append('authority_level', metadata.authority_level);
  if (metadata.citation) form.append('citation', metadata.citation);
  if (metadata.source_url) form.append('source_url', metadata.source_url);
  if (metadata.department) form.append('department', metadata.department);
  if (metadata.role_key) form.append('role_key', metadata.role_key);
  if (metadata.model) form.append('model', metadata.model);

  const res = await fetch('/admin/documents/upload', {
    method: 'POST',
    headers: authHeaders,
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function fetchDocumentJobs(status?: string): Promise<DocumentIngestJob[]> {
  const params = status ? `?status=${status}` : '';
  return json<DocumentIngestJob[]>(`/admin/documents/${params}`);
}

export function fetchDocumentJobStatus(jobId: string): Promise<DocumentIngestJob> {
  return json<DocumentIngestJob>(`/admin/documents/${jobId}`);
}

export function fetchDocumentStructure(jobId: string): Promise<DocumentStructure> {
  return json<DocumentStructure>(`/admin/documents/${jobId}/structure`);
}

export function cancelDocumentJob(jobId: string): Promise<DocumentIngestJob> {
  return json<DocumentIngestJob>(`/admin/documents/${jobId}/cancel`, { method: 'POST' });
}

// ── Integrity System ──────────────────────────────────────────────

export function fetchIntegrityDashboard(): Promise<IntegrityDashboard> {
  return json<IntegrityDashboard>('/admin/integrity/dashboard');
}

export function runHomeostasisScan(params: {
  scope?: string; scale_factor?: number; floor_threshold?: number; initiated_by?: string;
}): Promise<IntegrityScanResponse> {
  return json<IntegrityScanResponse>('/admin/integrity/homeostasis/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export function runDuplicatesScan(params: {
  scope?: string; similarity_threshold?: number; max_pairs?: number;
  cross_department_only?: boolean; initiated_by?: string;
}): Promise<IntegrityScanResponse> {
  return json<IntegrityScanResponse>('/admin/integrity/duplicates/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export function runConnectionsScan(params: {
  scope?: string; similarity_threshold?: number; max_suggestions?: number;
  exclude_same_parent?: boolean; initiated_by?: string;
}): Promise<IntegrityScanResponse> {
  return json<IntegrityScanResponse>('/admin/integrity/connections/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export function runConflictsScan(params: {
  scope?: string; sim_min?: number; sim_max?: number;
  batch_size?: number; max_pairs?: number; initiated_by?: string;
}): Promise<IntegrityScanResponse> {
  return json<IntegrityScanResponse>('/admin/integrity/conflicts/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export function runAgingScan(params: {
  scope?: string; staleness_overrides?: Record<string, number> | null;
  include_never_verified?: boolean; min_invocations?: number; initiated_by?: string;
}): Promise<IntegrityScanResponse> {
  return json<IntegrityScanResponse>('/admin/integrity/aging/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export function applyHomeostasisScan(scanId: number, reviewer: string): Promise<IntegrityApplyResult> {
  return json<IntegrityApplyResult>(`/admin/integrity/homeostasis/${scanId}/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewer }),
  });
}

export function fetchIntegrityScans(params?: {
  scan_type?: string; status?: string; limit?: number;
}): Promise<IntegrityScanSummary[]> {
  const qs = new URLSearchParams();
  if (params?.scan_type) qs.set('scan_type', params.scan_type);
  if (params?.status) qs.set('status', params.status);
  if (params?.limit) qs.set('limit', String(params.limit));
  const q = qs.toString();
  return json<IntegrityScanSummary[]>(`/admin/integrity/scans${q ? '?' + q : ''}`);
}

export function fetchIntegrityScanDetail(scanId: number): Promise<IntegrityScanDetail> {
  return json<IntegrityScanDetail>(`/admin/integrity/scans/${scanId}`);
}

export function fetchIntegrityFindings(params?: {
  finding_type?: string; status?: string; severity?: string; limit?: number;
}): Promise<IntegrityFinding[]> {
  const qs = new URLSearchParams();
  if (params?.finding_type) qs.set('finding_type', params.finding_type);
  if (params?.status) qs.set('status', params.status);
  if (params?.severity) qs.set('severity', params.severity);
  if (params?.limit) qs.set('limit', String(params.limit));
  const q = qs.toString();
  return json<IntegrityFinding[]>(`/admin/integrity/findings${q ? '?' + q : ''}`);
}

export function fetchIntegrityFindingDetail(findingId: number): Promise<IntegrityFindingDetail> {
  return json<IntegrityFindingDetail>(`/admin/integrity/findings/${findingId}`);
}

export function resolveIntegrityFinding(
  findingId: number, resolution: string, reviewer: string, notes?: string,
): Promise<IntegrityFinding> {
  return json<IntegrityFinding>(`/admin/integrity/findings/${findingId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resolution, reviewer, notes: notes || '' }),
  });
}

export function dismissIntegrityFinding(
  findingId: number, reviewer: string, notes?: string,
): Promise<IntegrityFinding> {
  return json<IntegrityFinding>(`/admin/integrity/findings/${findingId}/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewer, notes: notes || '' }),
  });
}

export function proposeIntegrityFinding(
  findingId: number, resolution: string, reviewer: string, notes?: string,
): Promise<IntegrityProposeResult> {
  return json<IntegrityProposeResult>(`/admin/integrity/findings/${findingId}/propose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resolution, reviewer, notes: notes || '' }),
  });
}

export function bulkResolveIntegrityFindings(
  findingIds: number[], resolution: string, reviewer: string, notes?: string,
): Promise<IntegrityBulkResolveResult> {
  return json<IntegrityBulkResolveResult>('/admin/integrity/findings/bulk-resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ finding_ids: findingIds, resolution, reviewer, notes: notes || '' }),
  });
}
