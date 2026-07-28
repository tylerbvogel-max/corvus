import type { ReactNode } from 'react';

export const LAB_MAGENTA = '#ff4fd8';
export const LAB_CYAN = '#45e6ff';

export default function CarlosLabFrame({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <section className="carlos-lab" data-testid="carlos-lab-surface">
      <header className="carlos-lab__header">
        <div>
          <div className="carlos-lab__badge">CARLOS LAB · IMPORTED EXPERIMENT</div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <div className="carlos-lab__chromosome" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      </header>
      <div className="carlos-lab__body">{children}</div>
    </section>
  );
}
