#!/usr/bin/env node
/* fakecli.js — a Claude Code CLI stand-in with PROGRAMMABLE TIMING.
 *
 * WHY THIS EXISTS
 * ---------------
 * The vanishing-message bug (D-55) is a race whose window is the gap between
 * "orgtree drained the mail into a turn" and "the CLI echoed it into its
 * transcript". Against the real CLI that gap is whatever it happens to be
 * (~1 s warm, longer cold, longer sandboxed) and the race is won or lost by
 * luck — which is why five rounds of probing could not pin it down. Here the
 * gap is a NUMBER, so the danger zone can be swept and each point repeated
 * until "sometimes" becomes "always" or "never".
 *
 * The supervisor lets a substitute in on purpose: `ORGTREE_CLAUDE_CLI` points
 * `_claude_argv()` at a .js file, which it runs as `node <file>` (supervisor.py
 * :174,227). Everything below is what orgtree actually consumes from the CLI —
 * nothing more:
 *
 *   stdin   one JSON line per user event: {"type":"user","message":{…}}
 *           (also {"type":"control_request", …} for the interrupt path)
 *   stdout  {"type":"system","subtype":"init",…}        (never confirms mail)
 *           {"type":"stream_event", …}                  (token deltas, thinking)
 *           {"type":"assistant","message":{…}}          (⚠ CONFIRMS the journal)
 *           {"type":"result", …}                        (the turn boundary)
 *   file    ~/.claude/projects/<slug-of-cwd>/<session-id>.jsonl — the
 *           transcript, which is what the desk actually RENDERS from
 *
 * The two are deliberately independent here, because in orgtree they are:
 * `_confirm_delivered` fires on the first non-`system` STDOUT event, while the
 * message is rendered from the TRANSCRIPT FILE. `echoAfterFirstEvent` inverts
 * their order so the suite can ask what happens when the CLI confirms before
 * it has published.
 *
 * CONFIG
 * ------
 * A JSON file named by $FAKECLI_CONFIG, re-read on every launch (so a test can
 * change the timing between turns without restarting the backend), keyed by
 * node id with a "default" fallback — the supervisor puts $ORGTREE_NODE in the
 * child env, which is how a scenario gives two agents different behaviour:
 *
 *   { "default": { "startMs": 0, "echoMs": 800, "firstEventMs": 1200 },
 *     "slowpoke": { "startMs": 4000 } }
 *
 *   startMs             wait before the init event (process/container boot)
 *   echoMs              wait after reading a user event before writing it to
 *                       the transcript — THE D-55 WINDOW
 *   firstEventMs        wait before the first assistant-side stdout event,
 *                       which is what confirms the delivery journal
 *   echoAfterFirstEvent write the transcript record AFTER that event instead
 *   thinkMs / deltaMs   simulated thinking and token streaming
 *   tools               how many tool calls to make (each one fires the
 *                       PostToolUse steer hook, if orgtree installed one)
 *   toolMs              time per tool call
 *   replyText           the assistant's answer
 *   resultMs            wait before the result event (the turn boundary)
 *   crashAfter          exit(1) after N user events, mid-turn
 *   crashAtMs           exit(1) this many ms after the user event, on an
 *                       independent clock — before or after the transcript
 *                       write depending on how it compares to echoMs
 *   hang                never answer (the turn-timeout path)
 *   usageLimit          answer with the CLI's usage-limit error text
 *   exitAfter           close after N user events (default: on stdin EOF)
 *   bgTasks             launch N BACKGROUND subagents before the result event
 *   bgMs                how long they run after the turn's boundary
 *   bgOrphan            never finish them AND stay alive (forces the reaper's
 *                       hand — this is the user's bug: a healthy CLI holding
 *                       a working child, killed by the idle watchdog)
 *   bgQuit              never finish them and let the process exit 0 with a
 *                       child still live (the other teardown that orphans)
 *
 * BACKGROUND SUBAGENTS (user bug 2026-08-20)
 * ------------------------------------------
 * The shapes below were captured from the pinned CLI on 2026-08-20, not
 * guessed — `probe_raw.py` in the subagent-fix scratch has the raw dump. Two
 * behaviours matter and both are reproduced exactly, because the bug lives in
 * the gap between them:
 *
 *   · `background_tasks_changed` publishes the WHOLE LIVE SET on every change
 *     ({"tasks":[…]} on launch, {"tasks":[]} when the last one lands), so it
 *     is a snapshot rather than a delta and orgtree cannot drift against it.
 *   · a backgrounded child OUTLIVES the turn: its tool_result returns at
 *     once, the result event fires, orgtree closes stdin — and the real CLI
 *     does NOT exit on that EOF while a child is still running. It keeps
 *     working, delivers the completion to its own model, lets it take one
 *     more turn, and only then exits 0. So stdin EOF must not exit here
 *     either while `bgLive` is non-empty, or the very window the bug lives in
 *     would not exist in the stand-in.
 */

