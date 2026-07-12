"""
SVG rasterization transform.
"""
import os

from ebook_converter.ebooks.oeb import base
from ebook_converter.ebooks.oeb.base import SVG_MIME


IMAGE_TAGS = {base.tag('xhtml', 'img'), base.tag('xhtml', 'object')}
KEEP_ATTRS = {'class', 'style', 'width', 'height', 'align'}


class Unavailable(Exception):
    pass


class SVGRasterizer(object):

    def __init__(self, base_css=''):
        self.base_css = base_css

    @classmethod
    def config(cls, cfg):
        return cfg

    @classmethod
    def generate(cls, opts):
        return cls()

    def __call__(self, oeb, context):
        # ponytail: SVG rasterization requires Qt (unavailable on Android), no-op.
        # If needed: svglib+reportlab (~4-6MB APK increase) is the least-effort path.
        oeb.logger.info('SVG rasterization skipped (Qt unavailable)')

    def rasterize_svg(self, elem, width=0, height=0, format='PNG'):
        raise Unavailable('SVG rasterization requires Qt')

    def dataize_manifest(self):
        pass

    def dataize_svg(self, item, svg=None):
        return svg if svg is not None else item.data

    def stylizer(self, item):
        pass

    def rasterize_spine(self):
        pass

    def rasterize_item(self, item):
        pass

    def rasterize_inline(self, elem, style, item):
        pass

    def rasterize_external(self, elem, style, item, svgitem):
        pass

    def rasterize_cover(self):
        pass
