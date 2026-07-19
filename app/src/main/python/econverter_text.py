"""Text ingress pipeline for eConverter's fork-added formats.

Fork code, deliberately kept outside the vendored ``ebook_converter`` package.
Everything text-like (``.txt``, ``.text``, ``.md``, ``.markdown``) is funneled
through one path: decode bytes to Unicode, strip characters XML 1.0 cannot
represent, render Mermaid fences to local SVG, render Markdown to HTML, and
fall back to a lossless escaped-HTML document whenever a stage fails. The
conversion may lose formatting on malformed input, but never content and never
with an lxml traceback.
"""

import codecs
import hashlib
import html
import os
import re
import tempfile


MARKDOWN_EXTENSIONS = {'.md', '.markdown'}
PLAIN_TEXT_EXTENSIONS = {'.txt', '.text'}
TEXT_EXTENSIONS = MARKDOWN_EXTENSIONS | PLAIN_TEXT_EXTENSIONS | {'.textile'}

# Matches TXTInput's default Markdown feature set ('footnotes, tables, toc')
# plus the rest of 'extra' (fenced code, definition lists, attributes).
PYTHON_MARKDOWN_EXTENSIONS = ['extra', 'sane_lists', 'toc']

_BOM_ENCODINGS = (
    (codecs.BOM_UTF32_LE, 'utf-32'),
    (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF16_LE, 'utf-16'),
    (codecs.BOM_UTF16_BE, 'utf-16'),
    (codecs.BOM_UTF8, 'utf-8-sig'),
)

_MERMAID_FENCE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})[ \t]*"
    r"(?:mermaid|\{[ \t]*\.mermaid[ \t]*\})[ \t]*\n"
    r"(?P<source>.*?)\n^(?P=indent)(?P=fence)[ \t]*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


