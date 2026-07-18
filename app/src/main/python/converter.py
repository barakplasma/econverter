"""
Wrapper for ebook-converter to be called from Android/Chaquopy.
"""
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

    TXTInput.fix_resources = fix_resources
    parse_utils.html5_parse = html5_parse
    Container.opf_xpath = opf_xpath


def _prepare_markdown(input_path, log):
    """Render fenced Mermaid blocks and rewrite the temporary Android input copy."""
    if os.path.splitext(input_path)[1].lower() not in MARKDOWN_EXTENSIONS:
        return None

    from ebook_converter.ebooks.txt.mermaid import render_fenced_diagrams

    with open(input_path, 'r', encoding='utf-8-sig', errors='replace') as source_file:
        source = source_file.read()

    rendered, diagram_temp_dir = render_fenced_diagrams(
        source,
        os.path.dirname(input_path),
        log,
    )
    if diagram_temp_dir is not None:
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
