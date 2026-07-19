"""Unit tests for the text ingress helpers (decode, Mermaid, fallbacks)."""

import codecs
import os
import re
from xml.etree import ElementTree

import pytest

import econverter_text as et


HEBREW = "שלום — café"


class TestDecodeTextBytes:
    def test_empty(self):
        assert et.decode_text_bytes(b"") == ""

    @pytest.mark.parametrize(
        "bom,encoding",
        [
            (codecs.BOM_UTF8, "utf-8"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
            (codecs.BOM_UTF32_LE, "utf-32-le"),
            (codecs.BOM_UTF32_BE, "utf-32-be"),
        ],
    )
    def test_bom_encodings(self, bom, encoding):
        raw = bom + f"# Title\n\n{HEBREW}\n".encode(encoding)
        assert et.decode_text_bytes(raw) == f"# Title\n\n{HEBREW}\n"

    def test_bomless_utf16le(self):
        raw = f"# Title\n\n{HEBREW} and English text\n".encode("utf-16-le")
        assert HEBREW in et.decode_text_bytes(raw)

    def test_bomless_utf16be(self):
        raw = f"# Title\n\n{HEBREW} and English text\n".encode("utf-16-be")
        assert HEBREW in et.decode_text_bytes(raw)

    def test_plain_utf8(self):
        assert et.decode_text_bytes(f"{HEBREW}\n".encode()) == f"{HEBREW}\n"

    def test_strips_xml_invalid_controls(self):
        text = et.decode_text_bytes("a\x00b\x08c\ttab\nkeeps\rok".encode())
        assert text == "abc\ttab\nkeeps\rok"

    def test_arbitrary_bytes_do_not_raise(self):
        text = et.decode_text_bytes(bytes(range(256)) * 4)
        assert "\x00" not in text

    def test_stray_nuls_do_not_trigger_utf16(self):
        raw = "word \x00 normal utf8 with stray nuls \x00\x00 mixed in".encode()
        assert "normal utf8" in et.decode_text_bytes(raw)


class TestSplitOverlongParagraphs:
    def test_short_text_untouched(self):
        text = "para one\n\npara two\n"
        assert et.split_overlong_paragraphs(text) == text

    def test_long_paragraph_gets_breaks(self):
        text = "word " * 3000
        result = et.split_overlong_paragraphs(text, limit=1000)
        for paragraph in re.split(r"\n[ \t]*\n+", result):
            assert len(paragraph) <= 1000
        assert result.replace("\n\n", " ").split() == text.split()

    def test_unbreakable_run_is_hard_cut(self):
        text = "x" * 5000
        result = et.split_overlong_paragraphs(text, limit=1000)
        assert result.replace("\n\n", "") == text


class TestMermaid:
    def test_no_fence_is_untouched(self, tmp_path):
        source = "# Title\n\n```python\nprint(1)\n```\n"
        assert et.render_fenced_diagrams(source, str(tmp_path)) == source
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "fence_open,fence_close",
        [
            ("```mermaid", "```"),
            ("~~~mermaid", "~~~"),
            ("````MERMAID", "````"),
            ("```{ .mermaid }", "```"),
        ],
    )
    def test_fence_variants_render(self, tmp_path, fence_open, fence_close):
        source = f"{fence_open}\nflowchart LR\n    A --> B\n{fence_close}\n"
        result = et.render_fenced_diagrams(source, str(tmp_path))
        assert "![Mermaid diagram](mermaid-" in result
        svgs = list(tmp_path.glob("*.svg"))
        assert len(svgs) == 1
        ElementTree.fromstring(svgs[0].read_text(encoding="utf-8"))

    def test_nul_in_label_is_sanitized(self, tmp_path):
        source = '```mermaid\nflowchart LR\n    A["Hidden\x00control"] --> B\n```\n'
        result = et.render_fenced_diagrams(source, str(tmp_path))
        assert "![Mermaid diagram]" in result
        svg = next(tmp_path.glob("*.svg")).read_text(encoding="utf-8")
        assert "\x00" not in svg
        ElementTree.fromstring(svg)

    def test_invalid_diagram_left_as_code_block(self, tmp_path):
        source = "```mermaid\nthis is not a diagram at all {{{\n```\n"
        result = et.render_fenced_diagrams(source, str(tmp_path))
        assert result == source
        assert list(tmp_path.glob("*.svg")) == []

    def test_duplicate_diagrams_rendered_once(self, tmp_path):
        block = "```mermaid\nflowchart LR\n    A --> B\n```\n"
        result = et.render_fenced_diagrams(block + "\n" + block, str(tmp_path))
        assert result.count("![Mermaid diagram]") == 2
        assert len(list(tmp_path.glob("*.svg"))) == 1


