export interface TreeNode {
  id: number;
  layer: number;
  node_type: string;
  label: string;
  department: string | null;
  role_key: string | null;
  invocations: number;
  avg_utility: number;
  children?: TreeNode[];
  child_count?: number;
}

export interface NeuronDetail {
  id: number;
  parent_id: number | null;
  layer: number;
  node_type: string;
  label: string;
  content: string | null;
  summary: string | null;
  department: string | null;
  role_key: string | null;
  invocations: number;
  avg_utility: number;
  is_active: boolean;
  cross_ref_departments: string[] | null;
  standard_date: string | null;
}

export interface NeuronScores {
  neuron_id: number;
  burst: number;
  impact: number;
  precision: number;
  novelty: number;
  recency: number;
  relevance: number;
  combined: number;
}

export interface RoleBubble {
  role: string;
  department: string;
  neuron_count: number;
  total_invocations: number;
  avg_utility: number;
}

export interface NeuronStats {
  total_neurons: number;
  by_layer: Record<string, number>;
  by_type: Record<string, number>;
  by_department: Record<string, number>;
  by_department_roles: Record<string, Record<string, number>>;
  role_bubbles: RoleBubble[];
  total_firings: number;
}

export interface MaintenanceWorkloadCost {
  workload: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  models: string[];
  equivalent_cost_usd: number;
}

export interface CostReport {
  total_queries: number;
  total_cost_usd: number;
  avg_cost_per_query: number;
  total_input_tokens: number;
  total_output_tokens: number;
  maintenance_cost_usd: number;
  maintenance_per_query_usd: number;
  maintenance_by_workload: MaintenanceWorkloadCost[];
  maintenance_since: string | null;
}

export interface CitationRelevanceClaim {
  claim: string;
  tokens: string[];
  score: number | null;
  band?: 'flag' | 'verify' | 'pass' | 'unsupported' | null;
  supported?: boolean;
  reason?: string;
}

export interface CitationRelevance {
  checked: number;
  scored: number;
  min: number | null;
  mean: number | null;
  flagged?: number;
  escalated?: number;
  unsupported?: number;
  escalation_status?: string;
  claims: CitationRelevanceClaim[];
}

export interface SlotResult {
  mode: string;
  model: string;
  neurons: boolean;
  citation_relevance?: CitationRelevance | null;
  response: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  observed_total_input_tokens?: number;
  estimated_memory_tokens?: number;
  memory_estimation_error_tokens?: number | null;
  duration_ms: number;
  token_budget: number | null;
  top_k: number | null;
  label: string | null;
  error?: boolean;
  citations_fabricated?: number;  // fake citations this slot produced (stripped by the exit layer)
  ungrounded_refs?: number;       // standards/regs named as authority but absent from the retrieved context
  ungrounded_ref_list?: string[]; // the normalised refs behind that count — drives inline answer marks
  effort?: string;                // effective reasoning effort this slot ran at
}

export interface NeuronScoreResponse {
  neuron_id: number;
  combined: number;
  burst: number;
  impact: number;
  precision: number;
  novelty: number;
  recency: number;
  relevance: number;
  spread_boost: number;
  entity_type?: 'neuron' | 'engram';
  label: string | null;
  department: string | null;
  layer: number;
  parent_id: number | null;
  summary: string | null;
}

export interface InputGuardOut {
  verdict: string;
  flags: { description: string; severity: string; pattern?: string }[];
  flag_count: number;
}

export interface GroundingOut {
  grounded: boolean | null;
  confidence: number | null;
  overlap_terms?: number;
  response_terms?: number;
  ungrounded_references?: string[];
  reason: string;
}

export interface EntailmentResultOut {
  claim: string;
  sources: string[];
  supported: boolean | null;
  reason: string;
}

// Opt-in claim-entailment pass on the primary answer (advisory).
// status: ok | no_answer | no_hop_session | no_cited_claims |
//         no_resolvable_sources | llm_error | parse_error
export interface EntailmentOut {
  checked: number;
  status: string;
  unsupported_count?: number;
  results?: EntailmentResultOut[];
  cost_usd?: number;
  error?: string;
}

