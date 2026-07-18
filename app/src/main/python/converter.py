"""
Wrapper for ebook-converter to be called from Android/Chaquopy.
"""
import codecs
import html
import os
import re
import shutil
import tempfile
import traceback


MARKDOWN_EXTENSIONS = {'.md', '.markdown'}
PLAIN_TEXT_EXTENSIONS = {'.txt', '.text'}
TEXT_EXTENSIONS = MARKDOWN_EXTENSIONS | PLAIN_TEXT_EXTENSIONS | {'.textile'}


def _parse_extra_args(extra_args):
    """Parse CLI-style args into (name, value) pairs for Plumber options."""
    opts = {}
    i = 0
    while i < len(extra_args):
        arg = extra_args[i]
        if arg.startswith('--'):
            name = arg[2:].replace('-', '_')
            if i + 1 >= len(extra_args) or extra_args[i + 1].startswith('--'):
                opts[name] = True
            else:
                opts[name] = extra_args[i + 1]
                i += 1
        i += 1
    return opts


def _install_android_compat():
    """Install Android substitutes for unavailable or desktop-only helpers."""
    from lxml import etree
    from lxml import html as lxml_html

    from ebook_converter import constants
    from ebook_converter.ebooks.conversion.plugins.txt_input import TXTInput
    from ebook_converter.ebooks.oeb import parse_utils
    from ebook_converter.ebooks.oeb.polish import toc as toc_module
    from ebook_converter.ebooks.oeb.polish.container import Container

    def parse_document(data):
        if isinstance(data, bytes):
            data = data.decode('utf-8', 'replace')
        root = lxml_html.document_fromstring(data)
        return etree.fromstring(etree.tostring(root, encoding='utf-8', method='xml'))

    def fix_resources(self, html_data, base_dir):
        try:
            root = parse_document(html_data)
        except (ValueError, etree.ParserError, etree.XMLSyntaxError):
            return html_data

        for img in root.xpath("//*[local-name()='img'][@src]"):
            src = img.get('src')
            prefix = src.split(':', 1)[0].lower()
            if prefix not in ('file', 'http', 'https', 'ftp') and not os.path.isabs(src):
                path = os.path.join(base_dir, src)
                if os.access(path, os.R_OK):
                    with open(path, 'rb') as source:
                        shifted = self.shift_file(os.path.basename(path), source.read())
                    img.set('src', os.path.basename(shifted))

        return etree.tostring(root, encoding='unicode', method='xml')

    def html5_parse(data, max_nesting_depth=100):
        return parse_document(data)

    def opf_xpath(self, expr):
        return self.opf.xpath(expr, namespaces={
            'opf': constants.OPF2_NS,
            'dc': constants.DC11_NS,
        })

    class CallableBaseProxy:
        """Forward module attributes while supporting legacy ``base(ns, tag)``."""

        def __init__(self, module):
            self._module = module

        def __call__(self, namespace, name):
            return self._module.tag(namespace, name)

        def __getattr__(self, name):
            return getattr(self._module, name)

    TXTInput.fix_resources = fix_resources
    parse_utils.html5_parse = html5_parse
    Container.opf_xpath = opf_xpath
    if not callable(toc_module.base):
        toc_module.base = CallableBaseProxy(toc_module.base)


