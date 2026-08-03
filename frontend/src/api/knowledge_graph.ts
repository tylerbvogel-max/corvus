// Knowledge-graph capability adapter — query pipeline (incl. streaming),
// neurons, engrams, lineage, graph projections.
//
// Route ownership is the generated table in ../contracts/capabilities.ts;
// functions here call only routes that table assigns to 'knowledge_graph'.

import type { components } from '../contracts/schema';
import type {
  TreeNode,
  NeuronDetail,
  NeuronScores,
  NeuronStats,
  QueryResponse,
  QuerySummary,
  QueryDetail,
  EvalResponse,
  RatingResponse,
  RefineResponse,
  ApplyRefineResponse,
  NeuronRefinementEntry,
  DeptChordEntry,
  EgoGraphResponse,
  SpreadTrailResponse,
  NeuronScoreResponse,
} from '../types';
import { json } from './http';

// ── Available LLM models ──
export interface ModelOption {
  display_name: string;
  provider: string;
  api_id: string;
  tier: string;
  input_price: number;
  output_price: number;
  context_window_tokens: number;
  effective_model?: string;
  effective_provider?: string;
  is_primary?: boolean;
}

export function fetchAvailableModels(): Promise<ModelOption[]> {
  return json<ModelOption[]>('/models');
}

// ── Simple chat (no neuron pipeline) ──
export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

export type ChatResponse = components['schemas']['ChatResponse'];

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

export interface Graph3DNode {
  id: number; label: string; department: string; layer: number;
  node_type: string; abstraction_type: string | null;
  role_key: string | null; invocations: number;
  avg_utility: number; centrality: number; parent_id: number | null;
  summary: string | null;
  entities: string[];
  authority_level: string | null;
  created_at: string | null;
  last_verified: string | null;
  health_state: 'contested' | 'stale' | 'low-confidence' | 'reinforced' | 'quiet' | 'current';
  health_reason: string;
}

export interface Graph3DEdge {
  source: number; target: number; weight: number; co_fire_count: number;
  edge_type: string;
  activity_1d: number;
  activity_7d: number;
  activity_30d: number;
  activity_all: number;
}

export interface Graph3DReplaySegment {
  source: number;
  target: number;
  weight: number;
  kind: 'coactivation' | 'spread';
  source_rank: number;
  target_rank: number;
}

export interface Graph3DReplayTrace {
  query_id: number;
  created_at: string | null;
  query_preview: string;
  neuron_count: number;
  spread_derived: boolean;
  firings: Array<{
    neuron_id: number;
    rank: number;
    score: number;
    spread_boost: number;
  }>;
  segments: Graph3DReplaySegment[];
}

export interface Graph3DReplayPayload {
  basis: string;
  sampled_from: number;
  traces: Graph3DReplayTrace[];
}

export interface Graph3DResponse {
  neurons: Graph3DNode[];
  edges: Graph3DEdge[];
  replays?: Graph3DReplayPayload;
}

export function fetchGraph3D(
  minWeight = 0.25,
  maxEdges = 12000,
  perNode = 3,
  replayLimit = 0,
): Promise<Graph3DResponse> {
  return json<Graph3DResponse>(
    `/neurons/graph-3d?min_weight=${minWeight}&max_edges=${maxEdges}&per_node=${perNode}&replay_limit=${replayLimit}`,
  );
}

export interface SemanticCluster {
  cluster_id: number;
  neuron_ids: number[];
  member_count?: number;
  departments: string[];
  avg_internal_weight: number;
  suggested_label: string;
  representative_labels?: string[];
}

export interface SemanticClustersResponse {
  cluster_count: number;
  clusters: SemanticCluster[];
}

export function fetchSemanticClusters(
  minWeight = 0.3,
  minSize = 3,
  minDepartments = 2,
  resolution = 1,
): Promise<SemanticClustersResponse> {
  return json<SemanticClustersResponse>(
    `/neurons/clusters?min_weight=${minWeight}&min_size=${minSize}` +
    `&min_departments=${minDepartments}&resolution=${resolution}`,
  );
}

export function fetchQueryHistory(): Promise<QuerySummary[]> {
  return json<QuerySummary[]>('/queries');
}