export interface OutputCheckOut {
  mode: string | null;
  risk_flags: { category: string; description: string; excerpt: string }[];
  grounding: GroundingOut | null;
  entailment?: EntailmentOut | null;
}

export interface StageTelemetry {
  stage: string;
  status: 'done' | 'error' | 'skipped';
  duration_ms: number;
  detail: Record<string, unknown>;
  error_message: string | null;
}

export interface CitationSource {
  kind: 'neuron' | 'engram';
  id: number;
  label: string | null;
}

export interface QueryResponse {
  query_id: number;
  llm_session_id?: string | null;  // persisted CLI session — pass back to resume
  context_reused?: boolean;        // drift gate reused the active context
  context_overlap?: number | null; // fresh-pack vs active-pack overlap
  intent: string | null;
  departments: string[];
  role_keys: string[];
  keywords: string[];
  neurons_activated: number;
  candidates_considered?: number;
  neurons_delivered?: number;
  estimated_memory_tokens?: number;
  memory_context_chars?: number;
  memory_context_utf8_bytes?: number;
  memory_token_budget?: number;
  assembly_stop_reason?: string | null;
  redundancy_suppressed?: number;
  token_estimator_version?: string | null;
  oversized_first_neuron?: boolean;
  recall_latency_ms?: number;
  observed_total_model_input_tokens?: number;
  neuron_scores: NeuronScoreResponse[];
  classify_cost: number;
  classify_input_tokens: number;
  classify_output_tokens: number;
  slots: SlotResult[];
  total_cost: number;
  input_guard?: InputGuardOut | null;
  output_checks?: OutputCheckOut[];
  stage_telemetry?: StageTelemetry[];
  failed_stage?: string | null;
  // Frequency-hop citations: token -> source. Empty/absent when hopping is off.
  citation_map?: Record<string, CitationSource>;
}

export interface QuerySummary {
  id: number;
  user_message: string;
  classified_intent: string | null;
  modes: string[];
  cost_usd: number | null;
  user_rating: number | null;
  created_at: string | null;
}

export interface NeuronHit {
  neuron_id: number;
  label: string;
  layer: number;
  department: string | null;
  parent_id: number | null;
  summary: string | null;
  combined: number;
  burst: number;
  impact: number;
  precision: number;
  novelty: number;
  recency: number;
  relevance: number;
  spread_boost: number;
}

export interface RefinementOut {
  id: number;
  neuron_id: number;
  action: string;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  reason: string | null;
  neuron_label: string | null;
}

export interface QueryDetail {
  id: number;
  user_message: string;
  classified_intent: string | null;
  departments: string[];
  role_keys: string[];
  keywords: string[];
  assembled_prompt: string | null;
  classify_input_tokens: number;
  classify_output_tokens: number;
  classify_cost: number;
  slots: SlotResult[];
  total_cost: number;
  user_rating: number | null;
  eval_text: string | null;
  eval_model: string | null;
  eval_input_tokens: number;
  eval_output_tokens: number;
  eval_scores: EvalScoreOut[];
  eval_winner: string | null;
  neuron_hits: NeuronHit[];
  refinements: RefinementOut[];
  pending_refine: RefineResponse | null;
  created_at: string | null;
}

export interface EvalScoreOut {
  answer_label: string;
  answer_mode: string;
  accuracy: number;
  completeness: number;
  clarity: number;
  faithfulness: number;
  overall: number;
}

export interface SynapticLearningOut {
  outcome: string;
  winner_mode: string | null;
  neurons_adjusted: number;
  edges_adjusted: number;
  avg_delta: number;
  total_reward: number;
  total_penalty: number;
}

export interface EvalResponse {
  query_id: number;
  eval_text: string;
  eval_model: string;
  eval_input_tokens: number;
  eval_output_tokens: number;
  scores: EvalScoreOut[];
  winner: string | null;
  learning: SynapticLearningOut | null;
}

export interface LearningEventOut {
  id: number;
  query_id: number;
  neuron_id: number;
  neuron_label: string | null;
  event_type: string;
  old_avg_utility: number;
  new_avg_utility: number;
  effective_delta: number;
  combined_score: number;
  attribution_weight: number;
  outcome: string;
  winner_mode: string | null;
  created_at: string | null;
}

