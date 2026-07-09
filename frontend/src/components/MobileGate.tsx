import { useEffect, useState } from 'react';
import corvusLogo from '../assets/corvus-logo.png';

/* "Best viewed on desktop" gate. The desktop is a windowing UI driven
   by cursor movement (wake, hover, edge-snapping, resize handles) that
   doesn't translate to touch — until the mobile shell ships (see the
   master-corvus roadmap node prod-mobile-shell), phones get a polished
   heads-up instead of a broken first impression. "Continue anyway" is
   remembered for the session. */

function isMobileViewport(): boolean {
  const coarse = window.matchMedia('(pointer: coarse)').matches;
  return window.innerWidth < 768 || (coarse && window.innerWidth < 1024);
}

const ACK_KEY = 'corvus-mobile-ack';

export function useMobileGate(): [boolean, () => void] {
  const [gated, setGated] = useState(
    () => isMobileViewport() && !sessionStorage.getItem(ACK_KEY),
  );
  useEffect(() => {
    const onResize = () => {
      if (!sessionStorage.getItem(ACK_KEY)) setGated(isMobileViewport());
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const dismiss = () => {
    sessionStorage.setItem(ACK_KEY, '1');
    setGated(false);
  };
  return [gated, dismiss];
}

export default function MobileGate({ onContinue }: { onContinue: () => void }) {
  return (
    <div className="mobile-gate">
      <img src={corvusLogo} alt="Corvus" className="mobile-gate-logo" />
      <h1>Corvus is built for the desktop</h1>
      <p>
        This interface is a windowed workspace — draggable panes, hover
        interactions, and a live water simulation that follows the cursor.
        On a phone it won't behave the way it's meant to.
      </p>
      <p>Open it on a laptop or desktop for the real experience.</p>
      <button className="mobile-gate-continue" onClick={onContinue}>
        Continue anyway
      </button>
    </div>
  );
}
