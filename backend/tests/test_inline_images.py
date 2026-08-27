"""FR-28 / D-167 — a user's attached image reaches the agent's context.

WHAT THIS COVERS
----------------
User request 2026-08-27: "if i send any images in a message, they should be
immediately loaded into context if under a certain reasonable max size".

The happy path is the least interesting thing here. ⭐ THE PROPERTY THAT
MATTERS IS THE NEGATIVE ONE: **an attachment must never vanish silently.**
Whatever happens to a file — too big, corrupt, wrong format, over the turn
budget, arriving mid-task where images cannot travel, or sent by someone whose
images we deliberately do not inline — the agent must be TOLD it existed and
why it is not in front of them. An agent that never learns a file was sent
cannot ask for it, and the sender has no way to discover it never arrived.
That absence-that-reads-like-a-normal-turn is the failure class this suite is
really about.

WHY THE CHECKS COME IN PAIRS
----------------------------
Every "it is announced and not inlined" assertion is paired with one that must
come out the OTHER way, because "nothing is ever inlined" satisfies the whole
negative half on its own and is a perfectly plausible bug. Likewise every
"it is inlined" has a twin that must not be.

⚠ WHAT THIS SUITE DOES *NOT* PROVE, and cannot: that Anthropic's API accepts
these blocks or costs what the docs say. The MECHANISM was measured separately
(a real PNG through the pinned CLI came back described — see imgblock.py's
header); the LIMITS are read from published docs. This suite pins OUR
behaviour only. If the vendor's numbers change, nothing here goes red.

Run:  python tests/test_inline_images.py
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ⚠ BEFORE the first orgtree import — store resolves ORGTREE_DATA at import.
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-img-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy                                                # noqa: E402
from orgtree import imgblock, store, supervisor                  # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

_no_deploy.install()
_no_deploy.assert_isolated_data_root()

_HERE = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_GOT = os.path.realpath(os.path.dirname(os.path.dirname(supervisor.__file__)))
if _GOT != _HERE:
    raise SystemExit(
        f"☠ REFUSING TO RUN: this suite lives under {_HERE!r} but imported "
        f"orgtree from {_GOT!r}.")
print(f"testing orgtree at: {_GOT}")

PASS = 0
FAIL: list[str] = []


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        FAIL.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n        {e}")
        return
    PASS += 1
    print(f"  ok    {label}")


try:
    from PIL import Image
    HAVE_PIL = True
except Exception:                                                # noqa: BLE001
    HAVE_PIL = False

TMP = tempfile.mkdtemp(prefix="imgfix-")


def png(name, size=(8, 8), colour=(255, 0, 0)):
    p = os.path.join(TMP, name)
    Image.new("RGB", size, colour).save(p)
    return p


def animated_gif(name="anim.gif"):
    """⚠ THE FRAMES MUST GENUINELY DIFFER. The first version of this built two
    `Image.new("P", …)` frames with different palette INDICES but the same
    default palette; Pillow collapsed them and wrote a ONE-frame GIF. The
    animated-GIF check then passed a still image to the code under test and
    "failed" for the right reason by luck — a fixture that does not build what
    it claims is the check testing nothing. Verified: n_frames == 2."""
    p = os.path.join(TMP, name)
    a = Image.new("RGB", (8, 8), (255, 0, 0)).convert("P")
    b = Image.new("RGB", (8, 8), (0, 0, 255)).convert("P")
    a.save(p, save_all=True, append_images=[b], duration=200, loop=0)
    with Image.open(p) as chk:
        assert getattr(chk, "n_frames", 1) == 2, \
            f"the animated fixture is not animated (n_frames=" \
            f"{getattr(chk, 'n_frames', 1)}) — the check using it proves " \
            f"nothing"
    return p


def junk(name, data=b"not an image at all", ):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


# ---------------------------------------------------------------------------
print("\n§0 · the fixtures themselves")

check("Pillow is importable (this suite's own precondition)",
      lambda: None if HAVE_PIL else (_ for _ in ()).throw(
          AssertionError("Pillow missing — the inline path cannot be tested; "
                         "it is a DECLARED dependency in requirements.txt")))


def _fixtures_are_what_they_claim():
    small = png("small.png")
    assert os.path.getsize(small) < 1000, os.path.getsize(small)
    bad = junk("broken.png")           # PNG *name*, garbage bytes
    assert os.path.getsize(bad) > 0
    # …and the control that matters: the two must not be interchangeable
    assert imgblock.is_image_name(small) and imgblock.is_image_name(bad), \
        "both are named .png — the NAME gate cannot tell them apart, which " \
        "is precisely why a decode gate has to exist"


check("fixtures: a real PNG and a garbage file share the .png NAME "
      "(control pair)", _fixtures_are_what_they_claim)


# ---------------------------------------------------------------------------
print("\n§1 · imgblock — the unit that decides")


def _the_name_gate_is_only_a_name_gate():
    for good in ("a.png", "b.JPG", "c.jpeg", "d.gif", "e.webp"):
        assert imgblock.is_image_name(good), good
    for bad in ("a.pdf", "b.txt", "c.svg", "d.bmp", "e", "f.png.txt", ""):
        assert not imgblock.is_image_name(bad), bad


check("name gate: accepts the four renderable formats, refuses the rest "
      "(control pair)", _the_name_gate_is_only_a_name_gate)


def _a_real_png_becomes_a_real_block():
    blk, note = imgblock.load_image_block(png("ok.png", (12, 34)), 10 ** 9)
    assert blk is not None, f"a valid PNG produced no block ({note})"
    assert blk["type"] == "image"
    assert blk["source"]["type"] == "base64"
    assert blk["source"]["media_type"] == "image/png", blk["source"]
    assert len(blk["source"]["data"]) > 0
    # the note carries the dimensions, which is what lets the mail line say
    # something checkable rather than "an image"
    assert "12x34" in (note or ""), note
    # ⚠ and it must be REAL base64 of the REAL file, not a placeholder
    import base64
    raw = base64.b64decode(blk["source"]["data"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", raw[:8]


check("a valid PNG becomes a base64 image block carrying the actual bytes",
      _a_real_png_becomes_a_real_block)


def _every_refusal_says_why():
    """The heart of it. Each of these must return NO block and a NON-EMPTY
    reason — a None block with a None note would be the silent drop."""
    cases = {
        "oversized": (png("big.png"), 10 ** 9, None),
        "over budget": (png("bud.png"), 1, None),
        "corrupt": (junk("corrupt.png"), 10 ** 9, None),
        "missing": (os.path.join(TMP, "nope.png"), 10 ** 9, None),
    }
    # make the oversized one genuinely oversized without writing 5 MB of pixels
    big = cases["oversized"][0]
    with open(big, "ab") as f:
        f.write(b"\0" * (imgblock.INLINE_IMAGE_MAX_BYTES + 1))
    for name, (path, budget, _) in cases.items():
        blk, note = imgblock.load_image_block(path, budget)
        assert blk is None, f"{name}: expected refusal, got a block"
        assert note and note.strip(), \
            f"☠ {name} WAS DROPPED SILENTLY — no block and no reason. This " \
            f"is the exact failure the feature exists to prevent."
    # each reason must be DISTINGUISHABLE — one generic "cannot load it"
    # for every cause tells the agent nothing it can act on
    notes = {n: imgblock.load_image_block(p, b)[1]
             for n, (p, b, _) in cases.items()}
    assert len(set(notes.values())) == len(notes), \
        f"two different failures produced the same words: {notes}"
    # and the size refusal must NAME the size and the limit
    assert "MB" in (notes["oversized"] or ""), notes["oversized"]


check("☠ every refusal returns a reason, and the reasons differ by cause",
      _every_refusal_says_why)


def _the_control_that_refusal_is_not_universal():
    """Without this, '§1 every refusal says why' passes on a build that
    refuses absolutely everything."""
    blk, _ = imgblock.load_image_block(png("ctl.png"), 10 ** 9)
    assert blk is not None, "nothing is ever inlined — every refusal check " \
                            "above is vacuous"


check("control: a good image under the caps IS inlined", _the_control_that_refusal_is_not_universal)


def _a_wrong_format_is_named():
    # a real BMP, correctly named .bmp -> the name gate stops it
    p = os.path.join(TMP, "x.bmp")
    Image.new("RGB", (4, 4)).save(p)
    assert not imgblock.is_image_name(p)
    # a real BMP wearing a .png name -> only the DECODE gate can catch it,
    # and it must name the format rather than say "corrupt"
    p2 = os.path.join(TMP, "liar.png")
    Image.new("RGB", (4, 4)).save(p2, format="BMP")
    blk, note = imgblock.load_image_block(p2, 10 ** 9)
    assert blk is None, "a BMP was inlined as a PNG"
    assert "BMP" in (note or ""), note


check("a mislabelled format is refused and NAMED (not called corrupt)",
      _a_wrong_format_is_named)


def _an_animated_gif_says_it_is_animated():
    blk, note = imgblock.load_image_block(animated_gif(), 10 ** 9)
    assert blk is not None, f"an animated GIF was refused outright ({note})"
    assert "ANIMATED" in (note or ""), (
        f"note={note!r} — the first frame is all the model sees, and an "
        f"agent describing one frame while believing it saw the animation "
        f"is wrong in a way it cannot detect")
    # control: a still GIF must NOT wear the animated warning
    still = os.path.join(TMP, "still.gif")
    Image.new("P", (8, 8), 3).save(still)
    _b2, n2 = imgblock.load_image_block(still, 10 ** 9)
    assert "ANIMATED" not in (n2 or ""), n2


check("an animated GIF is inlined AND flagged first-frame-only; a still GIF "
      "is not flagged (control pair)", _an_animated_gif_says_it_is_animated)


# ---------------------------------------------------------------------------
print("\n§2 · the mail block — what the agent actually reads")


def mkorg(name):
    o = store.create_org(name)
    o.hire(USER, None, "haiku", 5, "boss", add_dirs=[],
           tools={"bash": False, "web": False, "edit": False,
                  "subagents": False, "mcp": []},
           org_visibility="team", charter="image fixture")
    store.save_org(o)
    return o, o.d["slug"]


def upload(slug, nid, src, as_name=None):
    """Put a file where a real attachment would be: the node's uploads/."""
    up = os.path.join(supervisor.scratch_dir(slug, nid), "uploads")
    os.makedirs(up, exist_ok=True)
    dst = os.path.join(up, as_name or os.path.basename(src))
    with open(src, "rb") as a, open(dst, "wb") as b:
        b.write(a.read())
    return f"uploads/{os.path.basename(dst)}", os.path.getsize(dst)


