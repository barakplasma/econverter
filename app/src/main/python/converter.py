"""
Wrapper for ebook-converter to be called from Android/Chaquopy.
"""
import os
import shutil
import traceback

import econverter_text


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


def convert(input_path, output_path, *extra_args):
    """Convert ebook. Output format determined by extension. Returns dict."""
    temp_dirs = []
    try:
        from ebook_converter import logging
        from ebook_converter.customize.conversion import OptionRecommendation
        from ebook_converter.ebooks.conversion.plumber import Plumber

        _install_android_compat()
        recommendations = _parse_extra_args(extra_args)
        prepared_input, temp_dirs, warnings = econverter_text.prepare_text_input(
            input_path,
            logging.default_log,
            force_txt_plugin='formatting_type' in recommendations,
        )
        for warning in warnings:
            logging.default_log.warning(warning)
        plumber = Plumber(prepared_input, output_path, logging.default_log)

        if recommendations:
            plumber.merge_ui_recommendations([
                (name, val, OptionRecommendation.HIGH)
                for name, val in recommendations.items()
            ])

        plumber.run()
        return {'success': True, 'message': 'OK', 'warnings': warnings}
    except SystemExit:
        return {'success': False, 'message': 'Conversion failed (exit)'}
    except Exception as e:
        return {'success': False, 'message': f'{type(e).__name__}: {e}\n{traceback.format_exc()}'}
    finally:
        for directory in temp_dirs:
            shutil.rmtree(directory, ignore_errors=True)