export interface LearningAnalytics {
  total_events: number;
  total_wins: number;
  total_losses: number;
  avg_reward: number;
  avg_penalty: number;
  recent_events: LearningEventOut[];
}

export interface RatingResponse {
  query_id: number;
  utility: number;
  neurons_updated: number;
}

export interface NeuronUpdateSuggestion {
  neuron_id: number;
  field: string;
  old_value: string;
  new_value: string;
  reason: string;
}

export interface NewNeuronSuggestion {
  parent_id: number | null;
  layer: number;
  node_type: string;
  label: string;
  content: string;
  summary: string;
  department: string | null;
  role_key: string | null;
  reason: string;
}

export interface RefineResponse {
  query_id: number;
  model: string;
  input_tokens: number;
  output_tokens: number;
  reasoning: string;
  neuron_vs_raw_verdict: string;
  updates: NeuronUpdateSuggestion[];
  new_neurons: NewNeuronSuggestion[];
}

export interface ApplyRefineResponse {
  updated: number;
  created: number;
}

// ── AIP Phase 3 — Query Dossier ──────────────────────────────────────────

export interface DossierActionOut {
  id: number;
  kind: string;
  actor_type: string;
  actor_id: string | null;
  state: string;
  requires_approval: boolean;
  reason: string | null;
  parent_action_id: number | null;
  applied_at: string | null;
  error_message: string | null;
  created_at: string | null;
}

export interface DossierOutputViolationOut {
  id: number;
  rule_id: string;
  severity: string;
  action: string;
  matched_span: string | null;
  redaction: string | null;
  detail: Record<string, unknown> | null;
  action_id: number | null;
  created_at: string | null;
}

export interface DossierEvalRunParticipation {
  eval_run_id: number;
  eval_run_case_id: number;
  case_label: string;
  suite_name: string;
  suite_hash: string;
  certified: boolean;
  blocked: boolean;
  scores_json: Record<string, unknown> | null;
  violations_json: Record<string, unknown> | null;
  run_status: string;
  run_started_at: string | null;
  run_completed_at: string | null;
}

export interface DossierIntegrityFindingOut {
  id: number;
  scan_id: number;
  finding_type: string;
  severity: string;
  priority_score: number;
  description: string | null;
  status: string;
  resolution: string | null;
  attributed_via: string;
  overlapping_neuron_ids: number[];
  created_at: string | null;
}

export interface QueryDossier {
  query_id: number;
  user_message: string;
  created_at: string | null;
  pipeline: { stage_telemetry: StageTelemetry[] };
  eval: {
    ad_hoc_scores: EvalScoreOut[];
    eval_run_participations: DossierEvalRunParticipation[];
  };
  output_checks: { violations: DossierOutputViolationOut[] };
  actions: { actions: DossierActionOut[] };
  integrity: { findings: DossierIntegrityFindingOut[] };
}

export interface DeptChordEntry {
  source_dept: string;
  target_dept: string;
  source_department?: string;
  target_department?: string;
  total_weight: number;
  edge_count: number;
}

export interface EgoNeighbor {
  id: number;
  label: string;
  department: string | null;
  layer: number;
  node_type: string;
  weight: number;
  co_fire_count: number;
  hop: number;
}

export interface EgoEdge {
  source: number;
  target: number;
  weight: number;
  co_fire_count: number;
}

export interface EgoGraphResponse {
  center: { id: number; label: string; department: string | null; layer: number };
  neighbors: EgoNeighbor[];
  edges?: EgoEdge[];
}

export interface SpreadTrailNode {
  id: number;
  label: string;
  department: string | null;
  layer: number;
  combined: number;
  spread_boost: number;
}

export interface SpreadTrailEdge {
  source_id: number;
  target_id: number;
  weight: number;
}

export interface SpreadTrailResponse {
  nodes: SpreadTrailNode[];
  edges: SpreadTrailEdge[];
}

export interface NeuronRefinementEntry {
  id: number;
  query_id: number;
  neuron_id: number;
  action: string;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  reason: string | null;
  created_at: string | null;
  neuron_label: string | null;
  query_snippet: string | null;
}

