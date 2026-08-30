// MUST be the first import: its top-level code (window error/rejection
// listeners) must be live before anything else — including React itself —
// gets a chance to throw. See crashReporter.ts for why.
import { flushPendingReports } from './crashReporter'
import React from 'react'
import ReactDOM from 'react-dom/client'
import './mobile'   // D-125: stamp html.mobile before first paint
import App from './App'
import CrashBoundary, { CrashTestRenderTrigger } from './CrashBoundary'
import './styles.css'

flushPendingReports()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <CrashBoundary>
      <CrashTestRenderTrigger />
      <App />
    </CrashBoundary>
  </React.StrictMode>,
)