'use strict'
const fs = require('fs')
const os = require('os')
const path = require('path')
const { execSync } = require('child_process')

const argv = process.argv.slice(2)
if (argv.includes('--version')) { console.log('9.9.9 (fakecli)'); process.exit(0) }

function arg(name) {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null
}

const CFG_DEFAULT = {
  startMs: 0, echoMs: 600, firstEventMs: 900, echoAfterFirstEvent: false,
  thinkMs: 0, deltaMs: 0, tools: 0, toolMs: 50, resultMs: 30,
  replyText: 'ack.', crashAfter: 0, crashAtMs: 0, hang: false,
  usageLimit: false,
  exitAfter: 0,
  bgTasks: 0, bgMs: 1500, bgOrphan: false, bgQuit: false,
}
function loadCfg() {
  const p = process.env.FAKECLI_CONFIG
  let file = {}
  if (p) { try { file = JSON.parse(fs.readFileSync(p, 'utf8')) } catch (e) { file = {} } }
  const node = process.env.ORGTREE_NODE || ''
  return Object.assign({}, CFG_DEFAULT, file.default || {}, file[node] || {})
}
const cfg = loadCfg()

// --- the transcript, where the desk actually reads from ---------------------
const sid = arg('--session-id') || arg('--resume') || 'no-session'
const home = process.env.USERPROFILE || process.env.HOME || os.homedir()
const projDir = path.join(home, '.claude', 'projects',
  process.cwd().replace(/[\\/:]+/g, '-').replace(/^-+/, ''))
fs.mkdirSync(projDir, { recursive: true })
const tpath = path.join(projDir, sid + '.jsonl')

let seq = 0
function record(rec) {
  seq += 1
  if (!rec.timestamp) rec.timestamp = new Date().toISOString()
  // append + fsync: the backend reads this file from another process, and a
  // buffered write would make the transcript arrive later than the stdout
  // event that claims it exists — which is a thing this suite MEASURES, not a
  // thing it should introduce by accident
  const fd = fs.openSync(tpath, 'a')
  fs.writeSync(fd, JSON.stringify(rec) + '\n')
  fs.fsyncSync(fd)
  fs.closeSync(fd)
}
function say(obj) { process.stdout.write(JSON.stringify(obj) + '\n') }
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms | 0)))

// --- the PostToolUse steering hook orgtree installs -------------------------
// Mid-task mail rides this hook. The real CLI writes the injected context into
// the transcript as a `type:"attachment"` record, which `read_chat` cannot
// render — that shape is reproduced faithfully, because the whole question on
// the steer path is which carrier is holding the message at each instant.
let steerCmd = null
try {
  // the real CLI's contract is `--settings <file-or-json>`; orgtree parks the
  // JSON in a scratch dotfile since D-218, so honor both doors like it does
  const rawSettings = (arg('--settings') || '{}').trim()
  const s = JSON.parse(rawSettings.startsWith('{')
    ? rawSettings : fs.readFileSync(rawSettings, 'utf8'))
  const h = ((s.hooks || {}).PostToolUse || [])[0]
  steerCmd = h && h.hooks && h.hooks[0] && h.hooks[0].command
} catch (e) { steerCmd = null }

function runSteerHook() {
  if (!steerCmd) return
  let out = ''
  try {
    out = execSync(steerCmd, { input: '{"hook_event_name":"PostToolUse"}',
                               encoding: 'utf8', timeout: 8000,
                               shell: process.platform === 'win32' ? undefined : '/bin/sh' })
  } catch (e) { return }
  let ctx = null
  try { ctx = (JSON.parse(out).hookSpecificOutput || {}).additionalContext } catch (e) { return }
  if (!ctx) return
  record({ type: 'attachment', isSidechain: false,
           attachment: { type: 'hook_additional_context',
                         hookEvent: 'PostToolUse', additionalContext: ctx } })
}

// --- background subagents ---------------------------------------------------
const bgLive = new Map()          // task_id -> description (the live SET)
let stdinEnded = false
let bgHold = null                 // keeps the event loop alive under bgOrphan

function bgSnapshot() {
  say({ type: 'system', subtype: 'background_tasks_changed',
        tasks: [...bgLive.entries()].map(([task_id, description]) => (
          { task_id, task_type: 'local_agent', description })) })
}

function maybeExit() {
  // the real CLI stays up for its children and only then exits 0
  if (stdinEnded && bgLive.size === 0) process.exit(0)
}