export interface ObservationSummary {
  id: number;
  source: string;
  user_id: string;
  observation_type: string;
  text: string;
  proposed_department: string | null;
  proposed_layer: number;
  similar_neuron_id: number | null;
  similarity_score: number | null;
  status: string;
  created_neuron_id: number | null;
  created_at: string | null;
  eval_model: string | null;
}

export interface NearbyNeuron {
  id: number;
  label: string;
  layer: number;
  node_type: string;
  summary: string;
  invocations: number;
  avg_utility: number;
}

export interface SimilarNeuronDetail {
  id: number;
  label: string;
  layer: number;
  node_type: string;
  department: string | null;
  role_key: string | null;
  summary: string | null;
  content: string | null;
  invocations: number;
  avg_utility: number;
}

export interface ObservationEvalData {
  reasoning: string;
  action: 'create' | 'update' | 'merge' | 'dismiss';
  updates: NeuronUpdateSuggestion[];
  new_neurons: NewNeuronSuggestion[];
  merge_target_id: number | null;
  merge_content_delta: string | null;
}

export interface ObservationDetail {
  id: number;
  source: string;
  user_id: string;
  observation_type: string;
  text: string;
  entities: { type: string; value: string }[];
  app_context: string | null;
  project_path: string | null;
  proposed_department: string | null;
  proposed_role_key: string | null;
  proposed_layer: number;
  similar_neuron_id: number | null;
  similarity_score: number | null;
  similar_neuron: SimilarNeuronDetail | null;
  nearby_neurons: NearbyNeuron[];
  status: string;
  eval_json: ObservationEvalData | null;
  eval_model: string | null;
  eval_input_tokens: number;
  eval_output_tokens: number;
  created_neuron_id: number | null;
  created_at: string | null;
}

export interface ObservationEvalResponse {
  observation_id: number;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  reasoning: string;
  action: string;
  updates: NeuronUpdateSuggestion[];
  new_neurons: NewNeuronSuggestion[];
  merge_target_id: number | null;
  merge_content_delta: string | null;
}

export interface ObservationApplyResponse {
  observation_id: number;
  updated: number;
  created: number;
  merged: number;
  created_neuron_ids: number[];
}

// ── Integrity System ──────────────────────────────────────────────

export interface IntegrityScanSummary {
  id: number;
  scan_type: string;
  scope: string;
  status: string;
  findings_count: number;
  initiated_by: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface IntegrityFinding {
  id: number;
  scan_id: number;
  finding_type: string;
  severity: string;
  priority_score: number;
  description: string;
  status: string;
  resolution: string | null;
  proposal_id: number | null;
  resolved_by: string | null;
  resolved_at: string | null;
  neuron_ids: number[];
  created_at: string | null;
}

export interface IntegrityNeuronSnapshot {
  id: number;
  label: string;
  department: string | null;
  layer: number;
  content: string | null;
  summary: string | null;
  source_type: string | null;
  source_origin: string | null;
  invocations: number;
}

export interface IntegrityFindingDetail extends IntegrityFinding {
  detail: Record<string, unknown>;
  edge_ids: number[][];
  neurons: Record<string, IntegrityNeuronSnapshot>;
  created_by_agent_run_id?: number | null;
  created_by_agent_name?: string | null;
}

export interface IntegrityScanDetail extends IntegrityScanSummary {
  parameters: Record<string, unknown>;
  findings: IntegrityFinding[];
}

export interface IntegrityWeightDistribution {
  count: number;
  mean: number;
  median: number;
  std: number;
  p10: number;
  p25: number;
  p75: number;
  p90: number;
  max: number;
}

export interface IntegrityScanResponse extends IntegrityScanSummary {
  [key: string]: unknown;
}

export interface IntegrityDashboard {
  open_findings_total: number;
  open_by_type: Record<string, number>;
  open_by_severity: Record<string, number>;
  recent_scans: IntegrityScanSummary[];
}

export interface IntegrityApplyResult {
  proposal_id: number;
  state: string;
  item_count: number;
}

export interface IntegrityBulkResolveResult {
  resolved: Array<{ id: number; status: string }>;
}

export interface IntegrityProposeResult {
  proposal_id: number;
  state: string;
  item_count: number;
  gap_source: string;
}
