"""Cover helpers required by EPUB 3 conversion.

This module contains the small subset of Calibre's cover-polishing behavior
used by eConverter's EPUB output plugin.
"""

from ebook_converter.ebooks.oeb.base import OEB_DOCS


def find_cover_page(container):
    """Return the document marked as the book's cover page, if one exists."""
    version = container.opf_version_parsed
    mime_map = container.mime_map

    if version.major < 3:
        for reference_type, name in container.guide_type_map.items():
            if (
                reference_type.lower() == "cover"
                and mime_map.get(name, "").lower() in OEB_DOCS
            ):
                return name
        return None

    for name in container.manifest_items_with_property("calibre:title-page"):
        return name

    from ebook_converter.ebooks.oeb.polish.toc import get_landmarks

    for landmark in get_landmarks(container):
        name = landmark.get("dest")
        if (
            landmark.get("type") == "cover"
            and mime_map.get(name, "").lower() in OEB_DOCS
        ):
            return name
    return None


def fix_conversion_titlepage_links_in_nav(container):
    """Restore EPUB 3 navigation links to a generated conversion title page."""
    from ebook_converter.ebooks.oeb.polish.toc import find_existing_nav_toc

    cover_page_name = find_cover_page(container)
    if not cover_page_name:
        return

    nav_page_name = find_existing_nav_toc(container)
    if not nav_page_name:
        return

    changed = False
    root = container.parsed(nav_page_name)
    for element in root.xpath("//*[@data-calibre-removed-titlepage]"):
        element.attrib.pop("data-calibre-removed-titlepage", None)
        element.set("href", container.name_to_href(cover_page_name, nav_page_name))
        changed = True

    if changed:
        container.dirty(nav_page_name)