class TestResolveLocalResources:
    def _doc(self, body):
        return et.build_html_document("t", body)

    def test_relative_image_rewritten_to_input_dir(self, tmp_path):
        input_dir = tmp_path / "notes"
        work_dir = tmp_path / "work"
        input_dir.mkdir()
        work_dir.mkdir()
        (input_dir / "cover.png").write_bytes(b"\x89PNG")
        out = et.resolve_local_resources(
            self._doc('<p><img src="cover.png"/></p>'), str(input_dir), str(work_dir)
        )
        assert os.path.abspath(str(input_dir / "cover.png")) in out

    def test_workdir_mermaid_reference_untouched(self, tmp_path):
        input_dir = tmp_path / "notes"
        work_dir = tmp_path / "work"
        input_dir.mkdir()
        work_dir.mkdir()
        (work_dir / "mermaid-1-abc.svg").write_text("<svg/>")
        out = et.resolve_local_resources(
            self._doc('<p><img src="mermaid-1-abc.svg"/></p>'),
            str(input_dir),
            str(work_dir),
        )
        assert 'src="mermaid-1-abc.svg"' in out

    def test_missing_file_and_hyperlink_untouched(self, tmp_path):
        input_dir = tmp_path / "notes"
        work_dir = tmp_path / "work"
        input_dir.mkdir()
        work_dir.mkdir()
        (input_dir / "there.png").write_bytes(b"x")
        body = '<p><a href="there.png">link</a><img src="gone.png"/></p>'
        out = et.resolve_local_resources(self._doc(body), str(input_dir), str(work_dir))
        assert out == self._doc(body)  # nothing embeddable was rewritten

    def test_absolute_and_remote_urls_untouched(self, tmp_path):
        out = et.resolve_local_resources(
            self._doc('<p><img src="https://x/y.png"/><img src="/etc/passwd"/></p>'),
            str(tmp_path),
            str(tmp_path),
        )
        assert "https://x/y.png" in out and "/etc/passwd" in out


class TestPrepareTextInput:
    def test_non_text_passthrough(self, tmp_path):
        path = tmp_path / "book.epub"
        path.write_bytes(b"not text")
        prepared, temp_dirs, warnings = et.prepare_text_input(str(path))
        assert prepared == str(path)
        assert temp_dirs == [] and warnings == []

    def test_markdown_becomes_html(self, tmp_path):
        path = tmp_path / "doc.md"
        path.write_text("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        prepared, temp_dirs, warnings = et.prepare_text_input(str(path))
        assert prepared.endswith("index.html")
        assert warnings == []
        html = open(prepared, encoding="utf-8").read()
        assert "<h1" in html and "<table>" in html
        # Input file is never modified.
        assert path.read_text().startswith("# Title")

    def test_plain_text_is_escaped_not_interpreted(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("First <paragraph> & text.\nsecond line\n\nSecond paragraph.")
        prepared, _dirs, warnings = et.prepare_text_input(str(path))
        html = open(prepared, encoding="utf-8").read()
        assert "First &lt;paragraph&gt; &amp; text." in html
        assert "<br/>" in html and html.count("<p>") == 2
        assert warnings == []

    def test_markdown_failure_falls_back_to_plain(self, tmp_path, monkeypatch):
        def boom(text):
            raise ValueError("renderer exploded")

        monkeypatch.setattr(et, "markdown_body", boom)
        path = tmp_path / "doc.md"
        path.write_text("# Title\n\ncontent stays")
        prepared, _dirs, warnings = et.prepare_text_input(str(path))
        html = open(prepared, encoding="utf-8").read()
        assert "# Title" in html and "content stays" in html
        assert len(warnings) == 1 and "plain text" in warnings[0]

    def test_invalid_rendered_html_falls_back_to_plain(self, tmp_path, monkeypatch):
        calls = []

        def flaky_validate(document):
            calls.append(document)
            if len(calls) == 1:
                raise ValueError("lxml said no")

        monkeypatch.setattr(et, "validate_html_document", flaky_validate)
        path = tmp_path / "doc.md"
        path.write_text("# Title\n\ncontent stays")
        prepared, _dirs, warnings = et.prepare_text_input(str(path))
        html = open(prepared, encoding="utf-8").read()
        assert "content stays" in html
        assert len(warnings) == 1 and "validation" in warnings[0]

    def test_force_txt_plugin_only_normalizes(self, tmp_path):
        path = tmp_path / "doc.md"
        path.write_bytes("# Raw markdown stays\n".encode("utf-16-le"))
        prepared, _dirs, warnings = et.prepare_text_input(
            str(path), force_txt_plugin=True
        )
        assert prepared.endswith("doc.md") and prepared != str(path)
        assert open(prepared, encoding="utf-8").read() == "# Raw markdown stays\n"
        assert warnings == []