def mail(frm, atts, body="look at this"):
    return [{"id": "m1", "from": frm, "kind": "message", "body": body,
             "at": "2026-08-27T00:00:00.000Z", "relationship": "the user",
             "attachments": atts}]


def _the_users_image_is_inlined_and_announced():
    o, slug = mkorg("zz img user")
    try:
        rel, nb = upload(slug, "boss", png("shot.png", (20, 10)))
        txt, imgs = supervisor._mail_block(
            mail(USER, [{"name": "shot.png", "path": rel, "bytes": nb}]),
            slug, "boss", inline=True)
        assert len(imgs) == 1, f"expected one image block, got {len(imgs)}"
        assert imgs[0]["type"] == "image"
        assert "loaded into your context" in txt, txt
        assert "20x10" in txt, txt
        # the announce line must still be there — the image block is IN
        # ADDITION to being told a file arrived, never instead of it
        assert "[ATTACHED FILE:" in txt and rel in txt, txt
    finally:
        store.delete_org(slug)


check("user image: a real block AND a line saying so (both, not either)",
      _the_users_image_is_inlined_and_announced)


def _an_oversized_user_image_is_announced_never_dropped():
    o, slug = mkorg("zz img big")
    try:
        big = png("huge.png")
        with open(big, "ab") as f:
            f.write(b"\0" * (imgblock.INLINE_IMAGE_MAX_BYTES + 1))
        rel, nb = upload(slug, "boss", big)
        txt, imgs = supervisor._mail_block(
            mail(USER, [{"name": "huge.png", "path": rel, "bytes": nb}]),
            slug, "boss", inline=True)
        assert imgs == [], "an oversized image was inlined anyway"
        assert "NOT loaded" in txt, txt
        assert rel in txt, "the agent is not even told which file it was"
        assert "MB" in txt, f"the size is not stated: {txt}"
        # ⭐ and it must offer something to DO
        assert "resize" in txt or "Read" in txt, txt
    finally:
        store.delete_org(slug)


