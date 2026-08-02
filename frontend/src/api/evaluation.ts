// Evaluation capability adapter — eval runs and labs.
//
// Route ownership is the generated table in ../contracts/capabilities.ts;
// functions here call only routes that table assigns to 'evaluation'.

import { json } from './http';

export interface OracleFunnelStage {
  count: number;
  pct: number;
}

export interface OracleFunnelLedger {
  n_funneled: number;
  stages: Record<string, OracleFunnelStage>;
  per_category: Record<string, Record<string, number>>;
  headline: string;
}

export interface OracleFunnelArtifact {
  available: boolean;
  artifact_root: string;
  artifact_path?: string;
  run?: string;
  condition?: string;
  modified_at?: number;
  ledger?: OracleFunnelLedger;
  sample_rows?: Array<Record<string, unknown>>;
  row_count?: number;
  note?: string;
}

export function fetchLatestOracleFunnel(): Promise<OracleFunnelArtifact> {
  return json<OracleFunnelArtifact>('/admin/labs/oracle-funnel');
}

// ── Pattern #3: immutable eval runs ──
// Kept handwritten: the generated schema under-specifies this shape
// (dict-typed fields on the backend model). See record 05 measured addendum.
export interface EvalRunSummary {
  id: number;
  suite_name: string;
  suite_hash: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  started_by: string;
  tenant_id: string;
  summary: {
    total: number;
    blocked: number;
    errors: number;
    violation_count: number;
    severity_counts: Record<string, number>;
    pass_rate: number;
  } | null;
  is_certified: boolean;
}

export interface EvalRunCase {
  id: number;
  case_label: string;
  query_text: string;
  query_id: number | null;
  lineage_id: number | null;
  blocked: boolean;
  response_text: string | null;
  violations: Array<Record<string, unknown>>;
  scores: Record<string, unknown> | null;
  error_message: string | null;
}

// Kept handwritten: the generated schema under-specifies this shape
// (dict-typed fields on the backend model). See record 05 measured addendum.
export interface EvalRunDetail extends EvalRunSummary {
  model_versions: Record<string, unknown>;
  scoring_engine_version: string;
  overrides_snapshot: Record<string, unknown> | null;
  cases: EvalRunCase[];
}

export function listEvalRuns(limit = 50): Promise<EvalRunSummary[]> {
  return json<EvalRunSummary[]>(`/admin/eval/runs?limit=${limit}`);
}

export function getEvalRun(id: number): Promise<EvalRunDetail> {
  return json<EvalRunDetail>(`/admin/eval/runs/${id}`);
}

export function startEvalRun(suiteName: string): Promise<EvalRunSummary> {
  return json<EvalRunSummary>('/admin/eval/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ suite_name: suiteName }),
  });
}

export function certifyEvalRun(id: number): Promise<{
  eval_run_id: number; certified_at: string; certified_by: string;
}> {
  return json(`/admin/eval/runs/${id}/certify`, { method: 'POST' });
}
