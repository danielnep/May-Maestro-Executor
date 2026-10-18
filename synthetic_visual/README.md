# Real isolated web evidence

This fixture is synthetic and contains no client code or data. Its station profile
covers four screens, stock conservation, invalid forms, keyboard modal focus,
search/empty states, offline edits, injected synchronization errors, safe retry,
cancellation, event history and persistence after a full reload. It runs the same
interactions at 1440×960, 390×844 and 320×740 and captures twelve actual PNGs.

## GitHub Actions proof commands

```sh
docker build -t horizon-web-proof:1.58.2 synthetic_visual
python3 -m horizon_core.web_checks --root synthetic_visual/app --check node-test
python3 -m horizon_core.web_checks --root synthetic_visual/app --check browser-playwright --evidence runtime/web-evidence
```

Build dependencies before execution. The trusted image pins both Microsoft's
Playwright browser image and the npm SDK to **1.58.2**. npm is used to install a
browser SDK, not an agent CLI. Once built, every project execution uses Docker
`--network=none`, a non-root UID, dropped capabilities, a read-only project/root
filesystem, isolated shared memory and a writable temporary folder. No host ports,
network, credentials, Docker socket, or reference implementation enter the container.
Chromium's own sandbox is disabled inside this container; the container is the
execution boundary. The local HTTP server binds loopback **inside** the container.

## Integration contract

`run_web_check(root, tools, name='browser-playwright', evidence_dir=None,
 timeout=240, project_dir=None)` returns a JSON-serializable mapping.
`tools.path()` is the existing secret/path validation boundary. If `project_dir`
is supplied, it is checked with this boundary and must be an existing directory
inside the tool's workspace (for example `apps/operations-demo`). Snapshots preserve
only that app's relative layout. Capabilities remain the calling runtime's job.

Supported names are `node-test` and `browser-playwright`, with kinds `node` and
`browser`; the existing Python executor retains `python-unittest`. No shell, npm
script or project-supplied host command is accepted. Node checks discover
`tests/*.test.cjs` inside the container. Zero executed checks never passes.

The result includes `check`, `kind`, `passed`, `tests`, `exit_code`, `cleanup`,
`screenshots`, and the nested browser `report`. Each collected screenshot has an
absolute `path`, original `file`, `sha256`, `bytes` and viewport metadata. PNG
signatures, declared length and hash are checked against the browser manifest.
`manifest_path` and `evidence_path` point to copied artifacts outside the temporary
container mounts. The caller should publish these paths as workflow artifacts.
Failed checks may retain screenshots taken before the failure. Failed cleanup,
missing manifests, zero checks and unavailable Docker all return `passed=false`.

## Generic static applications

The default trusted harness serves any static app with an `index.html` at the
selected root. Without `horizon.browser.json`, it performs **smoke only** at desktop
and mobile: meaningful page, overflow, console/runtime errors, control names,
page title/language and PNG evidence. Its report explicitly records
`functional_scenarios=0`; smoke success does not certify all application flows.

Optional `horizon.browser.json` can specify declarative interactions:

```json
{
  "steps": [
    {"action":"click","target":{"role":"button","name":"New request"}},
    {"action":"fill","target":{"label":"Description"},"value":"Synthetic fixture"},
    {"action":"press","key":"Escape"},
    {"action":"expect_text","text":"Requests"}
  ]
}
```

Locators accept role+name, label, or test_id. Allowed actions are click, fill,
press, expect_text, offline (boolean value), and navigate (same-origin relative
path). No eval, arbitrary JavaScript, external URL, or shell instruction is accepted.
These steps are repeated independently at both viewports. The station fixture's
`{"profile":"station-synthetic-v1"}` selects the stronger trusted scenario suite;
it is not silently assumed for unrelated apps.

These are basic accessibility checks, not a complete WCAG/contrast audit. PNGs
prove browser rendering; a separate vision-capable model invocation is required to
claim that an agent visually interpreted them. Host unit tests use fake Docker
responses and are not represented as real screenshots or browser execution.

Official references checked: https://playwright.dev/docs/docker (matching image
and SDK versions; image alone lacks npm package), https://playwright.dev/docs/api/class-browsercontext
(offline contexts), and https://playwright.dev/docs/screenshots (PNG capture).
