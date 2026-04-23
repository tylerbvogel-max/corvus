import { useState } from 'react';
import { fetchPerformance } from '../api';

interface TokenPair { input: number; output: number }
interface CostSummary {
  total_queries: number; total_cost: number; avg_cost: number; min_cost: number; max_cost: number;
  classify_tokens: TokenPair; execute_tokens: TokenPair; eval_tokens: TokenPair;
  total_input_tokens: number; total_output_tokens: number;
}
interface CostModeling {
  avg_tokens: { classify_input: number; classify_output: number; execute_input: number; execute_output: number };
  per_query_cost: { haiku_neuron: number; sonnet_raw: number; opus_raw: number };
  savings_vs_opus: number; savings_vs_sonnet: number;
  projected_monthly_100k: { haiku_neuron: number; sonnet_raw: number; opus_raw: number };
  annual_savings_vs_opus_100k: number; annual_savings_vs_sonnet_100k: number;
}
interface QualityMode {
  mode: string; n: number; accuracy: number; completeness: number; clarity: number; faithfulness: number; overall: number;
}
interface ScoreDist { score: number; count: number }
interface Reliability { distribution: ScoreDist[]; total_evaluated: number; score_4_plus: number; reliability_pct: number }
interface TrendPeriod { queries: number; overall: number; accuracy: number; completeness: number }
interface NeuronStats {
  active_neurons: number; distinct_fired: number; never_fired: number;
  utilization: Record<string, number>; layer_distribution: Record<string, number>; department_distribution: Record<string, number>;
}
interface RefinementImpact { neurons_created: number; neurons_updated: number; graph_growth: Record<string, number> }
interface AutopilotRow { status: string; runs: number; avg_score: number; created: number; updated: number; cost: number }
interface Investment { query_pipeline: number; autopilot: number; total: number }
interface NeuronQualityCorr { bucket: string; queries: number; avg_score: number }
interface QueryTimelinePoint { id: number; cost: number; neurons: number; score: number | null; created_at: string | null }

interface GroupStats { label: string; n: number; mean: number; std?: number }
interface BinomialResult { p: number; significant: boolean; claim: string }
interface StatTest {
  id: string; title: string; description: string;
  group_a?: GroupStats; group_b?: GroupStats;
  welch_t?: number; welch_p?: number; welch_p_adj?: number;
  mann_whitney_u?: number; mann_whitney_p?: number; mann_whitney_p_adj?: number;
  cohens_d?: number; effect_size?: string;
  mean_diff?: number; ci_95?: [number, number];
  n_needed_80pct_power?: number; adequately_powered?: boolean;
  significant_welch?: boolean; significant_mw?: boolean;
  significant_welch_fdr?: boolean; significant_mw_fdr?: boolean;
  one_sided?: boolean; warning?: string | null;
  // reliability-specific
  n_total?: number; n_good?: number; observed_rate?: number;
  wilson_ci_95?: [number, number];
  binomial_75?: BinomialResult & { p_adj?: number; significant_fdr?: boolean };
  binomial_70?: BinomialResult & { p_adj?: number; significant_fdr?: boolean };
}

interface FdrCorrection { method: string; total_tests: number; alpha: number; description: string }
interface CostBucket {
  key: string; label: string; total_cost: number;
  source_table: string; description: string;
}
interface CostByActivity {
  buckets: CostBucket[];
  total_tracked: number;
  gap_note: string;
}

interface PerfData {
  stat_tests: StatTest[];
  fdr_correction: FdrCorrection | null;
  cost_summary: CostSummary; cost_modeling: CostModeling; quality_by_mode: QualityMode[];
  cost_by_activity?: CostByActivity;
  quality_ratio: number | null; reliability: Reliability;
  quality_trend: Record<string, TrendPeriod>; neuron_stats: NeuronStats;
  refinement_impact: RefinementImpact; autopilot: AutopilotRow[]; investment: Investment;
  neuron_quality_correlation: NeuronQualityCorr[]; query_timeline: QueryTimelinePoint[];
}

