# Refactor fork changes for resilience + fast CI (barakplasma/econverter)

## Context

The fork adds Markdown/Mermaid input, text-ingress sanitization (BOM-less UTF-16, NULs),
Android compat shims, and a single-job 331-line release workflow. Problems observed:

- **lxml errors** kept surfacing deep in the conversion pipeline (Mermaid SVG with
  XML-invalid chars, malformed generated HTML), and each fix cost a full push-to-master →
  emulator run (6–23 min; ~12 of the last 20 runs failed or were cancelled, plus manual
  version-bump commits).
- The conversion pipeline is **pure Python** (Chaquopy pins Python 3.11; `ebook_converter`
  is vendored) — every lxml error is reproducible on a Linux host in seconds, but there
  are no host tests. The emulator is only needed to validate Chaquopy packaging.
- The workflow hand-rolls concurrency cancellation, commit statuses, and a
  `continue-on-error` outcome ladder that separate jobs with `needs:` provide natively.

User decisions: **keep all features but make them resilient** (graceful degradation on
lxml failures, never lose content); **fast checks on every branch/PR push, emulator E2E +
prerelease APK only on master after fast checks pass**.

## Part 1 — Converter refactor (`app/src/main/python/converter.py` + helpers)

Keep the intent (sanitize all text ingress, render Mermaid, produce valid EPUB3/AZW3),
restructure into a single resilient funnel:

