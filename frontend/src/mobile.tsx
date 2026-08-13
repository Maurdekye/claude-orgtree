// D-125: mobile UI exists ONLY on phone/tablet OSes. Not a capability test —
// a Windows 2-in-1 in tablet mode reports `pointer: coarse` and no media
// query can tell it from an Android tablet; only the OS class can (user
// ruling 2026-08-14: "absolutely only shows on phones & tablets and nowhere
// else"). The allowlist: Android UA, iPhone/iPad UA, or a Mac platform with
// real touch points — iPadOS Safari reports a Macintosh UA by default, and
// real Macs report maxTouchPoints 0 even with trackpads. Evaluated ONCE at
// boot and stamped as the `html.mobile` root class that every mobile style
// scopes under; desktop OSes are bit-identical at every window width.
// Escape hatch (both directions): localStorage 'orgtree-mobile' = '1' | '0'.

import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

const compute = (): boolean => {
  try {
    const ov = localStorage.getItem('orgtree-mobile')
    if (ov === '1') return true
    if (ov === '0') return false
  } catch { /* private mode */ }
  try {
    return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
      || (navigator.maxTouchPoints > 1 && /Mac/.test(navigator.platform))
  } catch { return false }
}

export const isMobile: boolean = compute()
if (isMobile) document.documentElement.classList.add('mobile')

/** compact layout tier (phones): the spec's Axis A, evaluated live — but
 *  only ever true on an allowlisted device */
export const isCompact = (): boolean => isMobile && window.innerWidth <= 640

/** D-123 + D-125: the desk opens as a full-screen 1:1 sheet when the short
 *  viewport side cannot host a legible in-card desk. Only within the device
 *  allowlist — no desktop resolution can ever sheet. */
export const sheetGate = (): boolean =>
  isMobile && Math.min(window.innerWidth, window.innerHeight) < 780

/** Mobile-only portal to <body>. The canvas's `.viewport` carries
 *  `touch-action: none` (its pan/pinch surface), and touch-action narrows
 *  going DOWN the DOM — a scroller nested inside it can never scroll by
 *  touch. Portaling the overlays out re-enables native touch scrolling.
 *  Desktop renders children IN PLACE (no wrapper, no portal) so its DOM is
 *  byte-identical to before this wave — the mobile wrapper also stops
 *  pointerdown, because React synthetic events bubble through the COMPONENT
 *  tree (a portaled overlay's press would otherwise start a canvas pan). */
export function MaybePortal({ children }: { children: ReactNode }) {
  if (!isMobile) return <>{children}</>
  return createPortal(
    <div onPointerDown={(e) => e.stopPropagation()}>{children}</div>,
    document.body)
}