const fmt = (n: number, d = 2) => n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtK = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(0)}K` : `${n}`;
const pct = (n: number) => `${n.toFixed(1)}%`;

function ModeLabel({ mode }: { mode: string }) {
  const colors: Record<string, string> = {
    opus_neuron: '#a78bfa', sonnet_neuron: '#60a5fa', haiku_neuron: '#22c55e',
    opus_raw: '#f472b6', sonnet_raw: '#fb923c', haiku_raw: '#94a3b8',
  };
  return <span style={{ color: colors[mode] || 'var(--text)', fontWeight: 600 }}>{mode.replace('_', ' + ')}</span>;
}

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const w = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return <div style={{ background: color, height: 18, width: `${w}%`, borderRadius: 3, minWidth: 2 }} />;
}

export default function PerformancePage() {
  const [data, setData] = useState<PerfData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<string | null>(null);

  const runAnalysis = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchPerformance();
      setData(d);
      setLastRun(new Date().toLocaleTimeString());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="perf-page">
      <div className="perf-header">
        <div>
          <h2>Performance Analysis</h2>
          <p className="perf-subtitle">
            Pure SQL analytics — no LLM invocation. Computed from query logs, eval scores, and refinement history.
          </p>
        </div>
        <div className="perf-run-area">
          <button className="perf-run-btn" onClick={runAnalysis} disabled={loading}>
            {loading ? 'Computing...' : 'Run Analysis'}
          </button>
          {lastRun && <span className="perf-last-run">Last run: {lastRun}</span>}
        </div>
      </div>

      {error && <div className="perf-error">{error}</div>}

      {!data && !loading && (
        <div className="perf-empty">Click "Run Analysis" to compute performance metrics from the database.</div>
      )}

      {data && <>
        {/* Hero metrics */}
        <div className="perf-hero">
          <div className="perf-hero-card" style={{ borderTopColor: '#22c55e' }}>
            <span className="perf-hero-value" style={{ color: '#22c55e' }}>{pct(data.cost_modeling.savings_vs_opus)}</span>
            <span className="perf-hero-label">Cost savings vs Opus</span>
          </div>
          <div className="perf-hero-card" style={{ borderTopColor: '#38bdf8' }}>
            <span className="perf-hero-value" style={{ color: '#38bdf8' }}>{data.quality_ratio ?? '—'}%</span>
            <span className="perf-hero-label">Quality vs Opus Raw</span>
          </div>
          <div className="perf-hero-card" style={{ borderTopColor: '#facc15' }}>
            <span className="perf-hero-value" style={{ color: '#facc15' }}>{data.reliability.reliability_pct}%</span>
            <span className="perf-hero-label">Reliability (score 4+)</span>
          </div>
          <div className="perf-hero-card" style={{ borderTopColor: '#a78bfa' }}>
            <span className="perf-hero-value" style={{ color: '#a78bfa' }}>{data.cost_summary.total_queries}</span>
            <span className="perf-hero-label">Total queries</span>
          </div>
        </div>

        {/* Statistical Significance */}
        {data.stat_tests.length > 0 && (
          <section className="perf-section">
            <h3>Statistical Significance</h3>
            <p className="perf-section-desc">
              Hypothesis tests validating whether observed differences are statistically real or could be noise.
              Tests use &alpha;=0.05. Mann-Whitney U is preferred for ordinal (1&ndash;5) scale data.
              {data.fdr_correction && <> All p-values are corrected for multiple comparisons using <strong>{data.fdr_correction.method}</strong> FDR
              ({data.fdr_correction.total_tests} tests). Significance badges reflect adjusted p-values.</>}
            </p>

            <div className="stat-tests">
              {data.stat_tests.map(t => {
                const isSigRaw = t.significant_mw ?? t.significant_welch ?? (t.binomial_70?.significant);
                const isSigFdr = t.significant_mw_fdr ?? t.significant_welch_fdr ?? (t.binomial_70?.significant_fdr);
                const isSig = isSigFdr ?? isSigRaw;
                return (
                  <div key={t.id} className={`stat-test-card ${isSig ? 'stat-sig' : 'stat-ns'}`}>
                    <div className="stat-test-header">
                      <strong>{t.title}</strong>
                      <span className={`stat-badge ${isSig ? 'stat-badge-sig' : 'stat-badge-ns'}`}>
                        {isSig ? 'Significant (FDR)' : 'Not Significant (FDR)'}
                      </span>
                    </div>
                    <p className="stat-test-desc">{t.description}</p>

                    {t.warning && <p className="stat-test-warn">{t.warning}</p>}

                    {/* Group comparison tests */}
                    {t.group_a && t.group_b && (
                      <div className="stat-test-groups">
                        <div className="stat-test-group">
                          <span className="stat-group-label">{t.group_a.label}</span>
                          <span>n={t.group_a.n}, mean={t.group_a.mean}{t.group_a.std !== undefined ? `, std=${t.group_a.std}` : ''}</span>
                        </div>
                        <div className="stat-test-group">
                          <span className="stat-group-label">{t.group_b.label}</span>
                          <span>n={t.group_b.n}, mean={t.group_b.mean}{t.group_b.std !== undefined ? `, std=${t.group_b.std}` : ''}</span>
                        </div>
                      </div>
                    )}

                    <div className="stat-test-results">
                      {t.mann_whitney_p !== undefined && (
                        <div className="stat-result-row">
                          <span className="stat-result-label">Mann-Whitney U{t.one_sided ? ' (one-sided)' : ''}</span>
                          <span>U={t.mann_whitney_u}</span>
                          <span className={t.significant_mw ? 'stat-p-sig' : 'stat-p-ns'}>
                            p={t.mann_whitney_p < 0.000001 ? '<0.000001' : t.mann_whitney_p.toFixed(6)}
                          </span>
                          {t.mann_whitney_p_adj !== undefined && (
                            <span className={t.significant_mw_fdr ? 'stat-p-sig' : 'stat-p-ns'}>
                              p<sub>adj</sub>={t.mann_whitney_p_adj.toFixed(6)}
                            </span>
                          )}
                        </div>
                      )}
                      {t.welch_p !== undefined && (
                        <div className="stat-result-row">
                          <span className="stat-result-label">Welch's t-test</span>
                          <span>t={t.welch_t}</span>
                          <span className={t.significant_welch ? 'stat-p-sig' : 'stat-p-ns'}>
                            p={t.welch_p.toFixed(6)}
                          </span>
                          {t.welch_p_adj !== undefined && (
                            <span className={t.significant_welch_fdr ? 'stat-p-sig' : 'stat-p-ns'}>
                              p<sub>adj</sub>={t.welch_p_adj.toFixed(6)}
                            </span>
                          )}
                        </div>
                      )}
                      {t.cohens_d !== undefined && (
                        <div className="stat-result-row">
                          <span className="stat-result-label">Effect size</span>
                          <span>Cohen's d={t.cohens_d}</span>
                          <span className="stat-effect">{t.effect_size}</span>
                        </div>
                      )}
                      {t.ci_95 && (
                        <div className="stat-result-row">
                          <span className="stat-result-label">Mean diff</span>
                          <span>{t.mean_diff}</span>
                          <span>95% CI: [{t.ci_95[0]}, {t.ci_95[1]}]</span>
                        </div>
                      )}
                      {t.n_needed_80pct_power !== undefined && (
                        <div className="stat-result-row">
                          <span className="stat-result-label">Power (80%)</span>
                          <span>Need n={t.n_needed_80pct_power}/group</span>
                          <span className={t.adequately_powered ? 'stat-p-sig' : 'stat-p-ns'}>
                            {t.adequately_powered ? 'Adequately powered' : 'Underpowered'}
                          </span>
                        </div>
                      )}

                      {/* Reliability-specific */}
                      {t.observed_rate !== undefined && (
                        <>
                          <div className="stat-result-row">
                            <span className="stat-result-label">Observed</span>
                            <span>{t.n_good}/{t.n_total} = {t.observed_rate}%</span>
                            <span>Wilson 95% CI: [{t.wilson_ci_95?.[0]}%, {t.wilson_ci_95?.[1]}%]</span>
                          </div>
                          {t.binomial_75 && (
                            <div className="stat-result-row">
                              <span className="stat-result-label">H0: rate &le; 75%</span>
                              <span className={t.binomial_75.significant ? 'stat-p-sig' : 'stat-p-ns'}>
                                p={t.binomial_75.p.toFixed(6)}
                              </span>
                              <span>{t.binomial_75.claim}</span>
                            </div>
                          )}
                          {t.binomial_70 && (
                            <div className="stat-result-row">
                              <span className="stat-result-label">H0: rate &le; 70%</span>
                              <span className={t.binomial_70.significant ? 'stat-p-sig' : 'stat-p-ns'}>
                                p={t.binomial_70.p.toFixed(6)}
                              </span>
                              <span>{t.binomial_70.claim}</span>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Cost by Activity — v1 attribution breakdown */}
        {data.cost_by_activity && (
          <section className="perf-section">
            <h3>Cost by activity</h3>
            <p className="perf-section-desc">
              Where Corvus spends LLM budget across tracked activities.
              Sum of {data.cost_by_activity.buckets.length} buckets: ${fmt(data.cost_by_activity.total_tracked, 2)}.
            </p>
            <div className="perf-cost-grid">
              {data.cost_by_activity.buckets.map(b => {
                const total = data.cost_by_activity!.total_tracked || 1;
                const share = (b.total_cost / total) * 100;
                return (
                  <div key={b.key} className="perf-cost-card" title={`${b.description}\nSource: ${b.source_table}`}>
                    <div className="perf-cost-mode">{b.label}</div>
                    <div className="perf-cost-amount" style={{ color: b.key === 'autopilot' ? '#a78bfa' : '#22c55e' }}>${fmt(b.total_cost, 2)}</div>
                    <div className="perf-cost-label">{share.toFixed(1)}% of tracked spend</div>
                  </div>
                );
              })}
            </div>
            <p className="perf-section-desc" style={{ marginTop: 10, fontStyle: 'italic', opacity: 0.8 }}>
              {data.cost_by_activity.gap_note}
            </p>
          </section>
        )}

        {/* Cost Modeling */}
        <section className="perf-section">
          <h3>Cost Modeling</h3>
          <p className="perf-section-desc">Per-query cost comparison: Haiku + Neurons vs raw alternatives.</p>
          <div className="perf-cost-grid">
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Haiku + Neurons</div>
              <div className="perf-cost-amount" style={{ color: '#22c55e' }}>${fmt(data.cost_modeling.per_query_cost.haiku_neuron, 4)}</div>
              <div className="perf-cost-label">per query</div>
            </div>
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Sonnet Raw</div>
              <div className="perf-cost-amount" style={{ color: '#fb923c' }}>${fmt(data.cost_modeling.per_query_cost.sonnet_raw, 4)}</div>
              <div className="perf-cost-label">per query</div>
            </div>
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Opus Raw</div>
              <div className="perf-cost-amount" style={{ color: '#f472b6' }}>${fmt(data.cost_modeling.per_query_cost.opus_raw, 4)}</div>
              <div className="perf-cost-label">per query</div>
            </div>
          </div>

          <h4>Projected at 100,000 queries/month</h4>
          <table className="perf-table">
            <thead><tr><th>Approach</th><th>Monthly</th><th>Annual</th><th>Annual Savings</th></tr></thead>
            <tbody>
              <tr>
                <td><ModeLabel mode="haiku_neuron" /></td>
                <td>${fmt(data.cost_modeling.projected_monthly_100k.haiku_neuron)}</td>
                <td>${fmt(data.cost_modeling.projected_monthly_100k.haiku_neuron * 12)}</td>
                <td>—</td>
              </tr>
              <tr>
                <td><ModeLabel mode="sonnet_raw" /></td>
                <td>${fmt(data.cost_modeling.projected_monthly_100k.sonnet_raw)}</td>
                <td>${fmt(data.cost_modeling.projected_monthly_100k.sonnet_raw * 12)}</td>
                <td style={{ color: '#22c55e' }}>${fmt(data.cost_modeling.annual_savings_vs_sonnet_100k)}</td>
              </tr>
              <tr>
                <td><ModeLabel mode="opus_raw" /></td>
                <td>${fmt(data.cost_modeling.projected_monthly_100k.opus_raw)}</td>
                <td>${fmt(data.cost_modeling.projected_monthly_100k.opus_raw * 12)}</td>
                <td style={{ color: '#22c55e' }}>${fmt(data.cost_modeling.annual_savings_vs_opus_100k)}</td>
              </tr>
            </tbody>
          </table>
        </section>

        {/* Quality by Mode */}
        <section className="perf-section">
          <h3>Quality by Answer Mode</h3>
          <p className="perf-section-desc">Blind evaluation scores across all tested configurations.</p>
          <table className="perf-table">
            <thead><tr><th>Mode</th><th>N</th><th>Accuracy</th><th>Complete</th><th>Clarity</th><th>Faith</th><th>Overall</th></tr></thead>
            <tbody>
              {data.quality_by_mode.map(q => (
                <tr key={q.mode}>
                  <td><ModeLabel mode={q.mode} /></td>
                  <td>{q.n}</td>
                  <td>{q.accuracy}</td>
                  <td>{q.completeness}</td>
                  <td>{q.clarity}</td>
                  <td>{q.faithfulness}</td>
                  <td style={{ fontWeight: 700 }}>{q.overall}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {/* Reliability */}
        <section className="perf-section">
          <h3>Reliability Distribution</h3>
          <p className="perf-section-desc">
            Score distribution for Haiku + Neurons ({data.reliability.total_evaluated} evaluations).
            {' '}<strong>{data.reliability.reliability_pct}%</strong> score 4 or above.
          </p>
          <div className="perf-dist">
            {data.reliability.distribution.map(d => {
              const maxCount = Math.max(...data.reliability.distribution.map(x => x.count));
              const colors: Record<number, string> = { 1: '#ef4444', 2: '#fb923c', 3: '#facc15', 4: '#22c55e', 5: '#38bdf8' };
              return (
                <div key={d.score} className="perf-dist-row">
                  <span className="perf-dist-label">Score {d.score}</span>
                  <div className="perf-dist-bar-wrap">
                    <Bar value={d.count} max={maxCount} color={colors[d.score] || '#94a3b8'} />
                  </div>
                  <span className="perf-dist-count">{d.count} ({pct(d.count / data.reliability.total_evaluated * 100)})</span>
                </div>
              );
            })}
          </div>
        </section>

        {/* Quality Trend */}
        <section className="perf-section">
          <h3>Quality Trend</h3>
          <p className="perf-section-desc">Haiku + Neuron quality improvement as the graph grows.</p>
          {data.quality_trend.early && data.quality_trend.late && (
            <table className="perf-table">
              <thead><tr><th>Period</th><th>Queries</th><th>Overall</th><th>Accuracy</th><th>Completeness</th></tr></thead>
              <tbody>
                <tr>
                  <td>Early (1st half)</td>
                  <td>{data.quality_trend.early.queries}</td>
                  <td>{data.quality_trend.early.overall}</td>
                  <td>{data.quality_trend.early.accuracy}</td>
                  <td>{data.quality_trend.early.completeness}</td>
                </tr>
                <tr>
                  <td>Late (2nd half)</td>
                  <td>{data.quality_trend.late.queries}</td>
                  <td style={{ color: data.quality_trend.late.overall > data.quality_trend.early.overall ? '#22c55e' : '#ef4444', fontWeight: 700 }}>
                    {data.quality_trend.late.overall}
                  </td>
                  <td>{data.quality_trend.late.accuracy}</td>
                  <td>{data.quality_trend.late.completeness}</td>
                </tr>
                <tr>
                  <td style={{ fontStyle: 'italic', color: 'var(--text-dim)' }}>Change</td>
                  <td />
                  <td style={{ color: '#22c55e' }}>
                    +{pct((data.quality_trend.late.overall - data.quality_trend.early.overall) / data.quality_trend.early.overall * 100)}
                  </td>
                  <td />
                  <td />
                </tr>
              </tbody>
            </table>
          )}
        </section>

        {/* Neuron Count vs Quality */}
        <section className="perf-section">
          <h3>Neuron Count vs Quality</h3>
          <p className="perf-section-desc">Does selecting more neurons improve answer quality?</p>
          <table className="perf-table">
            <thead><tr><th>Neurons Selected</th><th>Queries</th><th>Avg Overall</th></tr></thead>
            <tbody>
              {data.neuron_quality_correlation.map(c => (
                <tr key={c.bucket}>
                  <td>{c.bucket}</td>
                  <td>{c.queries}</td>
                  <td style={{ fontWeight: 700 }}>{c.avg_score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {/* Investment */}
        <section className="perf-section">
          <h3>Total Investment</h3>
          <div className="perf-cost-grid">
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Query Pipeline</div>
              <div className="perf-cost-amount">${fmt(data.investment.query_pipeline)}</div>
              <div className="perf-cost-label">{data.cost_summary.total_queries} queries</div>
            </div>
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Autopilot</div>
              <div className="perf-cost-amount">${fmt(data.investment.autopilot)}</div>
              <div className="perf-cost-label">{data.autopilot.reduce((s, a) => s + a.runs, 0)} runs</div>
            </div>
            <div className="perf-cost-card" style={{ borderColor: 'var(--accent)' }}>
              <div className="perf-cost-mode">Total Spend</div>
              <div className="perf-cost-amount" style={{ color: 'var(--accent)' }}>${fmt(data.investment.total)}</div>
              <div className="perf-cost-label">{data.neuron_stats.active_neurons} neurons built</div>
            </div>
          </div>
        </section>

        {/* Token Usage */}
        <section className="perf-section">
          <h3>Token Usage</h3>
          <table className="perf-table">
            <thead><tr><th>Stage</th><th>Input Tokens</th><th>Output Tokens</th><th>Total</th></tr></thead>
            <tbody>
              <tr>
                <td>Classification (Stage 1)</td>
                <td>{fmtK(data.cost_summary.classify_tokens.input)}</td>
                <td>{fmtK(data.cost_summary.classify_tokens.output)}</td>
                <td>{fmtK(data.cost_summary.classify_tokens.input + data.cost_summary.classify_tokens.output)}</td>
              </tr>
              <tr>
                <td>Execution (Stage 2)</td>
                <td>{fmtK(data.cost_summary.execute_tokens.input)}</td>
                <td>{fmtK(data.cost_summary.execute_tokens.output)}</td>
                <td>{fmtK(data.cost_summary.execute_tokens.input + data.cost_summary.execute_tokens.output)}</td>
              </tr>
              <tr>
                <td>Evaluation</td>
                <td>{fmtK(data.cost_summary.eval_tokens.input)}</td>
                <td>{fmtK(data.cost_summary.eval_tokens.output)}</td>
                <td>{fmtK(data.cost_summary.eval_tokens.input + data.cost_summary.eval_tokens.output)}</td>
              </tr>
              <tr style={{ fontWeight: 700 }}>
                <td>Total</td>
                <td>{fmtK(data.cost_summary.total_input_tokens)}</td>
                <td>{fmtK(data.cost_summary.total_output_tokens)}</td>
                <td>{fmtK(data.cost_summary.total_input_tokens + data.cost_summary.total_output_tokens)}</td>
              </tr>
            </tbody>
          </table>
        </section>

        {/* Neuron Graph Stats */}
        <section className="perf-section">
          <h3>Neuron Graph</h3>
          <div className="perf-neuron-grid">
            <div>
              <h4>Utilization</h4>
              <table className="perf-table perf-table-compact">
                <tbody>
                  <tr><td>Active neurons</td><td>{data.neuron_stats.active_neurons.toLocaleString()}</td></tr>
                  <tr><td>Distinct fired</td><td>{data.neuron_stats.distinct_fired}</td></tr>
                  <tr><td>Never fired</td><td>{data.neuron_stats.never_fired} ({pct(data.neuron_stats.never_fired / data.neuron_stats.active_neurons * 100)})</td></tr>
                  {Object.entries(data.neuron_stats.utilization).map(([k, v]) => (
                    <tr key={k}><td>{k.replace(/_/g, ' ')}</td><td>{v}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h4>Layer Distribution</h4>
              <table className="perf-table perf-table-compact">
                <tbody>
                  {Object.entries(data.neuron_stats.layer_distribution).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td>{v}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h4>Department Distribution</h4>
              <table className="perf-table perf-table-compact">
                <tbody>
                  {Object.entries(data.neuron_stats.department_distribution).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td>{v}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* Refinement Impact */}
        <section className="perf-section">
          <h3>Refinement Impact</h3>
          <div className="perf-cost-grid">
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Neurons Created</div>
              <div className="perf-cost-amount" style={{ color: '#22c55e' }}>{data.refinement_impact.neurons_created.toLocaleString()}</div>
              <div className="perf-cost-label">via refinement</div>
            </div>
            <div className="perf-cost-card">
              <div className="perf-cost-mode">Neurons Updated</div>
              <div className="perf-cost-amount" style={{ color: '#38bdf8' }}>{data.refinement_impact.neurons_updated}</div>
              <div className="perf-cost-label">via refinement</div>
            </div>
          </div>
          <h4>Graph Growth by Query Phase</h4>
          <div className="perf-growth-bars">
            {Object.entries(data.refinement_impact.graph_growth).map(([phase, count]) => {
              const maxG = Math.max(...Object.values(data.refinement_impact.graph_growth));
              return (
                <div key={phase} className="perf-dist-row">
                  <span className="perf-dist-label" style={{ width: 80 }}>{phase.replace(/_/g, ' ')}</span>
                  <div className="perf-dist-bar-wrap">
                    <Bar value={count} max={maxG} color="#60a5fa" />
                  </div>
                  <span className="perf-dist-count">{count}</span>
                </div>
              );
            })}
          </div>
        </section>

        {/* Autopilot */}
        {data.autopilot.length > 0 && (
          <section className="perf-section">
            <h3>Autopilot Runs</h3>
            <table className="perf-table">
              <thead><tr><th>Status</th><th>Runs</th><th>Avg Score</th><th>Created</th><th>Updated</th><th>Cost</th></tr></thead>
              <tbody>
                {data.autopilot.map(a => (
                  <tr key={a.status}>
                    <td style={{ color: a.status === 'completed' ? '#22c55e' : '#fb923c' }}>{a.status}</td>
                    <td>{a.runs}</td>
                    <td>{a.avg_score}</td>
                    <td>{a.created}</td>
                    <td>{a.updated}</td>
                    <td>${fmt(a.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* Query Timeline */}
        <section className="perf-section">
          <h3>Query Timeline</h3>
          <p className="perf-section-desc">Per-query cost and quality score over time.</p>
          <div className="perf-timeline">
            <div className="perf-timeline-header">
              <span style={{ width: 40 }}>QID</span>
              <span style={{ width: 70 }}>Cost</span>
              <span style={{ width: 60 }}>Neurons</span>
              <span style={{ flex: 1 }}>Score</span>
            </div>
            <div className="perf-timeline-body">
              {data.query_timeline.map(q => (
                <div key={q.id} className="perf-timeline-row">
                  <span style={{ width: 40, color: 'var(--text-dim)' }}>#{q.id}</span>
                  <span style={{ width: 70 }}>${fmt(q.cost, 4)}</span>
                  <span style={{ width: 60 }}>{q.neurons}</span>
                  <span style={{ flex: 1 }}>
                    {q.score !== null ? (
                      <span className="perf-score-pip" style={{
                        background: q.score >= 4 ? '#22c55e' : q.score >= 3 ? '#facc15' : '#ef4444',
                        width: `${q.score / 5 * 100}%`,
                      }}>{q.score}</span>
                    ) : (
                      <span style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>—</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>
      </>}
    </div>
  );
}