# Complement of the XML 1.0 legal character set. A precompiled regex pushes the
# per-character filtering into CIL/C instead of a Python generator, which
# matters for large documents on the phone's Chaquopy interpreter.
_XML_INVALID_RE = re.compile(
    '[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def clean_xml_chars(text):
    """Remove only characters forbidden by XML 1.0."""
    return _XML_INVALID_RE.sub('', text)


def _bomless_utf16_encoding(raw):
    """Detect BOM-less UTF-16 through NUL-byte distribution.

    chardet reports UTF-16 with the same 0.95 confidence for genuine UTF-16
    and for 8-bit text containing stray control bytes (decoding the latter
    produces CJK garbage), so it cannot be trusted for this case. ASCII code
    units in real UTF-16 put NUL high bytes consistently on one side; requiring
    that pattern keeps false positives out.
    """
    sample = raw[:64 * 1024]
    pairs = max(1, len(sample) // 2)
    even_nuls = sample[0::2].count(0)
    odd_nuls = sample[1::2].count(0)
    if odd_nuls >= 4 and odd_nuls / pairs >= 0.15 and odd_nuls >= even_nuls * 2:
        return 'utf-16-le'
    if even_nuls >= 4 and even_nuls / pairs >= 0.15 and even_nuls >= odd_nuls * 2:
        return 'utf-16-be'
    return None


def decode_text_bytes(raw):
    """Decode text-like bytes and remove characters XML cannot represent."""
    if not raw:
        return ''

    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            return clean_xml_chars(raw.decode(encoding, 'replace'))

    utf16 = _bomless_utf16_encoding(raw)
    if utf16 is not None:
        return clean_xml_chars(raw.decode(utf16, 'replace'))

    # 8-bit input: chardet is good at telling UTF-8 from legacy codepages.
    # UTF-16/32 guesses are ignored here — the NUL heuristic above already
    # had its chance, and chardet's multi-byte guesses are overconfident.
    try:
        import chardet
        guess = chardet.detect(raw[:64 * 1024])
    except Exception:
        guess = None
    if guess and guess.get('encoding') and (guess.get('confidence') or 0) >= 0.8:
        encoding = guess['encoding'].lower()
        if not encoding.startswith(('utf-16', 'utf-32')):
            try:
                return clean_xml_chars(raw.decode(encoding, 'replace'))
            except LookupError:
                pass

    from ebook_converter.ebooks.chardet import xml_to_unicode
    text, _encoding = xml_to_unicode(raw, assume_utf8=True)
    return clean_xml_chars(text)


# EPUB output splits content into ~260 KB flows and aborts with SplitError
# when a single paragraph leaves no split point. Break pathological
# paragraphs at whitespace well below that limit.
_PARAGRAPH_LIMIT = 32 * 1024


def split_overlong_paragraphs(text, limit=_PARAGRAPH_LIMIT):
    """Insert paragraph breaks into paragraphs the ebook pipeline can't split."""
    parts = re.split(r'(\n[ \t]*\n+)', text)
    out = []
    for index, part in enumerate(parts):
        if index % 2 == 1 or len(part) <= limit:
            out.append(part)
            continue
        remaining = part
        while len(remaining) > limit:
            window = remaining[:limit]
            cut = max(window.rfind(' '), window.rfind('\n'), window.rfind('\t'))
            if cut < limit // 2:
                cut = limit
            out.append(remaining[:cut])
            out.append('\n\n')
            remaining = remaining[cut:].lstrip(' \t\n')
        out.append(remaining)
    return ''.join(out)


def render_fenced_diagrams(markdown_text, output_dir, log=None):
    """Replace Mermaid code fences with validated UTF-8 SVG image references.

    SVG files are written directly into ``output_dir`` and referenced by bare
    filename, so the generated HTML document must live in the same directory.
    Invalid diagrams are left as code blocks so one bad diagram cannot abort
    the whole conversion. Returns the updated Markdown text.
    """
    if not _MERMAID_FENCE.search(markdown_text):
        return markdown_text

    from merm import render_diagram
    from xml.etree import ElementTree

    rendered = {}

    def replace(match):
        # Mermaid labels copied from generated documents can contain hidden
        # NUL or other XML-invalid control characters. merm preserves those in
        # its SVG text nodes, after which lxml rejects the resource.
        source = clean_xml_chars(match.group("source")).strip()
        if not source:
            return match.group(0)

        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        filename = rendered.get(digest)
        if filename is None:
            try:
                svg = render_diagram(source)
                if isinstance(svg, bytes):
                    svg = svg.decode("utf-8", "replace")
                svg = clean_xml_chars(svg)
                # Validate before exposing the resource to the conversion
                # pipeline; a malformed renderer output fails here instead of
                # as an opaque OEB resource parsing error later. Parse the
                # UTF-8 bytes rather than the str: an SVG carrying an XML
                # encoding declaration makes ElementTree.fromstring reject a
                # str on some builds.
                ElementTree.fromstring(svg.encode('utf-8'))
            except Exception as exc:
                if log is not None:
                    log.warning("Could not render Mermaid diagram: %s" % exc)
                return match.group(0)

            filename = "mermaid-{}-{}.svg".format(len(rendered) + 1, digest[:10])
            with open(os.path.join(output_dir, filename), "w",
                      encoding="utf-8", newline="\n") as output:
                output.write(svg)
            rendered[digest] = filename

        return "\n\n![Mermaid diagram]({})\n\n".format(filename)

    return _MERMAID_FENCE.sub(replace, markdown_text)


def plain_text_body(text):
    """Escape plain text into paragraph markup, keeping hard line breaks."""
    stripped = text.strip('\n')
    paragraphs = re.split(r'\n[ \t]*\n+', stripped) if stripped else ['']
    body = []
    for paragraph in paragraphs:
        lines = [html.escape(line, quote=False) for line in paragraph.split('\n')]
        body.append('<p>{}</p>'.format('<br/>\n'.join(lines)))
    return '\n'.join(body)


def markdown_body(text):
    """Render Markdown to an HTML body fragment. Raises on renderer failure."""
    import markdown
    return markdown.markdown(text, extensions=PYTHON_MARKDOWN_EXTENSIONS)


def build_html_document(title, body):
    return (
        '<!DOCTYPE html>\n'
        '<html><head><meta charset="utf-8"/>'
        '<title>{}</title></head><body>\n{}\n</body></html>\n'
    ).format(html.escape(title), body)


def validate_html_document(document):
    """Run the same lxml parse the conversion pipeline will apply later.

    Raises when lxml cannot recover an XML-serializable tree, which is
    exactly the condition that used to abort conversions mid-pipeline.
    """
    from lxml import etree
    from lxml import html as lxml_html

    root = lxml_html.document_fromstring(document)
    parser = etree.XMLParser(recover=True)
    etree.fromstring(etree.tostring(root, encoding='utf-8', method='xml'), parser=parser)


def resolve_local_resources(document, input_dir, work_dir):
    """Point relative asset links at the input document's directory.

    Markdown asset references (``![cover](cover.png)``) are relative to the
    input file, but the generated HTML lives in a temporary work dir, so
    HTMLInput would resolve them against that empty directory and silently drop
    the resource. Rewrite such references to absolute paths under ``input_dir``
    so they stay embeddable. References that already resolve inside
    ``work_dir`` (the generated Mermaid SVGs), non-local URLs, and hyperlinks
    are left untouched. Returns the (possibly rewritten) document string.
    """
    from urllib.parse import unquote, urlsplit

    from lxml import etree
    from lxml import html as lxml_html

    root = lxml_html.document_fromstring(document)
    changed = False
    for element, attribute, url, _pos in root.iterlinks():
        # Only inline assets get embedded; hyperlinks are left as-is so a link
        # to a sibling file is never turned into an embedded resource.
        if attribute != 'src' and not (
                attribute == 'href' and element.tag in ('image', 'link')):
            continue
        split = urlsplit(url)
        if split.scheme or split.netloc or not split.path:
            continue
        rel = unquote(split.path)
        if os.path.isabs(rel):
            continue
        if os.path.exists(os.path.join(work_dir, rel)):
            continue  # generated Mermaid SVG, already beside the HTML
        candidate = os.path.join(input_dir, rel)
        if os.path.exists(candidate):
            element.set(attribute, os.path.abspath(candidate))
            changed = True
    if not changed:
        return document
    return etree.tostring(root, encoding='unicode', method='xml')


def prepare_text_input(input_path, log=None, force_txt_plugin=False):
    """Normalize a text-like input file for conversion.

    Returns ``(path_for_plumber, temp_dirs, warnings)``. The input file is
    never modified; normalized content lives in a temporary sibling directory
    which the caller removes after conversion. Non-text inputs pass through
    untouched.
    """
    extension = os.path.splitext(input_path)[1].lower()
    if extension not in TEXT_EXTENSIONS:
        return input_path, [], []

    with open(input_path, 'rb') as source_file:
        text = decode_text_bytes(source_file.read())
    text = split_overlong_paragraphs(text)

    warnings = []
    work_dir = tempfile.mkdtemp(
        prefix='.econverter-text-', dir=os.path.dirname(input_path))
    title = os.path.splitext(os.path.basename(input_path))[0] or 'Text document'

    # .textile still relies on TXTInput's textile processor, and an explicit
    # formatting_type request is an opt-in to TXTInput's other processors.
    # Both get encoding normalization only, via a temporary UTF-8 copy.
    if extension == '.textile' or force_txt_plugin:
        normalized = os.path.join(work_dir, os.path.basename(input_path))
        with open(normalized, 'w', encoding='utf-8', newline='\n') as output:
            output.write(text)
        return normalized, [work_dir], warnings

    body = None
    if extension in MARKDOWN_EXTENSIONS:
        try:
            with_diagrams = render_fenced_diagrams(text, work_dir, log)
            body = markdown_body(with_diagrams)
        except Exception as exc:
            warnings.append(
                'Markdown rendering failed (%s: %s); '
                'converted as plain text instead.' % (type(exc).__name__, exc))
            body = None

    if body is None:
        body = plain_text_body(text)

    document = build_html_document(title, body)
    try:
        document = resolve_local_resources(
            document, os.path.dirname(os.path.abspath(input_path)), work_dir)
        validate_html_document(document)
    except Exception as exc:
        warnings.append(
            'Rendered HTML failed validation (%s: %s); '
            'converted as plain text instead.' % (type(exc).__name__, exc))
        document = build_html_document(title, plain_text_body(text))

    html_path = os.path.join(work_dir, 'index.html')
    with open(html_path, 'w', encoding='utf-8', newline='\n') as output:
        output.write(document)
    return html_path, [work_dir], warnings
