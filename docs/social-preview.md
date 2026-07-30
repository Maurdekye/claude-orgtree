# Social preview — the defined process

`python tools/social_preview.py` regenerates `social-preview.png` (2560×1280,
GitHub's 2:1 social-card size). What it does:

1. Spins a throwaway isolated backend (`%TEMP%`, port 7397).
2. Builds the canonical demo cast: **orchestrator** (opus, grant 12) →
   **implementer** (sonnet, 4) + **planner** (sonnet, 2) → **explorer** ×2
   (haiku) under the implementer.
3. Waits for the intro glide to settle on the full-tree fit.
4. Zooms in about the viewport centre to `CONTENT_SCALE` of the fitted
   extent. The ruling asked for 72%; below ~0.77 the portrait tree clips
   either the eye or the explorer row in the 2:1 frame, so 0.77 is the
   closest non-clipping value — tweak the constant if the cast changes shape.
5. Pans up **from empty canvas** (grabbing a card would drag the node, not
   the camera) so the bottom row clears the frame edge.
6. Parks the cursor off-card — hovering a card would bake its H/S/O/F hire
   chips into the shot — and screenshots.

Upload is **manual**: GitHub repo → Settings → Social preview. GitHub has no
API for it.
