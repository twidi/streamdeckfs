#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import re
from dataclasses import dataclass
from time import time

from PIL import Image, ImageFont, ImageDraw

from ..common import ASSETS_PATH, RENDER_IMAGE_DELAY
from ..threads import Repeater
from .base import RE_PARTS, InvalidArg
from .image import keyImagePart


@dataclass(eq=False)
class KeyTextLine(keyImagePart):
    path_glob = 'TEXT*'
    main_path_re = re.compile(r'^(?P<kind>TEXT)(?:;|$)')
    filename_re_parts = keyImagePart.filename_re_parts + [
        re.compile(r'^(?P<arg>line)=(?P<value>\d+)$'),
        re.compile(r'^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<text_line>.*)$'),  # we'll use -1 if no line given
        re.compile(r'^(?P<arg>text)=(?P<value>.+)$'),
        re.compile(r'^(?P<arg>size)=(?P<value>' + RE_PARTS["% | number"] + ')$'),
        re.compile(r'^(?P<arg>weight)(?:=(?P<value>thin|light|regular|medium|bold|black))?$'),
        re.compile(r'^(?P<flag>italic)(?:=(?P<value>false|true))?$'),
        re.compile(r'^(?P<arg>align)(?:=(?P<value>left|center|right))?$'),
        re.compile(r'^(?P<arg>valign)(?:=(?P<value>top|middle|bottom))?$'),
        re.compile(r'^(?P<arg>color)=(?P<value>' + RE_PARTS["color"] + ')$'),
        re.compile(r'^(?P<arg>opacity)=(?P<value>' + RE_PARTS["0-100"] + ')$'),
        re.compile(r'^(?P<flag>wrap)(?:=(?P<value>false|true))?$'),
        re.compile(r'^(?P<arg>margin)=(?P<top>-?' + RE_PARTS["% | number"] + '),(?P<right>-?' + RE_PARTS["% | number"] + '),(?P<bottom>-?' + RE_PARTS["% | number"] + '),(?P<left>-?' + RE_PARTS["% | number"] + ')$'),
        re.compile(r'^(?P<arg>margin\.(?:[0123]|top|right|bottom|left))=(?P<value>-?' + RE_PARTS["% | number"] + ')$'),
        re.compile(r'^(?P<arg>scroll)=(?P<value>-?' + RE_PARTS["% | number"] + ')$'),
    ]
    main_filename_part = lambda args: 'TEXT'
    filename_parts = [
        lambda args: f'line={line}' if (line := args.get('line')) else None,
        keyImagePart.name_filename_part,
        lambda args: f'ref={ref.get("page") or ""}:{ref.get("key") or ref.get("key_same_page") or ""}:{ref.get("text_line") or ""}' if (ref := args.get('ref')) else None,
    ] + keyImagePart.filename_file_parts + [
        lambda args: f'text={text}' if (text := args.get('text')) else None,
        lambda args: f'size={size}' if (size := args.get('size')) else None,
        lambda args: f'weight={weight}' if (weight := args.get('weight')) else None,
        lambda args: 'italic' if args.get('italic', False) in (True, 'true', None) else None,
        lambda args: f'color={color}' if (color := args.get('color')) else None,
        lambda args: f'align={align}' if (align := args.get('align')) else None,
        lambda args: f'valign={valign}' if (valign := args.get('valign')) else None,
        lambda args: f'margin={margin["top"]},{margin["right"]},{margin["bottom"]},{margin["left"]}' if (margin := args.get('margin')) else None,
        lambda args: [f'{key}={value}' for key, value in args.items() if key.startswith('margin.')],
        lambda args: f'opacity={opacity}' if (opacity := args.get('opacity')) else None,
        lambda args: f'scroll={scroll}' if (scroll := args.get('scroll')) else None,
        lambda args: 'wrap' if args.get('wrap', False) in (True, 'true', None) else None,
        keyImagePart.disabled_filename_part,
    ]

    fonts_path = ASSETS_PATH / 'fonts'
    font_cache = {}
    text_size_cache = {}

    identifier_attr = 'line'
    parent_container_attr = 'text_lines'

    line: int

    text_size_drawer = None
    SCROLL_WAIT = 1

    def __post_init__(self):
        super().__post_init__()
        self.text = None
        self.size = None
        self.weight = None
        self.italic = False
        self.align = None
        self.valign = None
        self.color = None
        self.opacity = None
        self.wrap = False
        self.margin = None
        self.scroll = None
        self._complete_image = None
        self.scrollable = False
        self.scrolled = 0
        self.scrolled_at = None
        self.scroll_thread = None

    @property
    def str(self):
        return f'TEXT LINE{(" %s" % self.line) if self.line != -1 else ""} ({self.name}{", disabled" if self.disabled else ""})'

    @classmethod
    def convert_args(cls, args):
        final_args = super().convert_args(args)

        if len([1 for key in ('text', 'file') if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "text", "file"')

        final_args['line'] = int(args['line']) if 'line' in args else -1  # -1 for image used if no layers
        if 'text' in args:
            final_args['mode'] = 'text'
            final_args['text'] = args.get('text') or ''
        final_args['size'] = cls.parse_value_or_percent(args.get('size') or '20%')
        final_args['weight'] = args.get('weight') or 'medium'
        if 'italic' in args:
            final_args['italic'] = args['italic']
        final_args['align'] = args.get('align') or 'left'
        final_args['valign'] = args.get('valign') or 'top'
        final_args['color'] = args.get('color') or 'white'
        if 'opacity' in args:
            final_args['opacity'] = int(args['opacity'])
        if 'wrap' in args:
            final_args['wrap'] = args['wrap']
        if 'margin' in args:
            final_args['margin'] = {}
            for part, val in list(args['margin'].items()):
                final_args['margin'][part] = cls.parse_value_or_percent(val)
        if 'scroll' in args:
            final_args['scroll'] = cls.parse_value_or_percent(args['scroll'])

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        line = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in ('text', 'size', 'weight', 'italic', 'align', 'valign', 'color', 'opacity', 'wrap', 'margin', 'scroll'):
            if key not in args:
                continue
            setattr(line, key, args[key])
        if not line.deck.scroll_activated:
            line.scroll = None
        line.check_file_exists()
        return line

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, key = cls.find_reference_key(parent, ref_conf)
        if not final_ref_conf.get('text_line'):
            final_ref_conf['text_line'] = -1
        if not key:
            return final_ref_conf, None
        return final_ref_conf, key.find_text_line(final_ref_conf['text_line'])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for key, path, parent, ref_conf
            in self.iter_waiting_references_for_key(self.key)
            if (text_line := key.find_text_line(ref_conf['text_line'])) and text_line.line == self.line
        ]

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if args['line'] == int(filter):
                return True
        except ValueError:
            pass
        return args.get('name') == filter

    @property
    def resolved_text(self):
        if self.text is None:
            if self.mode == 'content':
                self.track_symlink_dir()
                try:
                    self.text = self.resolved_path.read_text()
                except Exception:
                    pass
                if not self.text and self.reference:
                    self.text = self.reference.resolved_text
            elif self.mode in ('file', 'inside'):
                if (path := self.get_file_path()):
                    try:
                        self.text = path.read_text()
                    except Exception:
                        pass
        return self.text

    @classmethod
    def get_text_size_drawer(cls):
        if cls.text_size_drawer is None:
            cls.text_size_drawer = ImageDraw.Draw(Image.new("RGB", (100, 100)))
        return cls.text_size_drawer

    def get_font_path(self):
        weight = self.weight.capitalize()
        if weight == 'regular' and self.italic:
            weight = ''
        italic = 'Italic' if self.italic else ''
        return self.fonts_path / 'roboto' / f'Roboto-{weight}{italic}.ttf'

    @classmethod
    def get_font(cls, font_path, size):
        if (font_path, size) not in cls.font_cache:
            cls.font_cache[(font_path, size)] = ImageFont.truetype(str(font_path), size)
        return cls.font_cache[(font_path, size)]

    @classmethod
    def get_text_size(cls, text, font):
        if text not in cls.text_size_cache.setdefault(font, {}):
            cls.text_size_cache[font][text] = cls.get_text_size_drawer().textsize(text, font=font)
        return cls.text_size_cache[font][text]

    @classmethod
    def split_text_in_lines(cls, text, max_width, font):
        '''Split the given `text`  using the minimum length word-wrapping
        algorithm to restrict the text to a pixel width of `width`.
        Based on:
        https://web.archive.org/web/20110818230121/http://jesselegg.com/archives/2009/09/5/simple-word-wrap-algorithm-pythons-pil/
        With some updates:
        - respect new lines
        - split word in parts if it is too long to fit on a line (always start on its own line, but the last of its lines
          can be followed by another word)
        '''
        line_width, line_height = cls.get_text_size(text, font)
        if line_width <= max_width:
            return [text]
        space_width = cls.get_text_size(' ', font)[0]
        lines = []
        text_lines = [line.strip() for line in text.splitlines()]
        for text_line in text_lines:
            if not text_line:
                lines.append(' ')
                continue
            remaining = 0 if lines else max_width
            for word in text_line.split(None):
                if (word_width := cls.get_text_size(word, font)[0]) + space_width > remaining:
                    if word_width > max_width:
                        word, left = word[:-1], word[-1]
                        while True:
                            word_width = cls.get_text_size(word, font)[0]
                            if len(word) == 1 and word_width >= max_width:  # big letters!
                                lines.append(word)
                                word, left = left, ''
                                remaining = 0
                                if not word:
                                    break
                            elif word_width <= remaining:
                                lines.append(word)
                                word, left = left, ''
                                if word:
                                    remaining = max_width
                                else:
                                    remaining = remaining - word_width
                                    break
                            else:
                                word, left = word[:-1], word[-1] + left
                    else:
                        lines.append(word)
                        remaining = max_width - word_width
                else:
                    if not lines:
                        lines.append(word)
                    else:
                        lines[-1] += ' %s' % word
                    remaining = remaining - (word_width + space_width)
        return lines

    def on_changed(self):
        super().on_changed()
        self.stop_scroller()
        if self.mode != 'text':
            self.text = None
        self._complete_image = self.compose_cache = None
        self.key.on_image_changed()
        for reference in self.referenced_by:
            reference.on_changed()

    def compose(self):
        if not self.scrollable:
            # will always be run the first time, because we'll only know
            # if it's scrollable after the first rendering
            return super().compose()
        return self._compose()

    def _compose(self):
        if not self._complete_image and not self._compose_base():
            return
        self.compose_cache = self._compose_final()
        self.start_scroller()
        return self.compose_cache

    def _compose_base(self):
        text = self.resolved_text
        if not text:
            return None

        image_size = self.key.image_size
        margins = self.convert_margins()
        max_width = image_size[0] - (margins['right'] + margins['left'])
        max_height = image_size[1] - (margins['top'] + margins['bottom'])

        font = self.get_font(self.get_font_path(), self.convert_coordinate(self.size, 'height'))
        if self.wrap:
            lines = self.split_text_in_lines(text, max_width, font)
        else:
            lines = [" ".join(stripped_line for line in text.splitlines() if (stripped_line := line.strip()))]

        # compute all sizes
        lines_with_dim = [(line, ) + self.get_text_size(line, font) for line in lines]
        if self.wrap:
            total_width = max(line_width for line, line_width, line_height in lines_with_dim)
            total_height = sum(line_height for line, line_width, line_height in lines_with_dim)
        else:
            total_width, total_height = lines_with_dim[0][1:]

        image = Image.new("RGBA", (total_width, total_height), '#00000000')
        drawer = ImageDraw.Draw(image)

        pos_x, pos_y = 0, 0

        for line, line_width, line_height in lines_with_dim:
            if self.align == 'right':
                pos_x = total_width - line_width
            elif self.align == 'center':
                pos_x = round((total_width - line_width) / 2)
            drawer.text((pos_x, pos_y), line, font=font, fill=self.color)
            pos_y += line_height

        self.apply_opacity(image)

        self._complete_image = {
            'image': image,
            'max_width': max_width,
            'max_height': max_height,
            'total_width': total_width,
            'total_height': total_height,
            'margins': margins,
            'default_crop': None,
            'visible_width': total_width,
            'visible_height': total_height,
            'fixed_position_top': None,
            'fixed_position_left': None,
        }

        align = self.align
        valign = self.valign
        if total_width > max_width or total_height > max_height:
            crop = {}
            if total_width <= max_width:
                crop['left'] = 0
                crop['right'] = total_width
            else:
                if self.scroll and not self.wrap and self.scroll_pixels:
                    self.scrollable = align = ('left' if self.scroll_pixels > 0 else 'right')
                self._complete_image['fixed_position_left'] = margins['left']
                if align == 'left':
                    crop['left'] = 0
                    crop['right'] = max_width
                elif align == 'right':
                    crop['left'] = total_width - max_width
                    crop['right'] = total_width
                else:  # center
                    crop['left'] = round((total_width - max_width) / 2)
                    crop['right'] = round((total_width + max_width) / 2)
                self._complete_image['visible_width'] = max_width

            if total_height <= max_height:
                crop['top'] = 0
                crop['bottom'] = total_height
            else:
                if self.scroll and self.wrap and self.scroll_pixels:
                    self.scrollable = valign = ('top' if self.scroll_pixels > 0 else 'bottom')
                self._complete_image['fixed_position_top'] = margins['top']
                if valign == 'top':
                    crop['top'] = 0
                    crop['bottom'] = max_height
                elif valign == 'bottom':
                    crop['top'] = total_height - max_height
                    crop['bottom'] = total_height
                else:  # middle
                    crop['top'] = round((total_height - max_height) / 2)
                    crop['bottom'] = round((total_height + max_height) / 2)
                self._complete_image['visible_height'] = max_height

            self._complete_image['default_crop'] = crop

        self._complete_image['align'] = align
        self._complete_image['valign'] = valign

        return self._complete_image

    def _compose_final(self):
        if not (ci := self._complete_image):
            return None

        if not (crop := ci['default_crop']):
            final_image = ci['image']
        else:
            if self.scrollable:
                if self.scrolled is None:
                    self.scrolled = 0
                else:
                    crop = crop.copy()
                    sign = self.scroll_pixels // abs(self.scroll_pixels)
                    if self.scrollable in ('left', 'right'):
                        if sign * self.scrolled >= ci['total_width']:
                            self.scrolled = -sign * ci['max_width']
                        crop['left'] += self.scrolled
                        crop['right'] = max(crop['right'] + self.scrolled, ci['total_width'])
                    else:  # top, bottom
                        if sign * self.scrolled >= ci['total_height']:
                            self.scrolled = -sign * ci['max_height']
                        crop['top'] += self.scrolled
                        crop['bottom'] = max(crop['bottom'] + self.scrolled, ci['total_height'])

            final_image = ci['image'].crop((crop['left'], crop['top'], crop['right'], crop['bottom']))

        if (left := ci['fixed_position_left']) is None:
            if (align := ci['align']) == 'left':
                left = ci['margins']['left']
            elif align == 'right':
                left = self.key.width - ci['margins']['right'] - ci['visible_width']
            else:  # center
                left = ci['margins']['left'] + round((ci['max_width'] - ci['visible_width']) / 2)

        if (top := ci['fixed_position_top']) is None:
            if (valign := ci['valign']) == 'top':
                top = ci['margins']['top']
            elif valign == 'bottom':
                top = self.key.height - ci['margins']['bottom'] - ci['visible_height']
            else:  # middle
                top = ci['margins']['top'] + round((ci['max_height'] - ci['visible_height']) / 2)

        return final_image, left, top, final_image

    @property
    def scroll_pixels(self):
        if not hasattr(self, '_scroll_pixels'):
            self._scroll_pixels = self.convert_coordinate(self.scroll, 'height' if self.wrap else 'width')
        return self._scroll_pixels

    def start_scroller(self):
        if self.scroll_thread or not self.scrollable:
            return
        self.scrolled = 0
        self.scrolled_at = time() + self.SCROLL_WAIT
        self.scroll_thread = Repeater(self.do_scroll, max(RENDER_IMAGE_DELAY, 1 / abs(self.scroll_pixels)), wait_first=self.SCROLL_WAIT, name=f'TxtScrol{self.page.number}.{self.key.row}{self.key.col}{(".%s" % self.line) if self.line and self.line != -1 else ""}')
        self.scroll_thread.start()

    def stop_scroller(self, *args, **kwargs):
        if not self.scroll_thread:
            return
        self.scroll_thread.stop()
        self.scroll_thread = None

    def do_scroll(self):
        # we scroll `self.scroll_pixels` pixels each second, so we compute the nb of pixels to move since the last move
        time_passed = (now := time()) - self.scrolled_at
        nb_pixels = round(time_passed * self.scroll_pixels)
        if not nb_pixels:
            return
        self.scrolled += nb_pixels
        self.scrolled_at = now
        self.key.on_image_changed()

    def version_deactivated(self):
        super().version_deactivated()
        self.stop_scroller()