function launchBg(i) {
  const taskId = 'bgtask' + i + '_' + Math.random().toString(16).slice(2, 8)
  const toolUseId = 'toolu_bg' + i
  const desc = 'bg subagent ' + i
  say({ type: 'assistant',
        message: { role: 'assistant', model: 'fake', usage: { input_tokens: 10 },
                   content: [{ type: 'tool_use', id: toolUseId, name: 'Agent',
                               input: { description: desc,
                                        subagent_type: 'general-purpose',
                                        run_in_background: true } }] } })
  bgLive.set(taskId, desc)
  bgSnapshot()
  say({ type: 'system', subtype: 'task_started', task_id: taskId,
        tool_use_id: toolUseId, description: desc,
        subagent_type: 'general-purpose', task_type: 'local_agent' })
  if (cfg.bgQuit) return          // launched, then let the loop drain: the
                                  // process exits 0 with the child still live
  if (cfg.bgOrphan) {
    // Never finishes — the REAPER must handle it. Returning bare is not
    // enough to model that: with no pending handle, node's event loop drains
    // at stdin EOF and this stand-in exits 0 on its own in ~1s, so the
    // watchdog never fires, `bg_idle` becomes dead config, and a check that
    // believes it is testing the reaper is really testing process exit
    // (redteam, 2026-08-21 — it was, and mutating _dog back to TURN_IDLE left
    // it passing). The real CLI does the opposite: measured 2026-08-20, a
    // background agent held its parent alive through 87s of stdout silence
    // and only then exited 0. So hold the loop open and make it be killed.
    if (!bgHold) bgHold = setInterval(() => {}, 1000)
    return
  }
  setTimeout(() => {
    if (!bgLive.has(taskId)) return
    bgLive.delete(taskId)
    bgSnapshot()                  // {"tasks":[]} once the last one lands
    say({ type: 'system', subtype: 'task_updated', task_id: taskId,
          patch: { status: 'completed' } })
    say({ type: 'system', subtype: 'task_notification', task_id: taskId,
          tool_use_id: toolUseId, status: 'completed',
          output_file: path.join(projDir, taskId + '.output'),
          summary: 'BG-DONE-' + i })
    // THE POSITIVE CONTROL. A check that only asserts "no orphan was
    // reported" passes just as well against a child that was killed and
    // whose evidence was then erased (the notice drives a turn, and a turn
    // clears last_error and drains the mailbox — measured 2026-08-20). This
    // marker is durable and can only exist if the child truly reached the
    // end, so the suite can assert the child LANDED rather than assert the
    // absence of a complaint.
    record({ type: 'assistant',
             message: { role: 'assistant', model: 'fake',
                        content: [{ type: 'text', text: 'BG-LANDED-' + i }],
                        usage: { input_tokens: 1 } } })
    maybeExit()
  }, Math.max(1, cfg.bgMs | 0) + i * 5)
}

// --- one turn ---------------------------------------------------------------
let served = 0

