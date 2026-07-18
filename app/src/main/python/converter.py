"""
Wrapper for ebook-converter to be called from Android/Chaquopy.
"""
import codecs
import os
import shutil
import traceback


MARKDOWN_EXTENSIONS = {'.md', '.markdown'}


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
        # Round-trip through XML serialization so the downstream OEB parser gets
        # well-formed markup and doesn't need the native html5-parser package.
        return etree.fromstring(etree.tostring(root, encoding='utf-8', method='xml'))

    def fix_resources(self, html, base_dir):
        try:
            root = parse_document(html)
        except (ValueError, etree.ParserError, etree.XMLSyntaxError):
            return html

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
        # The bundled desktop port accidentally passed a Clark-notation tag
        # string as lxml's namespace mapping. Define the two prefixes used by
        # Container.opf_xpath explicitly for the Android runtime.
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


def _decode_markdown_bytes(raw):
    """Decode Markdown bytes and remove characters XML cannot represent.

    Android document providers preserve the source bytes, so Markdown may be
    UTF-8, UTF-16 with a BOM, or UTF-16 without one. The latter commonly appears
    as an alternating NUL stream if it is decoded as UTF-8. Hidden NUL/control
    characters can also occur inside generated Mermaid labels and must not reach
    the XML-based ebook pipeline.
    """
    from ebook_converter.ebooks.chardet import xml_to_unicode
    from ebook_converter.utils.cleantext import clean_xml_chars

    if not raw:
        return ''

    # Check UTF-32 before UTF-16 because the little-endian UTF-32 BOM starts
    # with the UTF-16LE BOM bytes.
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

    # UTF-16 without a BOM is easy to identify in mostly ASCII Markdown by
    # which byte position contains the repeated NULs. Do this before chardet:
    # mixed Hebrew/emoji documents can otherwise be misidentified as Latin-1.
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


def _prepare_markdown(input_path, log):
    """Normalize Markdown and render fenced Mermaid blocks to local SVG."""
    if os.path.splitext(input_path)[1].lower() not in MARKDOWN_EXTENSIONS:
        return None

    from ebook_converter.ebooks.txt.mermaid import render_fenced_diagrams

    with open(input_path, 'rb') as source_file:
        source = _decode_markdown_bytes(source_file.read())

    rendered, diagram_temp_dir = render_fenced_diagrams(
        source,
        os.path.dirname(input_path),
        log,
    )

    # Always rewrite the app's temporary input copy as clean UTF-8. This fixes
    # UTF-16 Markdown and removes hidden XML-invalid controls even when the file
    # contains no Mermaid fences.
    with open(input_path, 'w', encoding='utf-8', newline='\n') as output_file:
        output_file.write(rendered)

    return diagram_temp_dir


def convert(input_path, output_path, *extra_args):
    """Convert ebook. Output format determined by extension. Returns dict."""
    diagram_temp_dir = None
    try:
        from ebook_converter import logging
        from ebook_converter.customize.conversion import OptionRecommendation
        from ebook_converter.ebooks.conversion.plumber import Plumber

        _install_android_compat()
        diagram_temp_dir = _prepare_markdown(input_path, logging.default_log)
        plumber = Plumber(input_path, output_path, logging.default_log)

        if extra_args:
            plumber.merge_ui_recommendations([
                (name, val, OptionRecommendation.HIGH)
                for name, val in _parse_extra_args(extra_args).items()
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
        if diagram_temp_dir is not None:
            shutil.rmtree(diagram_temp_dir, ignore_errors=True)
