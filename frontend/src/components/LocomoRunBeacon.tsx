import { useEffect, useState } from 'react';

/** LoCoMo certificate beacon — flashes on the Pallium when a benchmark
 *  phase finishes, so run completion is visible from any browser tab with
 *  no session attached. Data: GET /metrics/mind/locomo-run (log-derived).
 *  Click acknowledges the flash (per phase-completion event, localStorage). */

interface PhaseStatus {
  state: 'pending' | 'running' | 'done' | 'failed';
  detail: string | null;
  changed_at: string | null;
}

interface LocomoRun {
  phase_a: PhaseStatus;
  phase_b: PhaseStatus;
  scored_conversations: number;
  total_conversations: number;
}

const ACK_KEY = 'locomo-beacon-acked';

function ackedSet(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(ACK_KEY) ?? '[]'));
  } catch {
    return new Set();
  }
}

function eventKey(name: string, p: PhaseStatus): string {
  return `${name}:${p.state}:${p.changed_at ?? ''}`;
}

export default function LocomoRunBeacon() {
  const [run, setRun] = useState<LocomoRun | null>(null);
  const [acked, setAcked] = useState<Set<string>>(ackedSet);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      fetch('/metrics/mind/locomo-run')
        .then(r => (r.ok ? r.json() : null))
        .then(d => { if (alive && d) setRun(d); })
        .catch(() => {});
    poll();
    const id = setInterval(poll, 30_000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (!run) return null;

  const phases: Array<[string, PhaseStatus]> = [
    ['Phase A', run.phase_a],
    ['Phase B', run.phase_b],
  ];
  const finished = phases.filter(
    ([name, p]) =>
      (p.state === 'done' || p.state === 'failed') && !acked.has(eventKey(name, p)),
  );
  const running = phases.find(([, p]) => p.state === 'running');

  const ack = (name: string, p: PhaseStatus) => {
    const next = new Set(acked);
    next.add(eventKey(name, p));
    localStorage.setItem(ACK_KEY, JSON.stringify([...next]));
    setAcked(next);
  };

  if (finished.length === 0 && !running) return null;

  return (
    <>
      <style>{`
        @keyframes locomo-flash {
          0%, 100% { background: #ffd60a; color: #10120a; box-shadow: 0 0 28px 6px rgba(255, 214, 10, 0.55); }
          50% { background: #1a1c10; color: #ffd60a; box-shadow: 0 0 6px 1px rgba(255, 214, 10, 0.25); }
        }
        @keyframes locomo-flash-red {
          0%, 100% { background: #ff453a; color: #1a0505; box-shadow: 0 0 28px 6px rgba(255, 69, 58, 0.55); }
          50% { background: #1c0a08; color: #ff453a; box-shadow: 0 0 6px 1px rgba(255, 69, 58, 0.25); }
        }
        @keyframes locomo-breathe { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
        .locomo-beacon-flash {
          width: 100%; border: none; cursor: pointer; border-radius: 8px;
          padding: 12px 16px; margin-bottom: 10px; text-align: left;
          font: inherit; font-weight: 700; letter-spacing: 0.03em;
          animation: locomo-flash 0.9s steps(2, start) infinite;
        }
        .locomo-beacon-flash.failed { animation-name: locomo-flash-red; }
        .locomo-beacon-running {
          border-radius: 8px; padding: 8px 14px; margin-bottom: 10px;
          font-size: 12px; border: 1px solid rgba(255, 214, 10, 0.35);
          color: inherit; opacity: 0.85;
        }
        .locomo-beacon-running .locomo-dot {
          display: inline-block; width: 8px; height: 8px; border-radius: 50%;
          background: #ffd60a; margin-right: 8px;
          animation: locomo-breathe 1.6s ease-in-out infinite;
        }
      `}</style>
      {finished.map(([name, p]) => (
        <button
          key={eventKey(name, p)}
          className={`locomo-beacon-flash${p.state === 'failed' ? ' failed' : ''}`}
          title="Click to acknowledge"
          onClick={() => ack(name, p)}
        >
          ◆ LoCoMo {name} {p.state === 'failed' ? 'FAILED' : 'COMPLETE'}
          {p.state === 'done' && name === 'Phase B'
            ? ` — ${run.scored_conversations}/${run.total_conversations} conversations scored`
            : ''}
          {p.state === 'failed' && p.detail ? ` — ${p.detail}` : ''}
          <span style={{ float: 'right', fontWeight: 400, opacity: 0.75 }}>click to dismiss</span>
        </button>
      ))}
      {finished.length === 0 && running && (
        <div className="locomo-beacon-running">
          <span className="locomo-dot" />
          LoCoMo {running[0]} running
          {running[1].detail ? ` — ${running[1].detail}` : ''}
          {running[0] === 'Phase B'
            ? ` · ${run.scored_conversations}/${run.total_conversations} scored`
            : ''}
        </div>
      )}
    </>
  );
}