async function serve(text) {
  served += 1
  if (cfg.hang) return new Promise(() => {})
  // an INDEPENDENT clock, deliberately: whether the process dies before or
  // after it has written the user record to its transcript is the whole
  // question on the crash path, so the death must not be sequenced by the
  // same code that writes the record
  if (cfg.crashAtMs) setTimeout(() => process.exit(1), cfg.crashAtMs)
  if (!cfg.echoAfterFirstEvent) {
    await sleep(cfg.echoMs)
    record({ type: 'user', message: { role: 'user', content: text } })
    await sleep(Math.max(0, cfg.firstEventMs - cfg.echoMs))
  } else {
    // THE HAZARDOUS ORDERING. A non-`system` stdout event is what orgtree
    // treats as proof the CLI consumed the message (`_confirm_delivered`), so
    // one is emitted HERE — before the transcript carries anything. `echoMs`
    // then measures how far the transcript lags behind that proof, which is
    // the width of the hole if the real CLI ever does this.
    await sleep(cfg.firstEventMs)
    say({ type: 'stream_event',
          event: { type: 'content_block_delta',
                   delta: { type: 'text_delta', text: '…' } } })
    await sleep(cfg.echoMs)
  }
  if (cfg.crashAfter && served >= cfg.crashAfter) process.exit(1)
  if (cfg.thinkMs) {
    say({ type: 'stream_event',
          event: { type: 'content_block_start', content_block: { type: 'thinking' } } })
    await sleep(cfg.thinkMs)
    say({ type: 'stream_event',
          event: { type: 'content_block_delta',
                   delta: { type: 'thinking_delta', thinking: 'hmm…' } } })
  }
  if (cfg.deltaMs) {
    for (const w of cfg.replyText.split(' ')) {
      say({ type: 'stream_event',
            event: { type: 'content_block_delta',
                     delta: { type: 'text_delta', text: w + ' ' } } })
      await sleep(cfg.deltaMs)
    }
  }
  // SUBAGENT (Task) output — the shape the real CLI emits, verified against
  // cli.js: every assistant/user event carries `parent_tool_use_id`, null for
  // the agent's own output and the spawning Task's id for anything from
  // inside a subagent. The CLI's own consumer drops the non-null ones from
  // the persisted list, so the TRANSCRIPT records them as isSidechain — which
  // is what makes them unretirable live rows if the stream side forgets.
  // Deliberately BIG usage: a subagent's context is not the parent's.
  for (let i = 0; i < (cfg.subagents | 0); i += 1) {
    const pid = 'toolu_parenttask'
    const sid = 'toolu_sub' + i
    const blocks = [
      { type: 'tool_use', id: sid, name: 'Grep', input: { pattern: 'x' } },
      { type: 'text', text: 'SUBAGENT-CHATTER-' + i },
    ]
    for (const b of blocks) {
      say({ type: 'assistant', parent_tool_use_id: pid,
            message: { role: 'assistant', model: 'fake', content: [b],
                       usage: { input_tokens: 900000 } } })
      record({ type: 'assistant', isSidechain: true,
               message: { role: 'assistant', model: 'fake', content: [b],
                          usage: { input_tokens: 900000 } } })
    }
    await sleep(cfg.toolMs)
  }
  for (let i = 0; i < (cfg.tools | 0); i += 1) {
    const id = 'toolu_' + Math.random().toString(16).slice(2, 10)
    const block = { type: 'tool_use', id, name: 'Bash', input: { command: 'echo hi' } }
    say({ type: 'assistant', message: { role: 'assistant', model: 'fake',
                                        content: [block], usage: { input_tokens: 1000 } } })
    record({ type: 'assistant', message: { role: 'assistant', model: 'fake',
                                           content: [block], usage: { input_tokens: 1000 } } })
    await sleep(cfg.toolMs)
    record({ type: 'user', message: { role: 'user',
             content: [{ type: 'tool_result', tool_use_id: id, content: 'hi' }] } })
    runSteerHook()          // ← mid-task mail is delivered exactly here
  }
  if (cfg.echoAfterFirstEvent) {
    record({ type: 'user', message: { role: 'user', content: text } })
  }
  // BACKGROUND subagents — launched BEFORE the boundary, exactly as the real
  // CLI does (the Agent tool_use, then the snapshot, then task_started), so
  // orgtree sees a live child at the moment it decides to close stdin.
  for (let i = 0; i < (cfg.bgTasks | 0); i += 1) launchBg(i)
  const reply = cfg.usageLimit
    ? "You've hit your usage limit. Your limit will reset at 3pm."
    : cfg.replyText
  const msg = { role: 'assistant', model: 'fake',
                content: [{ type: 'text', text: reply }],
                usage: { input_tokens: 1200 } }
  say({ type: 'assistant', message: msg })
  record({ type: 'assistant', message: msg })
  await sleep(cfg.resultMs)
  say({ type: 'result', subtype: 'success', is_error: !!cfg.usageLimit,
        result: cfg.usageLimit ? reply : 'ok',
        usage: { input_tokens: 1200 }, total_cost_usd: 0.0001 })
  if (cfg.exitAfter && served >= cfg.exitAfter) process.exit(0)
}

// --- the stdin pump ---------------------------------------------------------
// stdin stays OPEN between turns: orgtree feeds queued messages into the SAME
// process at each result boundary, so a shim that exited after one message
// would silently convert every queued-message test into a fresh-turn test.
async function main() {
  await sleep(cfg.startMs)
  say({ type: 'system', subtype: 'init', model: 'fake', permissionMode: 'acceptEdits',
        cwd: process.cwd(), tools: ['Bash'], mcp_servers: [] })
  let buf = ''
  let chain = Promise.resolve()
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', (d) => {
    buf += d
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim()
      buf = buf.slice(i + 1)
      if (!line) continue
      let ev
      try { ev = JSON.parse(line) } catch (e) { continue }
      if (ev.type === 'control_request') {          // the ⏸ interrupt path
        say({ type: 'control_response', response: { subtype: 'success' } })
        continue
      }
      if (ev.type !== 'user') continue
      const c = ev.message && ev.message.content
      const text = typeof c === 'string' ? c
        : (c || []).map((b) => b.text || '').join('')
      chain = chain.then(() => serve(text))
    }
  })
  // stdin EOF is orgtree's TURN BOUNDARY, not a shutdown order: a CLI holding
  // a live background child keeps working through it (see the header note).
  process.stdin.on('end', () => {
    stdinEnded = true
    chain.then(maybeExit)
  })
}
main()