check("☠ oversized: announced with its size and a remedy, never dropped",
      _an_oversized_user_image_is_announced_never_dropped)


def _a_corrupt_file_is_announced_and_does_not_raise():
    o, slug = mkorg("zz img bad")
    try:
        rel, nb = upload(slug, "boss", junk("rotten.png"))
        txt, imgs = supervisor._mail_block(
            mail(USER, [{"name": "rotten.png", "path": rel, "bytes": nb}]),
            slug, "boss", inline=True)
        assert imgs == [], "a corrupt file produced an image block"
        assert "NOT loaded" in txt and rel in txt, txt
    finally:
        store.delete_org(slug)


check("☠ corrupt file: announced, no block, no exception",
      _a_corrupt_file_is_announced_and_does_not_raise)


def _a_missing_file_is_announced_and_does_not_raise():
    o, slug = mkorg("zz img gone")
    try:
        txt, imgs = supervisor._mail_block(
            mail(USER, [{"name": "ghost.png", "path": "uploads/ghost.png",
                         "bytes": 100}]), slug, "boss", inline=True)
        assert imgs == []
        assert "NOT loaded" in txt, txt
    finally:
        store.delete_org(slug)


check("☠ a vanished file: announced, no crash",
      _a_missing_file_is_announced_and_does_not_raise)