export function fetchQueryDetail(id: number): Promise<QueryDetail> {
  return json<QueryDetail>(`/queries/${id}`);
}

export function fetchQueryDossier(id: number): Promise<import('../types').QueryDossier> {
  return json<import('../types').QueryDossier>(`/queries/${id}/dossier`);
}

export type FollowUpSuggestion = components['schemas']['FollowUpSuggestion'];

export type FollowUpSuggestionsResponse = components['schemas']['FollowUpSuggestionsResponse'];

export function fetchFollowUps(queryId: number): Promise<FollowUpSuggestionsResponse> {
  return json<FollowUpSuggestionsResponse>(`/query/${queryId}/followups`, { method: 'POST' });
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
  // Omit for tenant delivery policy; explicit values remain hard safety caps.
  top_k?: number;
  max_output_tokens?: number;
  label?: string;
  effort?: string;  // per-slot reasoning effort override (low|medium|high)
  priming?: boolean; // prefix this slot's prompt with a packed-topic focus line
  spread_hops?: number;  // spread-activation hop cap override (1-6; server default 3)
  spread_floor?: number; // spread-activation min-activation floor (0-0.5; server default 0.15)
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
  // Server-authoritative per-stage timing. Emitted at the top level of the
  // SSE payload by pipeline/runner.py::_payload_for_emit. Missing on `active`
  // events (where the stage hasn't finished yet) and on stages emitted by
  // older clients; consumers must handle the undefined case.
  duration_ms?: number;
  detail?: Record<string, unknown>;
}

export interface SessionOpts {
  persist: boolean;           // opt in to server-side CLI session persistence
  llmSessionId?: string | null; // prior turn's session id (resume)
  refreshContext?: boolean;   // force fresh retrieval this turn (skip drift-gate reuse)
}

export function submitQueryStream(
  message: string,
  onStage?: (event: StageEvent) => void,
  prior_neuron_ids?: number[],
  slots?: SlotSpec[],
  effort?: string,
  sessionOpts?: SessionOpts,
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
    if (effort) {
      body.effort = effort;
    }
    if (sessionOpts?.persist) {
      body.persist_session = true;
      if (sessionOpts.llmSessionId) body.llm_session_id = sessionOpts.llmSessionId;
      if (sessionOpts.refreshContext) body.refresh_context = true;
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
          const err = new Error(parsed.message) as Error & { failedStage?: string };
          if (parsed.failed_stage) err.failedStage = parsed.failed_stage;
          throw err;
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
export function fetchLearningAnalytics(): Promise<import('../types').LearningAnalytics> {
  return json<import('../types').LearningAnalytics>('/learning-analytics');
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
  // Headline counts/rate cover a rate_window_days time window; entries and
  // corridors cover the `limit` most recent queries.
  rate_window_days: number;
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

// ── Engrams (durability-frontend-contracts criterion 7) ──
// These were called with bare fetch() from EngramPage and SigmaGraphPage,
// which meant a tenant without the knowledge_graph capability surfaced an
// unexplained error instead of an honestly absent feature.

export function fetchEngrams<T = unknown>(): Promise<T[]> {
  return json<T[]>('/engrams/');
}

export function fetchEngramSummary<T = unknown>(): Promise<T> {
  return json<T>('/engrams/stats/summary');
}

export function fetchEngramGaps<T = unknown>(): Promise<T[]> {
  return json<T[]>('/engrams/coverage/gaps');
}

export function resolveEngram<T = unknown>(id: string | number): Promise<T> {
  return json<T>(`/engrams/${id}/resolve`, { method: 'POST' });
}

export function createEngram<T = unknown>(body: unknown): Promise<T> {
  return json<T>('/engrams/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── Graph views ──

export function fetchGraph3d<T = unknown>(): Promise<T> {
  return json<T>('/neurons/graph-3d');
}

export function fetchClusters<T = unknown>(): Promise<T> {
  return json<T>('/neurons/clusters');
}

export function fetchEngram<T = unknown>(id: string | number): Promise<T> {
  return json<T>(`/engrams/${id}`);
}
