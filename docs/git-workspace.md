# Git repository workspace

Open Git beside the global docket button, or from an agent desk or docket item.
Register an existing repository with Add repository. Scan subfolders checks at
most two directory levels and 200 directories inside the org's known roots;
it does not initialize or clone repositories. Registration is host-operator
only. Public visitors and bridge requests receive no Git data.

Saved repositories load separately from automatic discovery of known folder
roots. Discovery runs once when the workspace opens; its progress, results or
failure are shown separately, so it does not hold up the graph or Add repository.
Opening Discovery results shows those candidates. Scan subfolders keeps the
same two-level, 200-directory limit and does not block the current graph.

Select a trunk and remote in Repository settings when no unambiguous suggestion
exists. A saved selection stays selected if its ref or remote disappears. Link
branches to docket items there; links are many-to-many and repository-qualified.
The annotation shows branch, ticket slugs and docket assignees with model icons.
Historical owners retain their original generation's icon and navigation target
when that generation still exists. Commit authors never establish ownership.

Hover for commit messages or checkout state. Staged, unstaged and untracked file
and line amounts stay separate; binary/unknown amounts are explicit. Several
checkouts at the same commit keep independent state. Ticket and agent names
open their existing application targets directly.

Click an unpushed or incoming commit for a nearby Push or Pull button. The click
chooses a branch, and the action always operates on its full outstanding history.
Push is non-forced; Pull only fast-forwards an already checked-out branch. Pull
requires all tracked and untracked paths to be clean and names blocking paths.
It never switches branches, stashes, resets or resolves conflicts. If a branch
has multiple checkouts, select its Pull checkout in Branches and history.
Configured Git hooks run normally. Failed or timed-out operations report the
observed result, including a checkout that moved before a hook failed.

Fetch every 30s is initially off. The backend persists the setting and coalesces
jobs across tabs, orgs and sibling worktrees; it never automatically pulls.
Freshness separates not watched, not yet observed, fresh, stale and failing,
with fetching layered over those facts. Failure keeps the last successful
observation age. External Git fetches are not claimed as service observations.

The graph preserves parent edges and shared commit identity. Drag both axes;
horizontal release recenters the trunk only when the content fits. Pinning uses
the normal application window controls. Branches and history exposes inactive
branches deliberately. Loaded commits keep fixed coordinates as older pages
arrive, and the viewport mounts only nearby nodes and edges.

Resource bounds: 40 branch lanes and 60 checkout statuses per snapshot, with
omitted counts shown. A snapshot captures at most 100,000 reachable commit OIDs
and parent relationships; exceeding that bound returns an explicit error.
Commit messages/details load 120 at a time, with selected old tips also present
in the first page. The snapshot expires after ten minutes; refresh creates a
new one. Counts against trunk/upstream cover full reachable history and are not
derived from loaded pages. Status returns at most 2,000 paths and marks larger
scans incomplete. Untracked line reads are bounded to 1 MiB per file and 4 MiB
per scan. Unsupported transports/refspecs fail with a reason. No webhook,
conflict editor, arbitrary Git console or repository deletion is provided.

The machine registry is a separate atomic `git-workspace.json` under
`ORGTREE_DATA`; it works alongside both SQLite and legacy JSON org storage.
Removing a registration removes that org's links and leaves repository files
untouched. Git APIs are in `gitapi.py`, repository behavior in `gitworkspace.py`,
and the bounded subprocess boundary in `gitrunner.py`.

Validation uses only temporary repositories and local bare remotes. The Python
fixture establishes and asserts fresh `ORGTREE_DATA`, HOME and Git config before
any application import. Run `backend/tests/test_git_workspace.py`; for the JSON
compatibility checks set `ORGTREE_STORE=json` before starting Python. Frontend
checks are `npm run typecheck`, `node tests/run.mjs gitworkspace`, and
`npm run build`. Build the browser fixture with
`node tests/gitworkspace_browser_bundle.mjs`, then run
`tests/gitworkspace_browser_probe.py --out-dir <evidence-dir>` with the project
Python environment. `--browser <installed-chromium-executable>` selects an
installed Chromium when Playwright's default revision is unavailable. Every
browser request is intercepted into the temporary API fixture; an intentional
forbidden request proves that interception is active.
