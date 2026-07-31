# Social preview — the defined process

`python tools/social_preview.py` regenerates `social-preview.png` (2560×1280,
GitHub's 2:1 social-card size). What it does:

1. Spins a throwaway isolated backend (`%TEMP%`, port 7397).
2. Builds the canonical demo cast (user ruling 2026-07-31):
   **coordinator** (opus, grant 24) → **implementer** (fable) +
   **researcher** (opus, grant 6) → **explorer-1/2** (sonnet) under the
   researcher.
3. Waits for the intro glide to settle on the full-tree fit.
4. Zooms about the viewport centre to `CONTENT_SCALE` of the fitted extent —
   0.88 (user ruling: the agents sit a bit further out than the old tight
   0.77 crop). Tweak the constant if the cast changes shape.
5. Pans up **from empty canvas** (grabbing a card would drag the node, not
   the camera) so the bottom row clears the frame edge.
6. Parks the cursor off-card — hovering a card would bake its H/S/O/F hire
   chips into the shot — and screenshots.

Upload is **manual**: GitHub repo → Settings → Social preview. GitHub has no
API for it.
