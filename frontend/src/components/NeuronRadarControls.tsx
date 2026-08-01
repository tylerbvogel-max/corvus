import { useRef, type KeyboardEvent, type PointerEvent } from 'react';

export interface RadarAxis {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
}

interface Props {
  axes: RadarAxis[];
  layoutMode: 'organic' | 'zones';
  zonesAvailable: boolean;
  bloom: boolean;
  motion: boolean;
  synapses: boolean;
  firings: boolean;
  firingPace: number;
  recallActive: boolean;
  replayTraceCount: number;
  onLayoutModeChange: (mode: 'organic' | 'zones') => void;
  onBloomChange: (enabled: boolean) => void;
  onMotionChange: (enabled: boolean) => void;
  onSynapsesChange: (enabled: boolean) => void;
  onFiringsChange: (enabled: boolean) => void;
  onRecallOpen: () => void;
  onFitView: () => void;
}

const WIDTH = 260;
const HEIGHT = 250;
const CX = 130;
const CY = 125;
const RADIUS = 76;
const LABEL_RADIUS = 103;
const INNER_FRACTION = 0.22;

function clamp(value: number, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function normalized(axis: RadarAxis) {
  return clamp((axis.value - axis.min) / (axis.max - axis.min || 1));
}

function angleFor(index: number, count: number) {
  return -Math.PI / 2 + (index * Math.PI * 2) / Math.max(1, count);
}

function radialFraction(axis: RadarAxis) {
  return INNER_FRACTION + normalized(axis) * (1 - INNER_FRACTION);
}

function point(index: number, fraction: number, count: number) {
  const angle = angleFor(index, count);
  return {
    x: CX + Math.cos(angle) * RADIUS * fraction,
    y: CY + Math.sin(angle) * RADIUS * fraction,
  };
}

function polygonPoints(fraction: number, count: number) {
  return Array.from({ length: count }, (_, index) => {
    const p = point(index, fraction, count);
    return `${p.x},${p.y}`;
  }).join(' ');
}

function labelPosition(index: number, count: number) {
  const angle = angleFor(index, count);
  const cosine = Math.cos(angle);
  return {
    x: CX + cosine * LABEL_RADIUS,
    y: CY + Math.sin(angle) * LABEL_RADIUS - 2,
    anchor: cosine > 0.28 ? 'end' : cosine < -0.28 ? 'start' : 'middle',
  } as const;
}

export default function NeuronRadarControls({
  axes,
  layoutMode,
  zonesAvailable,
  bloom,
  motion,
  synapses,
  firings,
  firingPace,
  recallActive,
  replayTraceCount,
  onLayoutModeChange,
  onBloomChange,
  onMotionChange,
  onSynapsesChange,
  onFiringsChange,
  onRecallOpen,
  onFitView,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragAxis = useRef<number | null>(null);
  const firingLive = motion && firings && firingPace > 0;

  const setFromClientPoint = (index: number, clientX: number, clientY: number) => {
    const svg = svgRef.current;
    const axis = axes[index];
    if (!svg || !axis) return;
    const rect = svg.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * WIDTH;
    const y = ((clientY - rect.top) / rect.height) * HEIGHT;
    const angle = angleFor(index, axes.length);
    const projection = (x - CX) * Math.cos(angle) + (y - CY) * Math.sin(angle);
    const radial = clamp(projection / RADIUS, INNER_FRACTION, 1);
    const fraction = (radial - INNER_FRACTION) / (1 - INNER_FRACTION);
    const raw = axis.min + fraction * (axis.max - axis.min);
    const steps = Math.round((raw - axis.min) / axis.step);
    const next = clamp(axis.min + steps * axis.step, axis.min, axis.max);
    axis.onChange(Number(next.toFixed(6)));
  };

  const onPointerDown = (event: PointerEvent<SVGElement>, index: number) => {
    event.preventDefault();
    event.stopPropagation();
    dragAxis.current = index;
    svgRef.current?.setPointerCapture(event.pointerId);
    setFromClientPoint(index, event.clientX, event.clientY);
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (dragAxis.current == null) return;
    setFromClientPoint(dragAxis.current, event.clientX, event.clientY);
  };

  const stopDragging = (event: PointerEvent<SVGSVGElement>) => {
    if (dragAxis.current == null) return;
    dragAxis.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const onHandleKeyDown = (event: KeyboardEvent<SVGCircleElement>, axis: RadarAxis) => {
    let direction = 0;
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') direction = 1;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') direction = -1;
    if (event.key === 'Home') {
      event.preventDefault();
      axis.onChange(axis.min);
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      axis.onChange(axis.max);
      return;
    }
    if (!direction) return;
    event.preventDefault();
    axis.onChange(clamp(
      Number((axis.value + direction * axis.step).toFixed(6)),
      axis.min,
      axis.max,
    ));
  };

  const valuePoints = axes.map((axis, index) => {
    const p = point(index, radialFraction(axis), axes.length);
    return `${p.x},${p.y}`;
  }).join(' ');

  return (
    <div className="neuron-radar-controls">
      <div className="neuron-radar-mode" data-testid="neuron-layout-toggle">
        <button
          type="button"
          className={layoutMode === 'organic' ? 'active' : ''}
          aria-pressed={layoutMode === 'organic'}
          data-testid="layout-organic"
          onClick={() => onLayoutModeChange('organic')}
        >
          Organic
        </button>
        <button
          type="button"
          className={layoutMode === 'zones' ? 'active' : ''}
          aria-pressed={layoutMode === 'zones'}
          data-testid="layout-zones"
          disabled={!zonesAvailable}
          onClick={() => onLayoutModeChange('zones')}
        >
          Assemblies
        </button>
      </div>

      <div
        className="neuron-radar-mode neuron-radar-effects"
        data-testid="neuron-effect-toggles"
        aria-label="Neuron universe effects"
      >
        <button
          type="button"
          className={bloom ? 'active' : ''}
          aria-pressed={bloom}
          data-testid="toggle-bloom"
          onClick={() => onBloomChange(!bloom)}
        >
          Bloom
        </button>
        <button
          type="button"
          className={motion ? 'active' : ''}
          aria-pressed={motion}
          data-testid="toggle-motion"
          onClick={() => onMotionChange(!motion)}
        >
          Motion
        </button>
        <button
          type="button"
          className={synapses ? 'active' : ''}
          aria-pressed={synapses}
          data-testid="toggle-synapses"
          onClick={() => onSynapsesChange(!synapses)}
        >
          Synapses
        </button>
        <button
          type="button"
          className={firings ? 'active' : ''}
          aria-pressed={firings}
          data-testid="toggle-firings"
          title="Animate a fair sweep across the full visible synapse graph"
          onClick={() => onFiringsChange(!firings)}
        >
          Firings
        </button>
      </div>

      <div className="neuron-radar-utility">
        <button
          type="button"
          className={`neuron-replay-status${recallActive ? ' active' : ''}`}
          data-testid="recall-replay-status"
          aria-pressed={recallActive}
          onClick={onRecallOpen}
        >
          <span className={firingLive ? 'live' : ''} />
          Recall replay
          <strong>{replayTraceCount} traces · {firingLive ? 'live' : 'paused'}</strong>
        </button>
        <button
          type="button"
          className="neuron-fit-view"
          data-testid="fit-neuron-view"
          title="Fit visible neurons to view"
          aria-label="Fit visible neurons to view"
          onClick={onFitView}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 2H2v4M10 2h4v4M6 14H2v-4M10 14h4v-4" />
          </svg>
          Fit
        </button>
      </div>

      <div className="neuron-radar-frame" data-testid="neuron-radar-control">
        <svg
          ref={svgRef}
          className="neuron-radar"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="group"
          aria-label="Neuron universe visual controls"
          onPointerMove={onPointerMove}
          onPointerUp={stopDragging}
          onPointerCancel={stopDragging}
        >
          <defs>
            <linearGradient id="neuron-radar-fill" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#53d8fb" stopOpacity="0.34" />
              <stop offset="100%" stopColor="#aa91ff" stopOpacity="0.22" />
            </linearGradient>
            <filter id="neuron-radar-glow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {[0.25, 0.5, 0.75, 1].map(fraction => (
            <polygon
              key={fraction}
              className="neuron-radar-grid"
              points={polygonPoints(fraction, axes.length)}
            />
          ))}

          {axes.map((axis, index) => {
            const end = point(index, 1, axes.length);
            return (
              <g key={axis.key}>
                <line className="neuron-radar-axis" x1={CX} y1={CY} x2={end.x} y2={end.y} />
                <line
                  className="neuron-radar-hit-axis"
                  x1={CX}
                  y1={CY}
                  x2={end.x}
                  y2={end.y}
                  onPointerDown={event => onPointerDown(event, index)}
                />
              </g>
            );
          })}

          <polygon className="neuron-radar-value" points={valuePoints} />
          <circle className="neuron-radar-origin" cx={CX} cy={CY} r="2.5" />

          {axes.map((axis, index) => {
            const handle = point(index, radialFraction(axis), axes.length);
            const label = labelPosition(index, axes.length);
            return (
              <g key={axis.key}>
                <text
                  className="neuron-radar-label"
                  x={label.x}
                  y={label.y}
                  textAnchor={label.anchor}
                >
                  {axis.label}
                </text>
                <text
                  className="neuron-radar-reading"
                  x={label.x}
                  y={label.y + 14}
                  textAnchor={label.anchor}
                >
                  {axis.format(axis.value)}
                </text>
                <circle
                  className="neuron-radar-handle-halo"
                  cx={handle.x}
                  cy={handle.y}
                  r="10"
                />
                <circle
                  className="neuron-radar-handle"
                  cx={handle.x}
                  cy={handle.y}
                  r="5.5"
                  role="slider"
                  tabIndex={0}
                  aria-label={axis.label}
                  aria-valuemin={axis.min}
                  aria-valuemax={axis.max}
                  aria-valuenow={axis.value}
                  aria-valuetext={axis.format(axis.value)}
                  data-testid={`radar-handle-${axis.key}`}
                  onPointerDown={event => onPointerDown(event, index)}
                  onKeyDown={event => onHandleKeyDown(event, axis)}
                />
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
