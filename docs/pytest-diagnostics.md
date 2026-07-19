============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/runner/work/econverter/econverter
collected 51 items

tests/python/test_convert_e2e.py ...............                         [ 29%]
tests/python/test_text_ingress.py .................................      [ 94%]
tests/python/test_vendored_fixes.py ...                                  [100%]

=============================== warnings summary ===============================
tests/python/test_convert_e2e.py: 345 warnings
tests/python/test_vendored_fixes.py: 23 warnings
  /home/runner/work/econverter/econverter/app/src/main/python/ebook_converter/ebooks/oeb/stylizer.py:215: DeprecationWarning: Call to deprecated method '_getCSSValue'. Use ``property.propertyValue`` instead.
    style.update(normalizer(name, prop.cssValue))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 51 passed, 368 warnings in 1.86s =======================
