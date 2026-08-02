// Governance capability adapter — proposals, provenance, integrity, agents.
//
// Route ownership is the generated table in ../contracts/capabilities.ts;
// functions here call only routes that table assigns to 'governance'.

import type { components } from '../contracts/schema';
import type {
  IntegrityDashboard,
  IntegrityScanSummary,
  IntegrityScanDetail,
  IntegrityScanResponse,
  IntegrityFinding,
  IntegrityFindingDetail,
  IntegrityApplyResult,
  IntegrityBulkResolveResult,
  IntegrityProposeResult,
} from '../types';
import { json } from './http';

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
  origin: string; // "autopilot" | "integrity" | "document" | "emergent" | "manual"
  is_autopilot: boolean;
  created_at: string | null;
  // Server-extracted source identifiers for reverse deep-linking back
  // to the upstream producer. Populated only when the origin carries a
  // retrievable id (currently integrity → finding_id + scan_id).
  finding_id: number | null;
  scan_id: number | null;
}

export interface GapEvidence {
  signal: string;
  description: string;
  metric_value: number;
  threshold: number;
  neuron_ids: number[];
  query_ids: number[];
  // Reconsolidation-auditor extension (signal === 'reconsolidation_quality')
  disposition?: string | null;
  confidence?: number | null;
  defect_classes?: string[] | null;
  evidence_citations?: string[] | null;
  risk_breakdown?: Record<string, { score: number; detail: string }> | null;
  blast_radius?: string | null;
  uncertainty?: string | null;
  heightened_review?: boolean | null;
  evidence_hash?: string | null;
}

export interface DocumentEvidence {
  source: string;
  document: string;
  section: string;
  section_id: string;
  job_id: string;
}

// Server-rendered review projection for reconsolidate items
// (backend services/reconsolidation/render.py). Field shapes are owned
// by the backend; the card renders what it is given.
export interface RenderedFusionMember {
  neuron_id: number;
  label: string | null;
  summary: string | null;
  content: string | null;
  node_type: string | null;
  scope: string | null;
  authority_level: string | null;
  invocations: number;
  avg_utility: number;
  is_active: boolean;
  facet_evidence_count: number;
  content_hash: string;
  status: 'fresh' | 'content-drifted' | 'state-drifted' | 'superseded' | 'missing';
  status_notes: string[];
  outcome: 'retain' | 'retire' | 'unchanged';
}

export interface RenderedFieldReceipt {
  field: string;
  before: { neuron_id: number; value: string | number | null }[];
  after: string | number | null;
  rule: string;
  why: string;
  rejected?: Record<string, number>;
  flags?: string[];
}

export interface RenderedFusionPlan {
  kind: 'reconsolidate';
  error?: string;
  render_version?: number;
  plan_hash?: string;
  member_state_hash?: string;
  disposition?: 'retain-canonical' | 'synthesize-new' | 'abstain';
  coverage_delta?: boolean;
  canonical_neuron_id?: number | null;
  proposed_node_type?: string | null;
  freshness?: {
    verdict: 'fresh' | 'stale';
    violations: string[];
    dead_targets: { neuron_id: number; superseded_by: number | null; note: string }[];
  };
  members?: RenderedFusionMember[];
  fields?: RenderedFieldReceipt[];
  facets?: { kind: string; text: string; evidence_member_ids: number[]; resolution: string | null }[];
  rewiring?: {
    internal_conducting_deleted: number;
    member_peer_retired: number;
    synthesis_peer_created: number;
    provenance_links_created: number;
    why: string;
    peers: { peer_id: number; union_cofire_queries: number; recomputed_weight: number; edge_type: string; weight_provenance: string }[];
    inactive_peers_dropped: number[];
  } | null;
  validators?: {
    preflight_passed: boolean;
    preflight_violations: string[];
    postconditions_asserted_at_apply: string[];
  };
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
  rendered_plan: RenderedFusionPlan | null;
}

// Kept handwritten: the generated schema under-specifies this shape
// (dict-typed fields on the backend model). See record 05 measured addendum.
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

export type ProposalStats = components['schemas']['ProposalStatsOut'];

export interface DedupCluster {
  proposal_ids: number[];
  size: number;
  representative: string;
}

export interface DedupClustersOut {
  clusters: DedupCluster[];
  scanned: number;
  threshold: number;
}

// Semantic near-duplicate clusters over pending proposals (embedding cosine,
// computed server-side at $0). Advisory grouping data for the queue UI.
export function fetchDedupClusters(state = 'proposed'): Promise<DedupClustersOut> {
  return json<DedupClustersOut>(`/admin/proposals/dedup-clusters?state=${state}`);
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

// ── AIP Phase 4 Pattern #208: agents surface ──
export type AgentSummary = components['schemas']['AgentSummaryOut'];

export type AgentDetail = components['schemas']['AgentDetailOut'];

export type AgentRun = components['schemas']['AgentRunOut'];

// Kept handwritten: the generated schema under-specifies this shape
// (dict-typed fields on the backend model). See record 05 measured addendum.
export interface AgentRunDetail extends AgentRun {
  child_actions: Array<{
    action_id: number;
    kind: string;
    tool: string;
    input: Record<string, unknown>;
    reason: string | null;
    state: string;
    result: Record<string, unknown> | null;
    error: string | null;
    applied_at: string | null;
  }>;
}

export function listAgents(): Promise<AgentSummary[]> {
  return json<AgentSummary[]>('/v1/agents');
}

export function getAgent(name: string): Promise<AgentDetail> {
  return json<AgentDetail>(`/v1/agents/${encodeURIComponent(name)}`);
}

export function listAgentRuns(agentName?: string, limit = 50): Promise<AgentRun[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (agentName) params.set('agent_name', agentName);
  return json<AgentRun[]>(`/v1/agents/runs?${params.toString()}`);
}

export function getAgentRun(actionId: number): Promise<AgentRunDetail> {
  return json<AgentRunDetail>(`/v1/agents/runs/${actionId}`);
}

export function triggerAgentRun(
  name: string,
  inputContext?: Record<string, unknown>,
): Promise<{
  action_id: number;
  agent_name: string;
  summary: string;
  turns: number;
  tool_calls: number;
  mutations: number;
  errors: number;
}> {
  return json(`/v1/agents/${encodeURIComponent(name)}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input_context: inputContext || {} }),
  });
}

// Kept handwritten: the generated schema under-specifies this shape
// (dict-typed fields on the backend model). See record 05 measured addendum.
export interface IntegrityRun {
  id: number;
  kind: string;                 // "scan:<scan_type>" | "agent:<agent_name>"
  started_at: string | null;
  completed_at: string | null;
  initiated_by: string;
  state: string;
  summary: string | null;
  scan_scope: string | null;
  findings_count: number | null;
  turns: number | null;
  tool_calls: number | null;
  mutations: number | null;
  errors: number | null;
}

export function listIntegrityRuns(
  limit: number = 50,
  kindFilter?: 'scan' | 'agent',
): Promise<IntegrityRun[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (kindFilter) params.set('kind_filter', kindFilter);
  return json<IntegrityRun[]>(`/admin/integrity/runs?${params.toString()}`);
}
