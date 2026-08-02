import { useState, useEffect } from 'react';
import { fetchSystemBanner } from '../api/operator';

const BANNER_ACK_KEY = 'corvus-banner-ack';

export default function SystemUseBanner() {
  const [bannerText, setBannerText] = useState('');
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const acked = sessionStorage.getItem(BANNER_ACK_KEY);
    if (acked) return;

    fetchSystemBanner()
      .then(data => {
        if (data.enabled && data.banner_text) {
          setBannerText(data.banner_text);
          setVisible(true);
        }
      })
      .catch(() => {
        // Banner fetch failure should never block the app
      });
  }, []);

  if (!visible) return null;

  function acknowledge() {
    sessionStorage.setItem(BANNER_ACK_KEY, '1');
    setVisible(false);
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 10000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
    }}>
      <div style={{
        background: 'var(--bg-card, #1e293b)', border: '1px solid #334155',
        borderRadius: 10, padding: '32px 36px', maxWidth: 560,
        boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
      }}>
        <h3 style={{ marginTop: 0, color: '#f59e0b', fontSize: '1rem', letterSpacing: 0.5 }}>
          SYSTEM USE NOTIFICATION
        </h3>
        <p style={{
          color: '#cbd5e1', fontSize: '0.85rem', lineHeight: 1.7,
          whiteSpace: 'pre-wrap', marginBottom: 24,
        }}>
          {bannerText}
        </p>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={acknowledge}
            style={{
              background: '#3b82f6', color: '#fff', border: 'none',
              borderRadius: 6, padding: '8px 24px', cursor: 'pointer',
              fontSize: '0.85rem', fontWeight: 600,
            }}
          >
            I Acknowledge
          </button>
        </div>
      </div>
    </div>
  );
}
