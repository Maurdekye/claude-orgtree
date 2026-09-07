// frontend/tests/containment/alloc.probe.mjs - the containment suite's planted
// allocator. NOT a test file (no .test. in the name, so run.mjs never bundles
// it): containment.test.ts launches it under tests/joblimit.ps1 to prove the
// memory ceiling actually kills and the absence of one actually lets it finish.
//
//   node alloc.probe.mjs <mb>          commit <mb> of ArrayBuffer in 32 MB
//                                      chunks, touch every page so the commit
//                                      is real, print "held <mb> MB", exit 0
//   node alloc.probe.mjs sleep <ms>    sit for <ms> then exit 0 (run-limit probe)
//
// ArrayBuffer on purpose: it is EXTERNAL to V8's heap, the memory class
// --max-old-space-size cannot bound and the one the 2026-08-29 incident was
// made of (D-177).
const [mode, arg] = process.argv.slice(2)
if (mode === 'sleep') {
  setTimeout(() => { console.log('slept'); process.exit(0) }, Number(arg))
} else {
  const mb = Number(mode)
  const held = []
  const CHUNK = 32
  for (let done = 0; done < mb; done += CHUNK) {
    const buf = new Uint8Array(CHUNK * 1024 * 1024)
    for (let i = 0; i < buf.length; i += 4096) buf[i] = 1
    held.push(buf)
  }
  console.log(`held ${held.length * CHUNK} MB`)
  process.exit(0)
}
