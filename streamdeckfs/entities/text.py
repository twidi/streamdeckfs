#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import re
from collections import namedtuple
from dataclasses import dataclass
from time import time

from emoji import UNICODE_EMOJI, emojize
from PIL import Image, ImageDraw, ImageFont

from ..common import ASSETS_PATH, RENDER_IMAGE_DELAY
from ..threads import Repeater
from .base import RE_PARTS, InvalidArg
from .image import KeyImagePart

TextPart = namedtuple("TextPart", ["kind", "text", "width", "height"], defaults=[None, None, None, None])
TextLine = namedtuple("TextLine", ["parts", "width", "height"])
PreparedText = namedtuple(
    "PreparedText", ["lines", "width", "height", "top_margin", "nb_forced_wrap", "emoji_size", "text_font"]
)
TEXT = "t"
SPACE = " "
EMOJI = "e"
EMOJI_TYPES = {"\uFE0F", "\uFE0E"}  # emoji variant, text variant


@dataclass(eq=False)
class KeyTextLine(KeyImagePart):
    path_glob = "TEXT*"
    main_part_re = re.compile(r"^(?P<kind>TEXT)$")
    main_part_compose = lambda args: "TEXT"
    get_main_args = lambda self: {"kind": "TEXT"}

    allowed_args = KeyImagePart.allowed_args | {
        "line": re.compile(r"^(?P<arg>line)=(?P<value>\d+)$"),
        "ref": re.compile(
            r"^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<text_line>.*)$"  # we'll use -1 if no line given
        ),
        "text": re.compile(r"^(?P<arg>text)=(?P<value>.+)$", re.DOTALL),  # include new lines
        "size": re.compile(r"^(?P<arg>size)=(?P<value>" + RE_PARTS["% | number"] + ")$"),
        "weight": re.compile(r"^(?P<arg>weight)(?:=(?P<value>thin|light|regular|medium|bold|black))?$"),
        "italic": re.compile(r"^(?P<flag>italic)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
        "align": re.compile(r"^(?P<arg>align)(?:=(?P<value>left|center|right))?$"),
        "valign": re.compile(r"^(?P<arg>valign)(?:=(?P<value>top|middle|bottom))?$"),
        "color": re.compile(r"^(?P<arg>color)=(?P<value>" + RE_PARTS["color"] + ")$"),
        "wrap": re.compile(r"^(?P<flag>wrap)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
        "fit": re.compile(r"^(?P<flag>fit)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
        "emojis": re.compile(r"^(?P<flag>emojis)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
        "scroll": re.compile(r"^(?P<arg>scroll)=(?P<value>-?" + RE_PARTS["% | number"] + ")$"),
    }

    fonts_path = ASSETS_PATH / "fonts"
    font_cache = {}
    text_size_cache = {}
    emoji_font_size = 109
    emoji_cache = {(emoji_font_size, 0): {}}

    identifier_attr = "line"
    parent_container_attr = "text_lines"

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
        self.fit = False
        self.allow_emojis = True
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
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)

        if len([1 for key in ("text", "file") if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "text", "file"')

        if len([1 for key in ("size", "fit") if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "size", "fit"')

        final_args["line"] = int(args["line"]) if "line" in args else -1  # -1 for image used if no layers
        if "text" in args:
            final_args["mode"] = "text"
            final_args["text"] = args.get("text") or ""
        final_args["size"] = cls.parse_value_or_percent(args.get("size") or "20%")
        final_args["weight"] = args.get("weight") or "medium"
        if "italic" in args:
            final_args["italic"] = args["italic"]
        final_args["align"] = args.get("align") or ("center" if args.get("fit") else "left")
        final_args["valign"] = args.get("valign") or ("middle" if args.get("fit") else "top")
        final_args["color"] = args.get("color") or "white"
        if "opacity" in args:
            final_args["opacity"] = int(args["opacity"])
        if "wrap" in args:
            final_args["wrap"] = args["wrap"]
        if "fit" in args:
            final_args["fit"] = args["fit"]
        if "emojis" in args:
            final_args["allow_emojis"] = args["emojis"]
        if "margin" in args:
            final_args["margin"] = {}
            for part, val in list(args["margin"].items()):
                final_args["margin"][part] = cls.parse_value_or_percent(val)
        if "scroll" in args:
            final_args["scroll"] = cls.parse_value_or_percent(args["scroll"])

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        line = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in (
            "text",
            "size",
            "weight",
            "italic",
            "align",
            "valign",
            "color",
            "opacity",
            "wrap",
            "fit",
            "allow_emojis",
            "margin",
            "scroll",
        ):
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
        if not final_ref_conf.get("text_line"):
            final_ref_conf["text_line"] = -1
        if not key:
            return final_ref_conf, None
        return final_ref_conf, key.find_text_line(final_ref_conf["text_line"])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for key, path, parent, ref_conf in self.iter_waiting_references_for_key(self.key)
            if (text_line := key.find_text_line(ref_conf["text_line"])) and text_line.line == self.line
        ]

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if args["line"] == int(filter):
                return True
        except ValueError:
            pass
        return args.get("name") == filter

    @property
    def resolved_text(self):
        if self.text is None:
            if self.mode == "content":
                self.track_symlink_dir()
                try:
                    self.text = self.resolved_path.read_text()
                except Exception:
                    pass
                if not self.text and self.reference:
                    self.text = self.reference.resolved_text
            elif self.mode in ("file", "inside"):
                if path := self.get_file_path():
                    try:
                        self.text = path.read_text()
                    except Exception:
                        pass
        if self.text:
            self.text = self.replace_special_chars(
                self.replace_vars_in_content(self.text),
                {
                    "slash": self.slash_repl,
                    "semicolon": self.semicolon_repl,
                },
            )
        else:
            self.text = ""
        return self.text

    @classmethod
    def get_text_size_drawer(cls):
        if cls.text_size_drawer is None:
            cls.text_size_drawer = ImageDraw.Draw(Image.new("RGB", (100, 100)))
        return cls.text_size_drawer

    def get_text_font_path(self):
        weight = self.weight.capitalize()
        if weight == "regular" and self.italic:
            weight = ""
        italic = "Italic" if self.italic else ""
        return self.fonts_path / "roboto" / f"Roboto-{weight}{italic}.ttf"

    @classmethod
    def get_text_font(cls, font_path, size):
        if (font_path, size) not in cls.font_cache:
            cls.font_cache[(font_path, size)] = ImageFont.truetype(str(font_path), size)
        return cls.font_cache[(font_path, size)]

    @classmethod
    def get_emoji_font(cls):
        if "emoji" not in cls.font_cache:
            font_path = cls.fonts_path / "noto-emoji" / "NotoColorEmoji.ttf"
            size = cls.emoji_font_size
            cls.font_cache["emoji"] = ImageFont.truetype(str(font_path), size, layout_engine=ImageFont.LAYOUT_RAQM)
        return cls.font_cache["emoji"]

    @classmethod
    def get_text_size(cls, text, font):
        if text not in cls.text_size_cache.setdefault(font, {}):
            cls.text_size_cache[font][text] = cls.get_text_size_drawer().textsize(text, font=font)
        return cls.text_size_cache[font][text]

    @classmethod
    def get_emoji_image(cls, text, size, top_margin):
        font = cls.get_emoji_font()
        original_key = (cls.emoji_font_size, 0)
        key = (size, top_margin)
        cls.emoji_cache.setdefault(key, {})
        if text not in cls.emoji_cache[original_key]:
            width, height = cls.get_text_size(text, font)
            if width == 0:
                cls.emoji_cache[original_key][text] = cls.emoji_cache[key][text] = None
                return None
            image = Image.new("RGBA", (width, height), "#00000000")
            drawer = ImageDraw.Draw(image)
            drawer.text((0, 0), text, font=font, fill="white", embedded_color=True)
            cls.emoji_cache[original_key][text] = image.crop(image.getbbox())  # auto-crop
        if text not in cls.emoji_cache[key]:
            original_image = cls.emoji_cache[original_key][text]
            if original_image is None:
                cls.emoji_cache[key][text] = None
                return None
            original_width, original_height = original_image.size
            new_height = size - top_margin
            new_width = round(original_width * new_height / original_height)
            image = original_image.resize((new_width, new_height), Image.LANCZOS)
            if top_margin:
                final_image = Image.new("RGBA", (new_width, new_height + top_margin), "#00000000")
                final_image.paste(image, (0, top_margin))
            else:
                final_image = image
            cls.emoji_cache[key][text] = final_image
        return cls.emoji_cache[key][text]

    @classmethod
    def get_emmoji_size(cls, text, size, top_margin):
        if image := cls.get_emoji_image(text, size, top_margin):
            return image.size
        return 0, 0

    @classmethod
    def get_text_or_emoji_size(cls, kind, text, text_font, emoji_size, top_margin):
        if kind == EMOJI:
            return cls.get_emmoji_size(text, emoji_size, top_margin)
        return cls.get_text_size(text, text_font)

    @classmethod
    def finalize_line(cls, parts, text_font, emoji_size, top_margin):
        final_parts = []
        line_height = line_width = 0
        # for kind, text, width, height, part_top_margin in parts:
        for part in parts:
            if part.width is None:
                width, height = cls.get_text_or_emoji_size(part.kind, part.text, text_font, emoji_size, top_margin)
                if not width or not height:
                    continue
                part = part._replace(width=width, height=height)
            line_width += part.width
            if part.height > line_height:
                line_height = part.height
            final_parts.append(part)

        return TextLine(final_parts, line_width, line_height)

    def split_text_on_lines_and_emojis(self, text, max_width, text_font, emoji_size, top_margin):
        emojis = UNICODE_EMOJI["en"] if self.allow_emojis else {}
        if self.allow_emojis:
            text = emojize(text, use_aliases=True, variant="emoji_type")

        # we strip lines and replace all consecutive whitespaces on each line by a single space
        lines = [" ".join(line.split()) for line in text.splitlines()]
        # if we don't have a max width, we keep all non empty lines and put them on a single line
        if not self.wrap:
            lines = [" ".join(line for line in lines if line)]

        while lines and not lines[-1]:
            lines.pop()

        final_lines = []
        total_width = total_height = 0
        for line in lines:
            if not line:
                # we keep empty lines
                line = " "
            parts = []
            current_kind = None
            current_part = []
            for char in line:
                new_kind = (
                    SPACE
                    if char == " "  # we keep spaces appart to help wrapping
                    else (EMOJI if self.allow_emojis and (char in EMOJI_TYPES or char in emojis) else TEXT)
                )
                if new_kind != current_kind:
                    if current_part:
                        parts.append((current_kind, current_part))
                    current_kind = new_kind
                    current_part = []
                current_part.append(char)
            if current_part:
                parts.append((current_kind, current_part))
            line_parts, line_width, line_height = self.finalize_line(
                [TextPart(part_kind, "".join(chars)) for part_kind, chars in parts], text_font, emoji_size, top_margin
            )
            total_height += line_height
            if line_width > total_width:
                total_width = line_width
            final_lines.append(TextLine(line_parts, line_width, line_height))

        nb_forced_wrap = 0
        if total_width > max_width and self.wrap:
            final_lines, total_width, total_height, nb_forced_wrap = self.wrap_parts(
                final_lines, max_width, text_font, emoji_size, top_margin
            )

        return final_lines, total_width, total_height - top_margin, top_margin, nb_forced_wrap

    @classmethod
    def wrap_parts(cls, lines, max_width, text_font, emoji_size, top_margin):
        def get_text_size(text, source_part):
            return cls.get_text_or_emoji_size(source_part.kind, text, text_font, emoji_size, top_margin)

        def text_part(text, source_part, width=None, height=None):
            if width is None:
                width, height = get_text_size(text, source_part)
            return TextPart(source_part.kind, text, width, height)

        def finish_line():
            nonlocal line_remaining_width
            parts = current_parts
            if parts:
                # remove leading space
                if (part := parts[0]).text.startswith(" "):
                    parts = ([text_part(part.text[1:], part)] if part.text != " " else []) + parts[1:]
                # remove trailing space
                if (part := parts[-1]).text.endswith(" "):
                    parts = parts[:-1] + ([text_part(part.text[:-1], part)] if part.text != " " else [])

                final_lines.append(cls.finalize_line(parts, text_font, emoji_size, top_margin))

            line_remaining_width = max_width
            current_parts[:] = []

        def add_part(part):
            nonlocal line_remaining_width, parts_remaining_width
            current_parts.append(part)
            line_remaining_width -= part.width
            parts_remaining_width -= part.width

        def unshift_part(part):
            remaining_parts.insert(0, part)

        final_lines = []
        nb_forced_wrap = 0
        for line in lines:

            # if we have a line that is short enough, we can add it entirely
            if line.width <= max_width:
                final_lines.append(line)
                continue

            # else we have to manage the different parts of the line

            current_parts = []
            line_remaining_width = max_width
            parts_remaining_width = line.width

            remaining_parts = list(line.parts)
            while remaining_parts:
                part = remaining_parts.pop(0)

                # if we are at the start of a line and the part is a space, we ignore it
                if line_remaining_width == max_width and part.kind == SPACE:
                    continue

                # if the text is short enough to fit in the remaining space we can add the part directly
                if part.width <= line_remaining_width:
                    add_part(part)
                    continue

                # if there is only one character but bigger than the whole line, we place it in its own line
                if len(part.text) == 1 and line_remaining_width == max_width:
                    # so we end the current line if anything in it
                    finish_line()
                    # then we add our line with our character
                    add_part(part)
                    finish_line()
                    # the remaining parts will then be analyzed for a new line
                    continue

                # our part does not fit...

                # if it fits in a new line, we'll end the current line and add the part back to the remaining parts
                if part.width <= max_width:
                    finish_line()
                    unshift_part(part)
                    continue

                # ok here our part does not fit in a whole line, so we extrat the text that fit to
                # put in on the current line, then we'll start a new line for the reste
                nb_forced_wrap += 1
                for size in range(1, len(part.text) + 1):
                    text = part.text[:size]
                    width, height = get_text_size(text, part)
                    if width <= line_remaining_width:
                        continue
                    if size == 1:
                        # if the first char does not fit
                        if width > max_width:
                            # if it does not fit even the whole line, we end the current line
                            # and then we add it itself as a part (will be handled above), and the rest too
                            finish_line()
                            unshift_part(
                                text_part(part.text[1:], part)  # now at pos 0, will be at 1 on unshift_part next line
                            )
                            unshift_part(text_part(text, part, width, height))
                        else:
                            # in the other case (first char does not fit the remaining, but fit in a whole line) we
                            # simply finish the whole line and move the whole part to be analyzed again
                            finish_line()
                            unshift_part(part)
                    else:
                        # we add the text that fit (the one without the current char) into the current line
                        # that we finish, and we move the remaining part to be analyzed again
                        add_part(text_part(part.text[: size - 1], part))
                        finish_line()
                        unshift_part(text_part(part.text[size - 1 :], part))
                    break

            finish_line()

        return (
            final_lines,
            max(line.width for line in final_lines),
            sum(line.height for line in final_lines),
            nb_forced_wrap,
        )

    def on_file_content_changed(self):
        super().on_file_content_changed()
        self.stop_scroller()
        if self.mode != "text":
            self.text = None
        self._complete_image = self.compose_cache = None
        self.key.on_image_changed()
        for reference in self.referenced_by:
            reference.on_file_content_changed()

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

    def prepare_text(self, text, max_width, max_height):
        font_path = self.get_text_font_path()

        def _prepare_text(text_size):
            text_font = self.get_text_font(font_path, text_size)
            top_margin = text_font.font.height - text_size
            emoji_size = text_font.getmetrics()[0]

            lines, width, height, top_margin, nb_forced_wrap = self.split_text_on_lines_and_emojis(
                text, max_width, text_font, emoji_size, top_margin
            )
            return PreparedText(lines, width, height, top_margin, nb_forced_wrap, emoji_size, text_font)

        if not self.fit:
            return _prepare_text(self.convert_coordinate(self.size, "height"))

        min_bound = min_size = round(max_width * 0.1)
        max_bound = round(max_width * 1.2)

        fit_size = fit_prepared_text = None
        while min_bound < max_bound:
            size = (min_bound + max_bound) // 2
            if size in (min_bound, max_bound):
                break
            prepared_text = _prepare_text(size)
            if prepared_text.width > max_width or prepared_text.height > max_height:
                if max_bound == size:
                    break
                max_bound = size
            else:
                if min_bound == size:
                    break
                fit_size = min_bound = size
                fit_prepared_text = prepared_text

        if fit_prepared_text is None:
            fit_size = min_size
            fit_prepared_text = _prepare_text(fit_size)

        return fit_prepared_text

    def _compose_base(self):
        text = self.resolved_text
        if not text or not text.strip():
            return None

        image_size = self.key.image_size
        margins = self.convert_margins()
        max_width = image_size[0] - (margins["right"] + margins["left"])
        max_height = image_size[1] - (margins["top"] + margins["bottom"])

        prepared_text = self.prepare_text(text, max_width, max_height)
        total_width, total_height = prepared_text.width, prepared_text.height

        image = Image.new("RGBA", (total_width, total_height), "#00000000")
        drawer = ImageDraw.Draw(image)

        pos_y = -prepared_text.top_margin
        for line_index, line in enumerate(prepared_text.lines):
            pos_x = 0
            if self.align == "right":
                pos_x = total_width - line.width
            elif self.align == "center":
                pos_x = round((total_width - line.width) / 2)
            for part in line.parts:
                if part.kind == EMOJI:
                    image.paste(
                        self.get_emoji_image(part.text, prepared_text.emoji_size, prepared_text.top_margin),
                        (pos_x, pos_y),
                    )
                else:
                    drawer.text((pos_x, pos_y), part.text, font=prepared_text.text_font, fill=self.color)
                pos_x += part.width
            pos_y += line.height

        self.apply_opacity(image)

        self._complete_image = {
            "image": image,
            "max_width": max_width,
            "max_height": max_height,
            "total_width": total_width,
            "total_height": total_height,
            "margins": margins,
            "default_crop": None,
            "visible_width": total_width,
            "visible_height": total_height,
            "fixed_position_top": None,
            "fixed_position_left": None,
        }

        align = self.align
        valign = self.valign
        if total_width > max_width or total_height > max_height:
            crop = {}
            if total_width <= max_width:
                crop["left"] = 0
                crop["right"] = total_width
            else:
                if self.scroll and not self.wrap and self.scroll_pixels:
                    self.scrollable = align = "left" if self.scroll_pixels > 0 else "right"
                self._complete_image["fixed_position_left"] = margins["left"]
                if align == "left":
                    crop["left"] = 0
                    crop["right"] = max_width
                elif align == "right":
                    crop["left"] = total_width - max_width
                    crop["right"] = total_width
                else:  # center
                    crop["left"] = round((total_width - max_width) / 2)
                    crop["right"] = round((total_width + max_width) / 2)
                self._complete_image["visible_width"] = max_width

            if total_height <= max_height:
                crop["top"] = 0
                crop["bottom"] = total_height
            else:
                if self.scroll and self.wrap and self.scroll_pixels:
                    self.scrollable = valign = "top" if self.scroll_pixels > 0 else "bottom"
                self._complete_image["fixed_position_top"] = margins["top"]
                if valign == "top":
                    crop["top"] = 0
                    crop["bottom"] = max_height
                elif valign == "bottom":
                    crop["top"] = total_height - max_height
                    crop["bottom"] = total_height
                else:  # middle
                    crop["top"] = round((total_height - max_height) / 2)
                    crop["bottom"] = round((total_height + max_height) / 2)
                self._complete_image["visible_height"] = max_height

            self._complete_image["default_crop"] = crop

        self._complete_image["align"] = align
        self._complete_image["valign"] = valign

        return self._complete_image

    def _compose_final(self):
        if not (ci := self._complete_image):
            return None

        if not (crop := ci["default_crop"]):
            final_image = ci["image"]
        else:
            if self.scrollable:
                if self.scrolled is None:
                    self.scrolled = 0
                else:
                    crop = crop.copy()
                    sign = self.scroll_pixels // abs(self.scroll_pixels)
                    if self.scrollable in ("left", "right"):
                        if sign * self.scrolled >= ci["total_width"]:
                            self.scrolled = -sign * ci["max_width"]
                        crop["left"] += self.scrolled
                        crop["right"] = max(crop["right"] + self.scrolled, ci["total_width"])
                    else:  # top, bottom
                        if sign * self.scrolled >= ci["total_height"]:
                            self.scrolled = -sign * ci["max_height"]
                        crop["top"] += self.scrolled
                        crop["bottom"] = max(crop["bottom"] + self.scrolled, ci["total_height"])

            final_image = ci["image"].crop((crop["left"], crop["top"], crop["right"], crop["bottom"]))

        if (left := ci["fixed_position_left"]) is None:
            if (align := ci["align"]) == "left":
                left = ci["margins"]["left"]
            elif align == "right":
                left = self.key.width - ci["margins"]["right"] - ci["visible_width"]
            else:  # center
                left = ci["margins"]["left"] + round((ci["max_width"] - ci["visible_width"]) / 2)

        if (top := ci["fixed_position_top"]) is None:
            if (valign := ci["valign"]) == "top":
                top = ci["margins"]["top"]
            elif valign == "bottom":
                top = self.key.height - ci["margins"]["bottom"] - ci["visible_height"]
            else:  # middle
                top = ci["margins"]["top"] + round((ci["max_height"] - ci["visible_height"]) / 2)

        return final_image, left, top, final_image

    @property
    def scroll_pixels(self):
        if not hasattr(self, "_scroll_pixels"):
            self._scroll_pixels = self.convert_coordinate(self.scroll, "height" if self.wrap else "width")
        return self._scroll_pixels

    def start_scroller(self):
        if self.scroll_thread or not self.scrollable:
            return
        self.scrolled = 0
        self.scrolled_at = time() + self.SCROLL_WAIT
        self.scroll_thread = Repeater(
            self.do_scroll,
            max(RENDER_IMAGE_DELAY, 1 / abs(self.scroll_pixels)),
            wait_first=self.SCROLL_WAIT,
            name=f'TxtScrol{self.page.number}.{self.key.row}{self.key.col}{(".%s" % self.line) if self.line and self.line != -1 else ""}',
        )
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