1. **Route Markdown through HTML input, not TXTInput.** Render `.md`/`.markdown`
   ourselves with the already-bundled `markdown` package (extensions: `extra`,
   `sane_lists`, `toc` — matches TXTInput's defaults plus tables/fenced code), wrap in the
   same lossless HTML document writer used for plain text (`_write_plain_text_html`,
   generalized to accept a pre-rendered body), and hand the HTML file to Plumber. This
   bypasses TXTInput's heuristic/auto-detection paths (source of the "silently consumed
   first paragraph" bug the fork worked around) and gives one code path for TXT and MD.
   HTMLInput picks up local `<img>` resources (Mermaid SVGs) natively, so the
   `TXTInput.fix_resources` monkeypatch can be deleted.
2. **Layered fallback (the resilience core):** if python-markdown raises, or the rendered
   HTML fails an lxml parse check (`lxml.html.document_fromstring` + XML round-trip, same
   check `_install_android_compat.parse_document` does), fall back to the escaped
   plain-text HTML path. A malformed document converts as readable plain text instead of
   failing with an lxml traceback. Log the downgrade into the returned message dict
   (add `'warnings'` key) so the Android UI / tests can surface it.
3. **Simplify `_decode_text_bytes`:** BOM check first (keep), then use the already-added
   `chardet>=7` dependency (currently installed but unused by fork code) for BOM-less
   UTF-16/other detection instead of the hand-rolled nul-ratio heuristic; fall back to
   the bundled `xml_to_unicode`. Keep `clean_xml_chars`.
4. **Stop rewriting the user's input file in place.** `_prepare_text_input` currently
   overwrites `input_path` with normalized UTF-8. Write the normalized copy into the temp
   work dir instead and pass that to Plumber. (Instrumented tests that assert on the
   rewritten input file will assert on the conversion output instead.)
5. **`ebooks/txt/mermaid.py`:** keep design (regex fence extraction, `_clean_xml_chars`,
   ElementTree validation, invalid diagrams left as code blocks). Move it to a
   non-vendored location `app/src/main/python/econverter_text.py` (or similar) along with
   the funnel helpers so fork code is cleanly separated from vendored calibre code; keep
   dedup-by-digest and per-conversion temp dir cleanup.
6. **`_install_android_compat`:** keep (html5_parse substitute, `Container.opf_xpath`,
   toc `base` proxy) minus the now-unneeded `fix_resources` patch; make it idempotent and
   importable on host CI (no Android-only imports) so host tests exercise the exact
   Android code path. Vendored additions (`db/write.py`, `oeb/polish/cover.py`,
   `oeb/polish/upgrade.py`, `utils/ipc/simple_worker.py`, `ebooks/markdown/__init__.py`)
   stay as-is — they mirror calibre APIs and are fine.

## Part 2 — Host-side Python test suite (the cycle-time core)

New `tests/python/` at repo root + `tests/python/requirements.txt` that mirrors the
Chaquopy pip list from `app/build.gradle.kts` exactly (beautifulsoup4, chardet,
css-parser, filelock, html2text, lxml, markdown, merm==0.1.5, odfpy, pillow,
python-dateutil, reportlab, svglib, tinycss) — deliberately **without** html5-parser so
the Android compat patches are exercised. `conftest.py` adds `app/src/main/python` to
`sys.path`.

Tests (pytest, Python 3.11):
- **Unit:** `_decode_text_bytes` (all BOMs, BOM-less UTF-16LE/BE, NULs, latin-1-ish
  bytes), Mermaid fence regex (tilde fences, indented, `{.mermaid}`, nested fences,
  invalid diagram → left as code block), fallback trigger (broken markdown → plain path).
- **End-to-end via `converter.convert()`:** port the three instrumented scenarios
  (UTF-8 MD + Mermaid + NUL → EPUB3 with valid sanitized SVG; BOM-less UTF-16LE MD →
  AZW3 with `BOOKMOBI` signature; UTF-16LE TXT + NUL → EPUB with content preserved), plus
  a small corpus of nastier real docs: repo `README.md`, tables/footnotes/nested lists,
  raw HTML in markdown, RTL text, image refs to missing files, huge single paragraph.
  Assert content preservation by unzipping the EPUB (reuse assertion helpers).
- These reproduce the historical lxml failures locally: `pip install -r
  tests/python/requirements.txt && pytest tests/python` — no emulator, no push.

**Slim the instrumented test** (`MarkdownConversionInstrumentedTest.kt`) to what only the
device can prove: Python starts under Chaquopy, one MD→EPUB3 and one MD→AZW3 conversion
succeed with content present. Deep assertions live in the host suite. Drop the
`--debug-pipeline` debugging scaffolding from the test (keep support in converter.py).

## Part 3 — Workflow refactor

Replace `markdown-test-release.yml` with a staged `ci.yml` (delete the old file; leave
upstream's `release.yml` tag-driven workflow untouched):

```
on: push (all branches), pull_request, workflow_dispatch
concurrency: group ci-${{ github.ref }}, cancel-in-progress: true

jobs:
  fast-checks:            # every push/PR, ~2–3 min
    - setup-python 3.11 (pip cache keyed on tests/python/requirements.txt)
    - rumdl==0.2.34 check (keep .rumdl.toml MD070 gate)
    - pytest tests/python  (replaces the inline "smoke" heredoc)
  android-e2e:            # needs: fast-checks; if master push or dispatch
    - KVM enable + AVD snapshot cache (keep existing restore/create/save pattern,
      api 36 / pixel_7_pro key)
    - connectedDebugAndroidTest (slimmed test class); upload reports+logcat on failure
  release:                # needs: android-e2e; master only
    - generate throwaway signing key, assembleRelease, verify, rename
    - publish prerelease with existing body text (updated), upload APK artifact
```

Deletions (behavior now native): "Cancel superseded workflow runs" step (concurrency
covers it), "Mark build pending"/"Report workflow status" `gh api` steps (per-job commit
statuses are automatic), the entire `continue-on-error` + outcome-ladder + final
`exit 1` construction (`needs:` gives the same gating with readable per-job UI).

**Kill manual version-bump commits:** `app/build.gradle.kts` derives version from CI —
`versionName = "1.0.6-markdown.${System.getenv("VERSION_SUFFIX") ?: "dev"}"`,
`versionCode = base + (System.getenv("VERSION_CODE_OFFSET") ?: "0").toInt()` — with the
release job passing `github.run_number`. Release tag becomes
`v1.0.6-markdown.r${run_number}` (no more editing gradle + workflow YAML in lockstep for
every release).

Gradle: keep `setup-java` with `cache: gradle`; only the android jobs need Java.

## Files touched

- `app/src/main/python/converter.py` — rewrite funnel (major)
- `app/src/main/python/econverter_text.py` — new (mermaid + decode + HTML writer moved here)
- `app/src/main/python/ebook_converter/ebooks/txt/mermaid.py` — removed (moved)
- `tests/python/{conftest.py,requirements.txt,test_*.py}` — new
- `app/src/androidTest/.../MarkdownConversionInstrumentedTest.kt` — slimmed
- `app/build.gradle.kts` — env-derived version, no dependency changes
- `.github/workflows/ci.yml` — new; `.github/workflows/markdown-test-release.yml` — deleted
- `README.md` fork section note about running host tests locally (small)

## Verification

1. `pip install -r tests/python/requirements.txt && pytest tests/python` locally in this
   container (Python 3.11) — full conversion suite must pass, including EPUB3 SVG and
   AZW3 BOOKMOBI checks. This directly exercises the refactored funnel end-to-end.
2. Inject a deliberately broken markdown file in a test to confirm the plain-text
   fallback produces readable output rather than an lxml traceback.
3. `./gradlew assembleDebug` compiles (Kotlin test slimming + gradle version wiring).
4. Push branch `claude/econverter-refactor-actions-u2220z`; `fast-checks` job must go
   green on the branch push (proves the new fast path). Emulator E2E runs on master
   later per design — draft PR notes this.
5. Draft PR against fork master with summary of behavior changes.
