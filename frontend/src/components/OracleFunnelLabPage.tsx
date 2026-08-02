import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchLatestOracleFunnel, type OracleFunnelArtifact } from '../api/evaluation';
import CarlosLabFrame, { LAB_CYAN, LAB_MAGENTA } from './CarlosLabFrame';

const STAGES = [
  ['ingest', 'No neuron encoded the gold fact'],
  ['candidate', 'Gold neuron missed the wide top-100 pool'],
  ['rank', 'Candidate existed but fell below production top-K'],
  ['assembly', 'Ranked evidence did not enter the answer prompt'],
  ['synthesis', 'Model saw the evidence but did not use it'],
  ['judge', 'Answer contained the gold result but was rejected'],
  ['success', 'Evidence survived the complete pipeline'],
] as const;

export default function OracleFunnelLabPage() {
  const [artifact, setArtifact] = useState<OracleFunnelArtifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setArtifact(await fetchLatestOracleFunnel());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const biggestLoss = useMemo(() => {
    if (!artifact?.ledger) return null;
    return STAGES
      .filter(([stage]) => stage !== 'success')
      .map(([stage, description]) => ({
        stage,
        description,
        ...artifact.ledger!.stages[stage],
      }))
      .filter(stage => (stage.count || 0) > 0)
      .sort((a, b) => (b.count || 0) - (a.count || 0))[0] ?? null;
  }, [artifact]);

  return (
    <CarlosLabFrame
      title="Oracle Funnel"
      subtitle="A read-only loss ledger for LoCoMo: where did the gold fact die?"
    >
      <div className="carlos-lab__toolbar">
        <span className="carlos-lab__readonly">OBSERVER ONLY · ZERO EXTRA LLM CALLS</span>
        <button onClick={() => void refresh()} disabled={loading}>
          {loading ? 'Reading…' : 'Refresh artifact'}
        </button>
      </div>

      {error && <div className="carlos-lab__error">{error}</div>}

      {!loading && artifact && !artifact.available && (
        <div className="funnel-empty" data-testid="oracle-funnel-empty">
          <div className="funnel-empty__mark">∅</div>
          <div>
            <h3>No funnel artifact yet</h3>
            <p>{artifact.note}</p>
            <code>
              python eval/locomo/run_locomo.py --phase answer --oracle-funnel
              {' '}--results-suffix=-carlos-lab
            </code>
            <p className="funnel-empty__honesty">
              This page refuses to invent demonstration numbers. Once the
              harness writes a real <code>funnel-*.jsonl</code>, it appears here.
            </p>
          </div>
        </div>
      )}

      {artifact?.available && artifact.ledger && (
        <>
          <div className="funnel-meta">
            <div>
              <span>RUN</span>
              <strong>{artifact.run}</strong>
            </div>
            <div>
              <span>ARM</span>
              <strong>{artifact.condition}</strong>
            </div>
            <div>
              <span>QUESTIONS</span>
              <strong>{artifact.ledger.n_funneled}</strong>
            </div>
            <div>
              <span>LARGEST LOSS</span>
              <strong style={{ color: biggestLoss ? LAB_MAGENTA : LAB_CYAN }}>
                {biggestLoss?.stage ?? 'none'}
              </strong>
            </div>
          </div>

          <div className="funnel-headline" data-testid="oracle-funnel-headline">
            {artifact.ledger.headline}
          </div>

          <div className="funnel-stages">
            {STAGES.map(([stage, description]) => {
              const value = artifact.ledger!.stages[stage] ?? { count: 0, pct: 0 };
              const success = stage === 'success';
              return (
                <article className="funnel-stage" key={stage}>
                  <div className="funnel-stage__copy">
                    <strong style={{ color: success ? LAB_CYAN : LAB_MAGENTA }}>
                      {stage.toUpperCase()}
                    </strong>
                    <span>{description}</span>
                  </div>
                  <div className="funnel-stage__number">
                    {value.count} <small>{value.pct}%</small>
                  </div>
                  <div className="funnel-stage__track">
                    <span
                      style={{
                        width: `${value.pct}%`,
                        background: success ? LAB_CYAN : LAB_MAGENTA,
                      }}
                    />
                  </div>
                </article>
              );
            })}
          </div>

          {!!artifact.sample_rows?.length && (
            <details className="funnel-rows">
              <summary>Inspect the first {artifact.sample_rows.length} question receipts</summary>
              {artifact.sample_rows.map((row, index) => (
                <article key={index}>
                  <div>
                    <span>{String(row.stage ?? 'unknown')}</span>
                    <strong>{String(row.question ?? `Question ${index + 1}`)}</strong>
                  </div>
                  <small>
                    category {String(row.category ?? '—')} ·
                    {' '}{String(row.n_hits ?? 0)} hits ·
                    {' '}oracle neurons {String(row.n_oracle ?? 0)}
                  </small>
                </article>
              ))}
            </details>
          )}
        </>
      )}
    </CarlosLabFrame>
  );
}
