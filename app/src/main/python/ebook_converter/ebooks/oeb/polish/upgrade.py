"""EPUB 2 to EPUB 3 upgrade support used by the conversion pipeline.

This is the conversion-relevant subset of Calibre's upgrader. It deliberately
avoids desktop library/editor dependencies which aren't part of the Android
application, while producing the EPUB 3 metadata, manifest properties and
navigation document required by generated ebooks.
"""

from datetime import datetime, timezone

from ebook_converter import constants as const
from ebook_converter.ebooks.oeb import base
from ebook_converter.ebooks.oeb.polish.opf import get_book_language
from ebook_converter.ebooks.oeb.polish.toc import (
    commit_nav_toc,
    find_existing_ncx_toc,
    get_landmarks,
    get_toc,
)


def _add_properties(item, *properties):
    existing = set((item.get("properties") or "").split())
    existing.update(properties)
    if existing:
        item.set("properties", " ".join(sorted(existing)))


def _upgrade_metadata(container):
    metadata = container.opf_xpath("./opf:metadata")[0]

    # EPUB requires a language. The generated OPF normally already has one,
    # but keep the upgrade robust for bare Markdown input.
    languages = container.opf_xpath("./opf:metadata/dc:language")
    if not languages:
        language = metadata.makeelement(base.tag("dc", "language"))
        language.text = "und"
        metadata.append(language)

    # Convert OPF 2 creator/contributor attributes into EPUB 3 refinements.
    for element_name in ("creator", "contributor"):
        for element in container.opf_xpath("./opf:metadata/dc:" + element_name):
            role = element.attrib.pop("{%s}role" % const.OPF2_NS, None)
            file_as = element.attrib.pop("{%s}file-as" % const.OPF2_NS, None)
            if not role and not file_as:
                continue
            element_id = element.get("id")
            if not element_id:
                element_id = "%s-%x" % (element_name, id(element))
                element.set("id", element_id)
            if role:
                refinement = metadata.makeelement(
                    base.tag("opf", "meta"),
                    refines="#" + element_id,
                    property="role",
                    scheme="marc:relators",
                )
                refinement.text = role
                metadata.append(refinement)
            if file_as:
                refinement = metadata.makeelement(
                    base.tag("opf", "meta"),
                    refines="#" + element_id,
                    property="file-as",
                )
                refinement.text = file_as
                metadata.append(refinement)

    # EPUB 3 requires exactly one dcterms:modified timestamp.
    for old in container.opf_xpath(
        './opf:metadata/opf:meta[@property="dcterms:modified"]'
    ):
        old.getparent().remove(old)
    modified = metadata.makeelement(
        base.tag("opf", "meta"), property="dcterms:modified"
    )
    modified.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata.append(modified)


def _collect_manifest_properties(container):
    for item in container.opf_xpath("//opf:manifest/opf:item[@href and @media-type]"):
        media_type = (item.get("media-type") or "").lower()
        if media_type not in base.OEB_DOCS:
            continue
        name = container.href_to_name(item.get("href"), container.opf_name)
        try:
            root = container.parsed(name)
        except KeyError:
            continue

        properties = []
        if root.xpath('//*[local-name()="svg"]'):
            properties.append("svg")
        if root.xpath('//*[local-name()="script"]'):
            properties.append("scripted")
        if root.xpath('//*[local-name()="math"]'):
            properties.append("mathml")
        if root.xpath(
            '//*[local-name()="switch" and namespace-uri()=$namespace]',
            namespace=const.EPUB_NS,
        ):
            properties.append("switch")
        if properties:
            _add_properties(item, *properties)


def _normalize_landmarks(container, landmarks):
    guide_type_map = {
        "acknowledgements": "acknowledgments",
        "bibliography": "bibliography",
        "colophon": "colophon",
        "copyright-page": "copyright-page",
        "cover": "cover",
        "dedication": "dedication",
        "epigraph": "epigraph",
        "foreword": "foreword",
        "glossary": "glossary",
        "index": "index",
        "loi": "loi",
        "lot": "lot",
        "notes": "rearnotes",
        "preface": "preface",
        "text": "bodymatter",
        "title-page": "titlepage",
        "toc": "toc",
    }
    normalized = []
    for landmark in landmarks:
        landmark = dict(landmark)
        old_type = (landmark.get("type") or "").lower()
        new_type = guide_type_map.get(old_type)
        if new_type is None and old_type.startswith("other."):
            new_type = old_type.partition(".")[-1]
        if not new_type:
            continue
        landmark["type"] = new_type
        normalized.append(landmark)
        if (
            new_type == "cover"
            and container.mime_map.get(landmark.get("dest"), "").lower()
            in base.OEB_DOCS
        ):
            container.apply_unique_properties(landmark["dest"], "calibre:title-page")
    return normalized


def epub_2_to_3(container, report, previous_nav=None, remove_ncx=True):
    """Upgrade a generated EPUB 2 container to EPUB 3 in place."""
    del report  # Kept for API compatibility with Calibre's upgrader.

    # Read EPUB 2 navigation before changing the package version.
    toc = get_toc(container)
    ncx_name = find_existing_ncx_toc(container)
    landmarks = get_landmarks(container)

    _upgrade_metadata(container)
    _collect_manifest_properties(container)

    if ncx_name and remove_ncx:
        container.remove_item(ncx_name)
        for spine in container.opf_xpath("./opf:spine"):
            spine.attrib.pop("toc", None)

    for guide in container.opf_xpath("./opf:guide"):
        guide.getparent().remove(guide)

    container.opf.set("version", "3.0")
    commit_nav_toc(
        container,
        toc,
        lang=get_book_language(container),
        landmarks=_normalize_landmarks(container, landmarks),
        previous_nav=previous_nav,
    )
    container.refresh_mime_map()
    container.dirty(container.opf_name)
