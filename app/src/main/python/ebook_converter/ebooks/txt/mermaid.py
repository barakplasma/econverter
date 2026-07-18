"""Render fenced Mermaid diagrams in Markdown to local SVG images."""

import hashlib
import os
import re
import shutil
import tempfile
from xml.etree import ElementTree


_MERMAID_FENCE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})[ \t]*"
    r"(?:mermaid|\{[ \t]*\.mermaid[ \t]*\})[ \t]*\n"
    r"(?P<source>.*?)\n^(?P=indent)(?P=fence)[ \t]*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _clean_xml_chars(text):
    """Remove only characters forbidden by XML 1.0."""
    return "".join(
        char
        for char in text
        if (
            ord(char) in (0x09, 0x0A, 0x0D)
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
    )


def render_fenced_diagrams(markdown_text, base_dir, log=None):
    """Replace Mermaid code fences with validated UTF-8 SVG image references.

    Returns ``(updated_markdown, temporary_directory)``. The caller must keep
    the temporary directory alive until referenced resources have been copied,
    then remove it. Invalid diagrams are left as code blocks so one diagram
    cannot abort the whole ebook conversion.
    """
    if not _MERMAID_FENCE.search(markdown_text):
        return markdown_text, None

    from merm import render_diagram

    temp_dir = tempfile.mkdtemp(prefix=".econverter-mermaid-", dir=base_dir)
    relative_dir = os.path.basename(temp_dir)
    rendered = {}

    def replace(match):
        # Mermaid labels copied from generated documents can contain hidden NUL
        # or other XML-invalid control characters. merm preserves those in its
        # SVG text nodes, after which lxml rejects the resource. Strip only
        # characters which XML 1.0 cannot represent.
        source = _clean_xml_chars(match.group("source")).strip()
        if not source:
            return match.group(0)

        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        filename = rendered.get(digest)
        if filename is None:
            try:
                svg = render_diagram(source)
                if isinstance(svg, bytes):
                    svg = svg.decode("utf-8", "replace")
                svg = _clean_xml_chars(svg)
                # Validate before exposing the resource to the conversion
                # pipeline. This catches malformed renderer output here rather
                # than as an opaque OEB resource parsing failure later.
                ElementTree.fromstring(svg)
            except Exception as exc:
                if log is not None:
                    log.warning("Could not render Mermaid diagram: %s", exc)
                return match.group(0)

            filename = "mermaid-{}-{}.svg".format(len(rendered) + 1, digest[:10])
            with open(os.path.join(temp_dir, filename), "w", encoding="utf-8", newline="\n") as output:
                output.write(svg)
            rendered[digest] = filename

        return "\n\n![Mermaid diagram]({}/{})\n\n".format(relative_dir, filename)

    updated = _MERMAID_FENCE.sub(replace, markdown_text)
    if not rendered:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return markdown_text, None
    return updated, temp_dir
