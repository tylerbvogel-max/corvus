import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './App.css'

async function boot() {
  // Seeded demo build (VITE_DEMO=1): intercept fetch with canned fixtures
  // before anything mounts. The dynamic import keeps the shim and its
  // fixtures out of normal builds (dead-branch eliminated).
  if (import.meta.env.VITE_DEMO === '1') {
    const { installDemoShim } = await import('./demo/shim')
    installDemoShim()
  }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void boot()