def _non_image_attachments_are_untouched():
    """The regression guard: this feature must not change what happens to a
    .txt, and must not claim anything about it."""
    o, slug = mkorg("zz img txt")
    try:
        rel, nb = upload(slug, "boss", junk("notes.txt"))
        txt, imgs = supervisor._mail_block(
            mail(USER, [{"name": "notes.txt", "path": rel, "bytes": nb}]),
            slug, "boss", inline=True)
        assert imgs == []
        assert "[ATTACHED FILE:" in txt and rel in txt
        assert "loaded into your context" not in txt, txt
        assert "NOT loaded" not in txt, \
            "a plain text attachment is being described as a failed image"
    finally:
        store.delete_org(slug)


check("a non-image attachment reads exactly as before (no image claims)",
      _non_image_attachments_are_untouched)


def _the_count_cap_binds_and_the_overflow_is_announced():
    o, slug = mkorg("zz img many")
    try:
        n = imgblock.INLINE_IMAGE_MAX_COUNT + 3
        atts = []
        for i in range(n):
            rel, nb = upload(slug, "boss", png(f"m{i}.png"), f"m{i}.png")
            atts.append({"name": f"m{i}.png", "path": rel, "bytes": nb})
        txt, imgs = supervisor._mail_block(mail(USER, atts), slug, "boss",
                                           inline=True)
        assert len(imgs) == imgblock.INLINE_IMAGE_MAX_COUNT, (
            f"the per-turn count cap did not bind: {len(imgs)} blocks for "
            f"{n} images")
        # ⭐ the ones that did NOT make it must say so — this is the cap
        # doing its job WITHOUT becoming a silent drop
        assert txt.count("per-turn limit") == 3, txt.count("per-turn limit")
    finally:
        store.delete_org(slug)


check("☠ the count cap binds AND every image past it is announced",
      _the_count_cap_binds_and_the_overflow_is_announced)


def _mid_task_does_not_inline_and_promises_nothing():
    o, slug = mkorg("zz img steer")
    try:
        rel, nb = upload(slug, "boss", png("mid.png"))
        atts = [{"name": "mid.png", "path": rel, "bytes": nb}]
        steer_txt, steer_imgs = supervisor._mail_block(
            mail(USER, atts), slug, "boss", inline=False)
        assert steer_imgs == [], \
            "mid-task produced image blocks — they cannot travel in " \
            "additionalContext, so these would be silently discarded"
        assert "Read" in steer_txt and rel in steer_txt, steer_txt
        # ⚠ IT MUST NOT PROMISE A LATER LOAD. Steered mail is DRAINED on
        # delivery, so it is never re-presented and no later turn inlines it.
        low = steer_txt.lower()
        assert "next turn" not in low, (
            f"the mid-task note promises a later load that will never "
            f"happen — steered mail is drained: {steer_txt}")
        assert "will not load later" in low, steer_txt
        # …and the control: the SAME mail at a turn boundary DOES inline
        _t2, turn_imgs = supervisor._mail_block(mail(USER, atts), slug,
                                                "boss", inline=True)
        assert len(turn_imgs) == 1, \
            "the turn-boundary control failed — this pair proves the " \
            "difference is the CARRIER, not a broken image"
    finally:
        store.delete_org(slug)


check("☠ mid-task: no blocks, names the file, offers Read, and promises "
      "NO later load; the same mail inlines at a boundary (control pair)",
      _mid_task_does_not_inline_and_promises_nothing)


def _only_the_users_images_are_inlined():
    o, slug = mkorg("zz img whose")
    try:
        rel, nb = upload(slug, "boss", png("peer.png"))
        atts = [{"name": "peer.png", "path": rel, "bytes": nb}]
        for sender in ("some-peer", "@org:other", "@net:elsewhere"):
            txt, imgs = supervisor._mail_block(
                mail(sender, atts), slug, "boss", inline=True)
            assert imgs == [], (
                f"an image from {sender!r} was inlined — outside mail is "
                f"untrusted input and inlining it was ruled out deliberately")
            assert "not auto-loaded" in txt, txt
            assert rel in txt, "…and it was not even announced"
        # control: same file, same everything, sender is the user
        txt, imgs = supervisor._mail_block(mail(USER, atts), slug, "boss",
                                           inline=True)
        assert len(imgs) == 1, \
            "nothing is inlined for anyone — the sender check above is vacuous"
    finally:
        store.delete_org(slug)