def _decode_text_bytes(raw):
    """Decode text-like bytes and remove characters XML cannot represent."""
    from ebook_converter.ebooks.chardet import xml_to_unicode
    from ebook_converter.utils.cleantext import clean_xml_chars

    if not raw:
        return ''

    bom_encodings = (
        (codecs.BOM_UTF32_LE, 'utf-32'),
        (codecs.BOM_UTF32_BE, 'utf-32'),
        (codecs.BOM_UTF16_LE, 'utf-16'),
        (codecs.BOM_UTF16_BE, 'utf-16'),
        (codecs.BOM_UTF8, 'utf-8-sig'),
    )
    for bom, encoding in bom_encodings:
        if raw.startswith(bom):
            return clean_xml_chars(raw.decode(encoding, 'replace'))

    sample = raw[:64 * 1024]
    pairs = max(1, len(sample) // 2)
    even_nuls = sample[0::2].count(0)
    odd_nuls = sample[1::2].count(0)
    even_ratio = even_nuls / pairs
    odd_ratio = odd_nuls / pairs

    if odd_nuls >= 4 and odd_ratio >= 0.15 and odd_nuls >= even_nuls * 2:
        text = raw.decode('utf-16-le', 'replace')
    elif even_nuls >= 4 and even_ratio >= 0.15 and even_nuls >= odd_nuls * 2:
        text = raw.decode('utf-16-be', 'replace')
    else:
        text, _encoding = xml_to_unicode(raw, assume_utf8=False)

    return clean_xml_chars(text)


def _write_plain_text_html(input_path, source):
    """Create lossless HTML for default `.txt`/`.text` conversion.

    The bundled TXT plugin can silently consume the first short paragraph while
    constructing its intermediate OEB document. Escaped HTML avoids that path
    while retaining paragraph and hard-line boundaries.
    """
    temp_dir = tempfile.mkdtemp(prefix='.econverter-text-', dir=os.path.dirname(input_path))
    title = os.path.splitext(os.path.basename(input_path))[0] or 'Text document'
    stripped = source.strip('\n')
    paragraphs = re.split(r'\n[ \t]*\n+', stripped) if stripped else ['']
    body = []
    for paragraph in paragraphs:
        escaped_lines = [html.escape(line, quote=False) for line in paragraph.split('\n')]
        body.append('<p>{}</p>'.format('<br/>\n'.join(escaped_lines)))

    document = (
        '<!DOCTYPE html>\n'
        '<html><head><meta charset="utf-8"/>'
        '<title>{}</title></head><body>\n{}\n</body></html>\n'
    ).format(html.escape(title), '\n'.join(body))
    html_path = os.path.join(temp_dir, 'index.html')
    with open(html_path, 'w', encoding='utf-8', newline='\n') as output_file:
        output_file.write(document)
    return html_path, temp_dir


def _prepare_text_input(input_path, log, plain_as_html=False):
    """Normalize text-like input and return the path handed to Plumber."""
    extension = os.path.splitext(input_path)[1].lower()
    if extension not in TEXT_EXTENSIONS:
        return input_path, []

    with open(input_path, 'rb') as source_file:
        source = _decode_text_bytes(source_file.read())

    cleanup_dirs = []
    rendered = source
    if extension in MARKDOWN_EXTENSIONS:
        from ebook_converter.ebooks.txt.mermaid import render_fenced_diagrams

        rendered, diagram_temp_dir = render_fenced_diagrams(
            source,
            os.path.dirname(input_path),
            log,
        )
        if diagram_temp_dir is not None:
            cleanup_dirs.append(diagram_temp_dir)

    # Preserve a normalized UTF-8 copy for every text-like source, even when a
    # temporary HTML document is used as the actual conversion input.
    with open(input_path, 'w', encoding='utf-8', newline='\n') as output_file:
        output_file.write(rendered)

    if plain_as_html:
        prepared_path, plain_temp_dir = _write_plain_text_html(input_path, rendered)
        cleanup_dirs.append(plain_temp_dir)
        return prepared_path, cleanup_dirs

    return input_path, cleanup_dirs


def convert(input_path, output_path, *extra_args):
    """Convert ebook. Output format determined by extension. Returns dict."""
    cleanup_dirs = []
    try:
        from ebook_converter import logging
        from ebook_converter.customize.conversion import OptionRecommendation
        from ebook_converter.ebooks.conversion.plumber import Plumber

        _install_android_compat()
        recommendations = _parse_extra_args(extra_args)
        input_extension = os.path.splitext(input_path)[1].lower()
        plain_as_html = (
            input_extension in PLAIN_TEXT_EXTENSIONS and
            'formatting_type' not in recommendations
        )
        prepared_input, cleanup_dirs = _prepare_text_input(
            input_path,
            logging.default_log,
            plain_as_html=plain_as_html,
        )
        plumber = Plumber(prepared_input, output_path, logging.default_log)

        if recommendations:
            plumber.merge_ui_recommendations([
                (name, val, OptionRecommendation.HIGH)
                for name, val in recommendations.items()
            ])

        plumber.run()

        return {'success': True, 'message': f'Converted to {output_path}'}
    except SystemExit:
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return {'success': True, 'message': f'Converted to {output_path}'}
        return {'success': False, 'message': 'Conversion failed (exit)'}
    except Exception as e:
        return {'success': False, 'message': f'{type(e).__name__}: {e}\n{traceback.format_exc()}'}
    finally:
        for directory in cleanup_dirs:
            shutil.rmtree(directory, ignore_errors=True)
