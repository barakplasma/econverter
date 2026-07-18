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