check("☠ only the USER's images inline; peers/@org:/@net: are announced only "
      "(control pair)", _only_the_users_images_are_inlined)


# ---------------------------------------------------------------------------
print("\n§3 · the carrier — the blocks reach the CLI")


def _user_event_carries_the_blocks():
    import json
    blk = {"type": "image",
           "source": {"type": "base64", "media_type": "image/png",
                      "data": "QUJD"}}
    ev = json.loads(supervisor._user_event("hello", [blk]))
    content = ev["message"]["content"]
    assert content[0] == {"type": "text", "text": "hello"}, content[0]
    assert content[1] == blk, content[1]
    assert ev["type"] == "user" and ev["message"]["role"] == "user"
    # the no-image shape must be byte-identical to what it always was, or
    # every turn in the system changes shape for a feature most turns skip
    assert json.loads(supervisor._user_event("hello")) == {
        "type": "user",
        "message": {"role": "user",
                    "content": [{"type": "text", "text": "hello"}]}}


check("_user_event: images ride after the text; the no-image shape is "
      "unchanged (control pair)", _user_event_carries_the_blocks)


def _the_envelope_carries_images_only_on_the_turn_path():
    o, slug = mkorg("zz img env")
    try:
        rel, nb = upload(slug, "boss", png("env.png"))
        att = [{"name": "env.png", "path": rel, "bytes": nb}]
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.post_mail(USER, "boss", "see this", attachments=att)
            store.save_org(org)
        txt, tok, imgs = supervisor._envelope(slug, "boss", "nudge",
                                              via="turn")
        assert len(imgs) == 1, f"turn envelope carried no image ({imgs})"
        assert "[MAIL" in txt and tok

        # the same mail arriving on the STEER path carries none
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.post_mail(USER, "boss", "see this too", attachments=att)
            store.save_org(org)
        stxt, stok, simgs = supervisor._envelope(slug, "boss", "nudge",
                                                 via="steer")
        assert simgs == [], "the steer envelope produced image blocks"
        assert rel in stxt
    finally:
        store.delete_org(slug)


check("_envelope: via='turn' carries blocks, via='steer' carries none "
      "(control pair)", _the_envelope_carries_images_only_on_the_turn_path)


def _an_unknown_node_still_returns_three_values():
    # an EXISTING org with a node that is not in it — the real early return.
    # (An unknown ORG raises out of store.load_org and never reaches it.)
    o, slug = mkorg("zz img nonode")
    try:
        r = supervisor._envelope(slug, "nobody", "plain")
        assert r == ("plain", None, []), r
    finally:
        store.delete_org(slug)


check("_envelope: the unknown-node early return keeps the 3-tuple shape",
      _an_unknown_node_still_returns_three_values)


# ---------------------------------------------------------------------------
print("\n§4 · the caps are documented numbers, not accidents")


def _the_caps_are_sane_relative_to_the_api():
    # 10 MB BASE64 is the vendor ceiling; base64 inflates by 4/3, so the raw
    # ceiling is ~7.5 MB. Our per-image cap must sit UNDER that with room.
    raw_ceiling = 10 * 1024 * 1024 * 3 // 4
    assert imgblock.INLINE_IMAGE_MAX_BYTES < raw_ceiling, (
        f"the per-image cap {imgblock.INLINE_IMAGE_MAX_BYTES} is at or above "
        f"the API's own raw ceiling {raw_ceiling} — an image we accept would "
        f"be rejected by the API and take the turn with it")
    # the per-turn byte cap must leave AT MOST half the 32 MB request ceiling
    # to images once base64'd, so the other half is available for the
    # conversation the images arrived in
    assert (imgblock.INLINE_IMAGE_TURN_MAX_BYTES * 4 / 3
            <= 32 * 1024 * 1024 / 2), (
        f"{imgblock.INLINE_IMAGE_TURN_MAX_BYTES} raw bytes base64s to more "
        f"than half the 32 MB request ceiling")
    # and the turn cap must be reachable — a per-turn cap below the per-image
    # cap would make the count cap unreachable and is almost certainly a typo
    assert (imgblock.INLINE_IMAGE_TURN_MAX_BYTES
            > imgblock.INLINE_IMAGE_MAX_BYTES)
    assert imgblock.INLINE_IMAGE_MAX_COUNT >= 1


check("the caps sit under the API's own ceilings and are mutually coherent",
      _the_caps_are_sane_relative_to_the_api)


# ---------------------------------------------------------------------------
print(f"\n{PASS} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
# the total line tools/run_tests.py parses — without it the tier shows this
# suite with a BLANK count, which is what a run cut halfway also looks like
print(f"\nALL {PASS} CHECKS PASS")
