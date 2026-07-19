"""Regression tests for surgical fixes to the vendored ebook_converter code.

Each test here corresponds to an entry in AGENTS.md's "Vendored changes" list
and fails against the unpatched vendored snapshot.
"""


def test_split_txt_keeps_short_paragraphs():
    # split_string_separator used to replace any paragraph longer than a
    # miscomputed average chunk size with empty bytes, silently deleting the
    # first paragraph of small documents ("the TXT plugin ate my heading").
    from ebook_converter.ebooks.txt.processor import convert_basic

    html = convert_basic('short heading\n\nbody\n', epub_split_size_kb=260)
    assert 'short heading' in html and 'body' in html


def test_split_txt_breaks_giant_paragraph_without_losing_content():
    from ebook_converter.ebooks.txt.processor import split_txt

    paragraph = 'sentence one. ' * 40000  # ~560 KB, no blank lines
    result = split_txt(paragraph, epub_split_size_kb=260)
    assert result.count('sentence one.') == 40000
    assert max(len(p) for p in result.split('\n\n')) <= 260 * 1024


def test_plumber_option_logging_does_not_crash(tmp_path):
    # plumber logged changed options with mismatched printf args, which
    # raises inside logging handlers that surface formatting errors.
    import converter

    input_path = tmp_path / 'doc.md'
    input_path.write_text('# T\n\nbody\n')
    output_path = tmp_path / 'doc.epub'
    result = converter.convert(
        str(input_path), str(output_path), '--epub-version', '3')
    assert result['success'], result['message']
