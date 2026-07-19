# AGENTS.md — working guide for AI agents and new contributors

This file explains how this repository is put together, what this fork changes
relative to upstream, how to test conversion changes **without an emulator or
a device**, and the sharp edges that have repeatedly cost hours. Read it fully
before touching conversion code or CI.

## What this project is

eConverter is an offline Android ebook converter. It embeds a trimmed fork of
Calibre's conversion pipeline (`ebook-converter`, a pure-Python package) inside
an Android app via [Chaquopy](https://chaquo.com/chaquopy/), with a small
Kotlin/Compose UI on top. Conversion happens fully on-device; there is no
server component.

- Upstream: <https://github.com/bilec/econverter>
- This fork (`barakplasma/econverter`) adds Markdown input, Mermaid diagram
  rendering, and hardened text ingress (encoding detection + sanitization),
  targeted at converting Markdown notes to AZW3 for sideloading onto a Kindle
  and EPUB for other readers. Primary manual test device: a Pixel 10.

## Repository map

| Path | What it is |
|---|---|
| `app/src/main/java/com/econverter/app/` | Kotlin UI (Compose). `ConverterViewModel.kt` holds the allowed input format list and calls Python. |
| `app/src/main/python/converter.py` | **The entry point the app calls.** `convert(input_path, output_path, *cli_args)` → `{'success', 'message', 'warnings'}`. Also installs Android compat monkeypatches. |
| `app/src/main/python/econverter_text.py` | **Fork-owned text ingress funnel** (decode, sanitize, Mermaid→SVG, Markdown→HTML, plain-text fallback). Not vendored code — edit freely. |
| `app/src/main/python/ebook_converter/` | **Vendored** Calibre-derived conversion package (~4.12 era). Treat as third-party: minimal, surgical changes only, each one documented in this file's "Vendored changes" section. |
| `app/src/androidTest/.../MarkdownConversionInstrumentedTest.kt` | Thin on-device packaging test (Chaquopy boots, conversions succeed). Deep behavior belongs in host tests. |
| `tests/python/` | **Host-side pytest suite.** Runs the full conversion pipeline on desktop Linux/macOS. This is where conversion behavior is specified and where you iterate. |
| `.github/workflows/ci.yml` | Staged CI: fast lint+pytest on every push; emulator E2E and prerelease APK only on master. |
| `.github/workflows/release.yml` | Upstream's tag-driven signed release flow (untouched by the fork). |
| `.rumdl.toml` | Markdown lint config for repo-maintained docs (structural rule MD070 only). |

## The one architectural rule

**The conversion pipeline is pure Python.** Chaquopy pins CPython 3.11 and
every pip dependency in `app/build.gradle.kts` has wheels for desktop too.
Therefore any conversion bug — including every lxml error ever hit — is
reproducible on a laptop in seconds:

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r tests/python/requirements.txt
pytest tests/python
```

Do **not** debug conversion logic through the Android emulator or by pushing
to CI. Reproduce it in `tests/python/` first, fix it, then let CI confirm
packaging. The emulator exists to prove Chaquopy packaging, nothing else.

`tests/python/requirements.txt` must mirror the Chaquopy `pip { install(...) }`
block in `app/build.gradle.kts` exactly — if you change one, change the other
in the same commit. `html5-parser` is deliberately absent from both: it has no
Android wheel, and its absence forces the compat code paths the app really uses.

## Text ingress design (`econverter_text.py`)

All text-like inputs (`.txt`, `.text`, `.md`, `.markdown`, `.textile`) pass
through `prepare_text_input()` before Calibre's Plumber sees them:

1. **Decode** (`decode_text_bytes`): BOM check → conservative NUL-pattern
   heuristic for BOM-less UTF-16LE/BE → chardet for 8-bit/UTF-8 → bundled
   `xml_to_unicode` fallback. Output is always Unicode with XML-invalid
   control characters removed (`clean_xml_chars`).
2. **Paragraph guard** (`split_overlong_paragraphs`): paragraphs over 32 KB
   get whitespace-boundary breaks inserted. EPUB output aborts with
   `SplitError` on paragraphs it cannot split; this makes that impossible.
3. **Markdown route**: Mermaid fences are rendered to sanitized, validated
   SVG files (`render_fenced_diagrams`; invalid diagrams stay as code
   blocks); then python-markdown renders to HTML (`extra`, `sane_lists`,
   `toc` extensions); the result is wrapped in a minimal HTML document next
   to the SVGs and handed to Plumber as **HTML input**, bypassing the
   TXT plugin entirely.
4. **Plain-text route**: content is escaped into `<p>`/`<br/>` markup —
   lossless by construction — and also handed over as HTML input.
5. **Fallback ladder**: if Markdown rendering raises, or the rendered HTML
   fails the same lxml parse the pipeline applies later
   (`validate_html_document`), the input is converted via the plain-text
   route instead. Content is never lost and lxml never aborts a conversion;
   each downgrade appends a human-readable string to the result's
   `'warnings'` list.
6. **Escape hatch**: passing `--formatting-type <x>` (or `.textile` input)
   routes a normalized UTF-8 temp copy through Calibre's TXT plugin instead,
   opting into its processors.

The user's input file is **never modified**. All intermediates live in a
`.econverter-text-*` temp dir beside the input, removed in `convert()`'s
`finally`.

Rationale notes (hard-won, do not "simplify" these away):

- **chardet cannot be trusted for UTF-16.** chardet 7 reports UTF-16LE at
  0.95 confidence for plain 8-bit text containing a couple of stray NUL/control
  bytes (decoding it would yield CJK garbage). Real BOM-less UTF-16 with any
  ASCII in it shows NUL high bytes on a consistent side; `_bomless_utf16_encoding`
  requires that evidence. chardet is only consulted for 8-bit vs UTF-8 calls,
  and its UTF-16/32 guesses are explicitly ignored.
- **Mermaid SVG must be sanitized and validated before Plumber sees it.**
  `merm` preserves XML-invalid control characters from diagram labels into
  SVG text nodes; lxml then rejects the resource deep inside OEB processing
  with an opaque error. `render_fenced_diagrams` strips those characters and
  round-trips the SVG through `ElementTree.fromstring` first.

## Android compat monkeypatches (`converter._install_android_compat`)

Applied at every `convert()` call; all idempotent; all exercised by host tests
because the host venv mirrors Android's missing packages:

- `parse_utils.html5_parse` → lxml-based parser (no `html5-parser` wheel on
  Android).
- `TXTInput.fix_resources` → lxml-based version (same reason; only reachable
  via the `--formatting-type` escape hatch).
- `Container.opf_xpath` → namespace-aware xpath used by the EPUB 3 upgrade
  path.
- `toc_module.base` → callable proxy for a legacy `base(ns, tag)` call shape.

Vendored *additions* that fill gaps for EPUB 3 output on Android:
`ebook_converter/db/write.py`, `ebooks/oeb/polish/cover.py`,
`ebooks/oeb/polish/upgrade.py`, `utils/ipc/simple_worker.py` (in-process
replacement for Calibre's fork-based worker — Android cannot fork worker
processes), `ebooks/markdown/__init__.py` (shim to the pip `markdown`
package).

## Vendored changes (`ebook_converter/`) — the full list

Keep this list current; it is the audit trail for divergence from the vendored
snapshot.

1. `ebooks/conversion/plumber.py` — fixed a broken stdlib-logging call
   (`log.info(' %s', name, repr(val))` — two args, one placeholder) that
   spammed "--- Logging error ---" and crashes under handlers that re-raise.
2. `ebooks/txt/processor.py` — `split_string_separator` upstream bug: it
   reassigned `txt = []` before iterating `range(0, len(txt), size)`, so any
   paragraph longer than the chunk size was silently replaced with **empty
   bytes** (this was the true cause of "the TXT plugin eats the first
   paragraph"); it also duplicated the split character. `split_txt`'s chunk
   math treated the average chunk size as a per-paragraph limit, mangling
   small documents. Both fixed to only break genuinely oversized paragraphs
   and never drop content.
3. Additions listed in the compat section above (new files only).

When you find a new vendored bug: fix it minimally, add a host test that
fails without the fix, and append it to this list in the same commit.

## Testing

- `pytest tests/python` — the source of truth for conversion behavior.
  - `test_text_ingress.py`: unit tests for decode/Mermaid/fallback/splitting.
  - `test_convert_e2e.py`: real conversions through `converter.convert()`:
    MD+Mermaid+NUL→EPUB3 (validated SVG inside), BOM-less UTF-16LE MD→AZW3
    (`BOOKMOBI` signature), UTF-16LE TXT→EPUB, plus a nasty-Markdown corpus
    (raw HTML, unclosed tags, RTL, huge paragraphs, control characters...).
    Add a corpus case for every new real-world document that misbehaves.
- `./gradlew connectedDebugAndroidTest` — on-device packaging proof; runs in
  CI on an API 36 emulator with a cached AVD snapshot. Keep these tests thin.
- Kotlin formatting: `./gradlew spotlessCheck` / `spotlessApply`.

## CI (`.github/workflows/ci.yml`)

Staged jobs, native GitHub gating (`needs:`), no `continue-on-error` ladders,
no manual `gh api` status posting, no manual run cancellation (a concurrency
group with `cancel-in-progress` handles supersession):

1. **fast-checks** — every push and PR: rumdl structural Markdown lint +
   `pytest tests/python` on CPython 3.11. Target: ~2–3 minutes. This is the
   job that should catch essentially all conversion regressions.
2. **android-e2e** — master pushes and manual dispatch only, after
   fast-checks: KVM emulator, cached AVD snapshot, slim instrumented tests.
3. **release** — master only, after android-e2e: builds a test-key-signed
   release APK and publishes it as a prerelease with tag
   `v<base>-markdown.r<run_number>`.

Versioning: `app/build.gradle.kts` reads `VERSION_SUFFIX` and
`VERSION_CODE_OFFSET` from the environment (CI passes the run number).
**Never hand-bump versionCode/versionName for a test release** — that
workflow-and-gradle-in-lockstep dance is what the env-var scheme replaced.

Iteration protocol for conversion changes: branch → `pytest tests/python`
locally → push branch (fast-checks only) → merge/push to master when green →
CI produces the installable prerelease APK for on-device (Pixel 10) checks.

## Sharp edges / gotchas

- **Don't add pip packages with native code without checking Chaquopy has a
  wheel** for all three ABIs in `abiFilters`. Pure-Python packages are safe.
- **`extractPackages("ebook_converter")`** in the Chaquopy block is required —
  the package reads its own data files from disk.
- **PDF input/output stays excluded** (needs poppler/PyQt5; unavailable on
  Android).
- The vendored logger (`ebook_converter/logging.py`) formats with `%` args;
  passing extra args crashes noisy handlers. Use `log.info('%s: %s', a, b)`
  shapes only.
- EPUB output enforces ~260 KB flows; unsplittable content raises
  `SplitError`. The ingress funnel pre-splits, but any *new* input route must
  do the same or route through `prepare_text_input`.
- `merm==0.1.5` is pinned: its SVG output shape is what the sanitizer and
  tests were written against.
- Emulator CI: API 36 needs KVM (`/dev/kvm` udev rule) and boots far faster
  from the cached AVD snapshot (`avd-v1-*` cache key). If emulator config
  changes (API level, profile, arch), bump the cache key version.

## Conventions

- Fork-owned Python lives outside `ebook_converter/`; vendored style is not a
  precedent for new code. Follow PEP 8, single quotes preferred in
  `converter.py`/`econverter_text.py`, double quotes fine where files already
  use them.
- Result contract with the Kotlin layer: `{'success': bool, 'message': str,
  'warnings': list[str]}` — additive changes only; the UI reads these keys.
- Commit messages: imperative summary line; explain *why* in the body when
  touching vendored code or CI.
- Plans and design notes for substantial changes live in `docs/plans/`.
