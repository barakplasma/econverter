"""End-to-end conversions through converter.convert(), as the app calls it.

These mirror (and go beyond) the Android instrumented tests. They exercise the
exact code the APK ships — same vendored ebook_converter, same pip
dependencies, same Python 3.11 — without an emulator.
"""

import os
import re
import zipfile
from xml.etree import ElementTree

import pytest

import converter


HEBREW = 'שלום — café'

MARKDOWN_FIXTURE = (
    '# Android emulator E2E\n'
    '\n'
    f'This text includes Unicode to exercise packaged encoding detection: {HEBREW}.\n'
    '\n'
    '| Feature | Result |\n'
    '|---|---|\n'
    '| Markdown | packaged runtime |\n'
    '| Mermaid | rendered for ebook output |\n'
    '\n'
    '```mermaid\n'
    'flowchart LR\n'
    '    Markdown["Hidden\x00control"] --> SVG\n'
    '    SVG --> Ebook\n'
    '```\n'
)


def epub_entries(path):
    with zipfile.ZipFile(path) as epub:
        return {name: epub.read(name) for name in epub.namelist()}


def epub_document_text(entries):
    return '\n'.join(
        data.decode('utf-8')
        for name, data in entries.items()
        if re.search(r'\.(x?html?|htm)$', name, re.IGNORECASE)
    )


def visible_text(document):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', document)).strip()


def run_convert(tmp_path, input_name, input_bytes, output_name, *args):
    input_path = tmp_path / input_name
    output_path = tmp_path / output_name
    input_path.write_bytes(input_bytes)
    result = converter.convert(str(input_path), str(output_path), *args)
    assert result['success'], result['message']
    assert output_path.is_file() and output_path.stat().st_size > 1024
    return input_path, output_path, result


def test_markdown_with_mermaid_to_epub3(tmp_path):
    _input_path, output_path, result = run_convert(
        tmp_path, 'android-e2e.md', MARKDOWN_FIXTURE.encode('utf-8'),
        'android-e2e.epub', '--epub-version', '3')
    assert result['warnings'] == []

    entries = epub_entries(output_path)
    assert entries['mimetype'].strip() == b'application/epub+zip'

    svg_names = [n for n in entries if n.lower().endswith('.svg')]
    assert svg_names, f'no Mermaid SVG in EPUB: {sorted(entries)}'
    svg = entries[svg_names[0]].decode('utf-8')
    assert '<svg' in svg and '\x00' not in svg
    ElementTree.fromstring(svg)

    document = epub_document_text(entries)
    assert 'Android emulator E2E' in document
    assert HEBREW.split()[0] in document
    assert '.svg' in document
    assert '<table' in document, 'Markdown table was not converted'
    assert '\x00' not in document


def test_bomless_utf16le_markdown_to_azw3(tmp_path):
    _input_path, output_path, _result = run_convert(
        tmp_path, 'kindle-usb-e2e.md', MARKDOWN_FIXTURE.encode('utf-16-le'),
        'kindle-usb-e2e.azw3')
    header = output_path.read_bytes()[:68]
    assert header[60:68] == b'BOOKMOBI', 'missing PalmDB/Kindle signature'
    assert output_path.stat().st_size > 4096


def test_bomless_utf16le_plain_text_to_epub(tmp_path):
    source = ('Plain text sanitizer\x00 keeps readable text — '
              f'{HEBREW}.\n\nSecond paragraph.')
    input_path, output_path, _result = run_convert(
        tmp_path, 'plain-control.txt', source.encode('utf-16-le'),
        'plain-control.epub')
    # The user's file must never be rewritten.
    assert input_path.read_bytes() == source.encode('utf-16-le')

    text = visible_text(epub_document_text(epub_entries(output_path)))
    assert 'Plain text sanitizer keeps readable text' in text
    assert 'Second paragraph.' in text
    assert '\x00' not in text


def test_repo_readme_to_epub(tmp_path):
    readme = os.path.join(os.path.dirname(__file__), '..', '..', 'README.md')
    _input_path, output_path, _result = run_convert(
        tmp_path, 'README.md', open(readme, 'rb').read(), 'readme.epub',
        '--epub-version', '3')
    text = visible_text(epub_document_text(epub_entries(output_path)))
    assert 'eConverter' in text


@pytest.mark.parametrize('name,source,expect', [
    ('raw-html', '# Doc\n\n<div class="x"><p>Raw <b>HTML</b> block\n\nmore text', 'more text'),
    ('unclosed-tags', 'Text with <table><tr><td>unclosed table\n\nafter the table\n', 'unclosed table'),
    ('rtl', '# כותרת\n\nפסקה בעברית עם [קישור](https://example.com) ו-*הדגשה*.\n', 'פסקה בעברית'),
    ('missing-image', '# Doc\n\n![gone](does-not-exist.png)\n\ntext continues\n', 'text continues'),
    ('nested-lists', '# Doc\n\n1. one\n   - nested\n     - deeper\n2. two\n\n> quote\n> more\n', 'deeper'),
    ('footnotes', 'Body text[^1]\n\n[^1]: the footnote\n', 'the footnote'),
    ('huge-paragraph', 'word ' * 60000, 'word word word'),
    ('control-soup', 'A\x00B\x01C\x1fD normal text continues here\n\nnext ¶\n', 'normal text continues'),
])
def test_markdown_corpus_never_fails(tmp_path, name, source, expect):
    _input_path, output_path, result = run_convert(
        tmp_path, f'{name}.md', source.encode('utf-8'), f'{name}.epub')
    text = visible_text(epub_document_text(epub_entries(output_path)))
    assert expect in text, (
        f'content lost for {name}: {text[:200]!r} (warnings: {result["warnings"]})')


def test_textile_still_converts(tmp_path):
    source = 'h1. Textile heading\n\nA paragraph with *strong* text.\n'
    _input_path, output_path, _result = run_convert(
        tmp_path, 'doc.textile', source.encode('utf-8'), 'doc.epub')
    text = visible_text(epub_document_text(epub_entries(output_path)))
    assert 'Textile heading' in text


def test_forced_formatting_type_uses_txt_plugin(tmp_path):
    source = '# Heading stays literal in plain mode\n\nbody\n'
    _input_path, output_path, _result = run_convert(
        tmp_path, 'doc.txt', source.encode('utf-8'), 'doc.epub',
        '--formatting-type', 'plain')
    text = visible_text(epub_document_text(epub_entries(output_path)))
    assert 'Heading stays literal' in text
