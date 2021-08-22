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

from PIL import Image, ImageDraw, ImageEnhance

from .base import RE_PARTS, EntityFile, InvalidArg
from .key import KeyContent


@dataclass(eq=False)
class KeyImagePart(KeyContent, EntityFile):

    no_margins = {"top": ("int", 0), "right": ("int", 0), "bottom": ("int", 0), "left": ("int", 0)}

    allowed_args = EntityFile.allowed_args | {
        "opacity": re.compile(r"^(?P<arg>opacity)=(?P<value>" + RE_PARTS["0-100"] + ")$"),
        "margin": re.compile(
            r"^(?P<arg>margin)=(?P<top>-?"
            + RE_PARTS["% | number"]
            + "),(?P<right>-?"
            + RE_PARTS["% | number"]
            + "),(?P<bottom>-?"
            + RE_PARTS["% | number"]
            + "),(?P<left>-?"
            + RE_PARTS["% | number"]
            + ")$"
        ),
        "margin.": re.compile(
            r"^(?P<arg>margin\.(?:[0123]|top|right|bottom|left))=(?P<value>-?" + RE_PARTS["% | number"] + ")$"
        ),
    }
    allowed_partial_args = EntityFile.allowed_partial_args | {
        "margin": re.compile(r"^margin\.(?:[0123]|top|right|bottom|left)$"),
    }

    def __str__(self):
        return f"{self.key}, {self.str}"

    def __post_init__(self):
        super().__post_init__()
        self.compose_cache = None

    def version_activated(self):
        super().version_activated()
        if self.disabled or self.key.disabled or self.page.disabled:
            return
        self.key.on_image_changed()

    def version_deactivated(self):
        super().version_deactivated()
        if self.disabled or self.key.disabled or self.page.disabled:
            return
        self.key.on_image_changed()

    def _compose(self):
        raise NotImplementedError

    def compose(self):
        if not self.compose_cache:
            self.compose_cache = self._compose()
        return self.compose_cache

    @staticmethod
    def parse_value_or_percent(value):
        kind = "int"
        if value.endswith("%"):
            kind = "%"
            value = float(value[:-1])
        else:
            value = int(value)
        return kind, value

    def convert_coordinate(self, value, dimension, source=None):
        kind, value = value
        if kind == "int":
            return value
        if kind == "%":
            return int(value * (getattr(self.key if source is None else source, dimension) - 1) / 100)

    @staticmethod
    def convert_angle(value):
        kind, value = value
        if kind == "int":
            return value
        if kind == "%":
            return round(value * 360 / 100)

    def convert_margins(self):
        return {
            margin_name: self.convert_coordinate(margin, "width" if margin_name in ("left", "right") else "height")
            for margin_name, margin in (self.margin or self.no_margins).items()
        }

    def apply_opacity(self, image):
        if self.opacity is None:
            return
        image.putalpha(ImageEnhance.Brightness(image.getchannel("A")).enhance(self.opacity / 100))


@dataclass(eq=False)
class KeyImageLayer(KeyImagePart):
    path_glob = "IMAGE*"
    main_part_re = re.compile(r"^(?P<kind>IMAGE)$")
    main_part_compose = lambda args: "IMAGE"
    get_main_args = lambda self: {"kind": "IMAGE"}

    allowed_args = KeyImagePart.allowed_args | {
        "layer": re.compile(r"^(?P<arg>layer)=(?P<value>\d+)$"),
        "ref": re.compile(  # we'll use -1 if no layer given
            r"^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<layer>.*)$"
        ),
        "colorize": re.compile(r"^(?P<arg>colorize)=(?P<value>" + RE_PARTS["color"] + ")$"),
        "crop": re.compile(
            r"^(?P<arg>crop)=(?P<left>"
            + RE_PARTS["% | number"]
            + "),(?P<top>"
            + RE_PARTS["% | number"]
            + "),(?P<right>"
            + RE_PARTS["% | number"]
            + "),(?P<bottom>"
            + RE_PARTS["% | number"]
            + ")$"
        ),
        "crop.": re.compile(
            r"^(?P<arg>crop\.(?:[0123]|left|top|right|bottom))=(?P<value>" + RE_PARTS["% | number"] + ")$"
        ),
        "rotate": re.compile(r"^(?P<arg>rotate)=(?P<value>-?" + RE_PARTS["% | number"] + ")$"),
        "draw": re.compile(
            r"^(?P<arg>draw)=(?P<value>line|rectangle|fill|points|polygon|ellipse|arc|chord|pieslice)$"
        ),
        "coords": re.compile(
            r"^(?P<arg>coords)=(?P<value>-?"
            + RE_PARTS["% | number"]
            + ",-?"
            + RE_PARTS["% | number"]
            + "(?:,-?"
            + RE_PARTS["% | number"]
            + ",-?"
            + RE_PARTS["% | number"]
            + ")*)$"
        ),
        "coords.": re.compile(r"^(?P<arg>coords\.\d+)=(?P<value>-?" + RE_PARTS["% | number"] + ")$"),
        "outline": re.compile(r"^(?P<arg>outline)=(?P<value>" + RE_PARTS["color & alpha?"] + ")$"),
        "fill": re.compile(r"^(?P<arg>fill)=(?P<value>" + RE_PARTS["color & alpha?"] + ")$"),
        "width": re.compile(r"^(?P<arg>width)=(?P<value>\d+)$"),
        "radius": re.compile(r"^(?P<arg>radius)=(?P<value>\d+)$"),
        "angles": re.compile(
            r"^(?P<arg>angles)=(?P<value>-?" + RE_PARTS["% | number"] + ",-?" + RE_PARTS["% | number"] + ")$"
        ),
        "angles.": re.compile(r"^(?P<arg>angles\.[01])=(?P<value>-?" + RE_PARTS["% | number"] + ")$"),
    }
    allowed_partial_args = KeyImagePart.allowed_partial_args | {
        "crop": re.compile(r"^crop\.(?:[0123]|top|right|bottom|left)$"),
        "coords": re.compile(r"^coords\.\d+$"),
        "angles": re.compile(r"^angles\.[01]$"),
    }

    identifier_attr = "layer"
    parent_container_attr = "layers"

    layer: int

    def __post_init__(self):
        super().__post_init__()
        self.color = None
        self.margin = None
        self.crop = None
        self.opacity = None
        self.rotate = None
        self.draw = None
        self.draw_coords = None
        self.draw_outline_color = None
        self.draw_fill_color = None
        self.draw_outline_width = None
        self.draw_radius = None
        self.draw_angles = None

    @property
    def str(self):
        return f'LAYER{(" %s" % self.layer) if self.layer != -1 else ""} ({self.name}{", disabled" if self.disabled else ""})'

    @classmethod
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)

        if len([1 for key in ("draw", "file") if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "draw", "file"')

        final_args["layer"] = int(args["layer"]) if "layer" in args else -1  # -1 for image used if no layers
        for name in ("margin", "crop"):
            if name not in args:
                continue
            final_args[name] = {}
            for part, val in list(args[name].items()):
                final_args[name][part] = cls.parse_value_or_percent(val)
        if "colorize" in args:
            final_args["color"] = args["colorize"]
        if "opacity" in args:
            final_args["opacity"] = int(args["opacity"])
        if "rotate" in args:
            # we negate the given value because PIL rotates counterclockwise
            final_args["rotate"] = -cls.convert_angle(cls.parse_value_or_percent(args["rotate"]))
        if "draw" in args:
            final_args["mode"] = "draw"
            if args["draw"] == "fill":
                args.update(
                    {
                        "draw": "rectangle",
                        "coords": "0,0,100%,100%",
                        "width": "0",
                    }
                )
            final_args["draw"] = args["draw"]
            if "coords" in args:
                final_args["draw_coords"] = tuple(
                    cls.parse_value_or_percent(val) for val in args["coords"].split(",")
                )
            final_args["draw_outline_color"] = args.get("outline") or "white"
            if "fill" in args:
                final_args["draw_fill_color"] = args["fill"]
            final_args["draw_outline_width"] = int(args.get("width") or 1)
            if "radius" in args:
                final_args["draw_radius"] = int(args["radius"])
            if "angles" in args:
                # we remove 90 degres from given values because PIL starts at 3 o'clock
                final_args["draw_angles"] = tuple(
                    cls.convert_angle(cls.parse_value_or_percent(val)) - 90 for val in args["angles"].split(",")
                )
        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        layer = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in (
            "margin",
            "crop",
            "color",
            "opacity",
            "rotate",
            "draw",
            "draw_coords",
            "draw_outline_color",
            "draw_fill_color",
            "draw_outline_width",
            "draw_radius",
            "draw_angles",
        ):
            if key not in args:
                continue
            setattr(layer, key, args[key])
        layer.check_file_exists()
        return layer

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, key = cls.find_reference_key(parent, ref_conf)
        if not final_ref_conf.get("layer"):
            final_ref_conf["layer"] = -1
        if not key:
            return final_ref_conf, None
        return final_ref_conf, key.find_layer(final_ref_conf["layer"])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for key, path, parent, ref_conf in self.iter_waiting_references_for_key(self.key)
            if (layer := key.find_layer(ref_conf["layer"])) and layer.layer == self.layer
        ]

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if args["layer"] == int(filter):
                return True
        except ValueError:
            pass
        return args.get("name") == filter

    def on_file_content_changed(self):
        super().on_file_content_changed()
        self.compose_cache = None
        self.key.on_image_changed()
        for reference in self.referenced_by:
            reference.on_file_content_changed()

    @property
    def resolved_image_path(self):
        if self.mode == "content":
            self.track_symlink_dir()
            return self.resolved_path
        if self.mode in ("file", "inside"):
            return self.get_file_path()

    def _compose(self):

        image_size = self.key.image_size
        if self.mode == "draw":
            layer_image = Image.new("RGBA", image_size)
            drawer = ImageDraw.Draw(layer_image)
            coords = [
                self.convert_coordinate(coord, "height" if index % 2 else "width")
                for index, coord in enumerate(self.draw_coords)
            ]
            if self.draw == "line":
                drawer.line(coords, fill=self.draw_outline_color, width=self.draw_outline_width)
            elif self.draw == "rectangle":
                if self.draw_radius:
                    drawer.rounded_rectangle(
                        coords,
                        outline=self.draw_outline_color,
                        fill=self.draw_fill_color,
                        width=self.draw_outline_width,
                        radius=self.draw_radius,
                    )
                else:
                    drawer.rectangle(
                        coords,
                        outline=self.draw_outline_color,
                        fill=self.draw_fill_color,
                        width=self.draw_outline_width,
                    )
            elif self.draw == "points":
                drawer.point(coords, fill=self.draw_outline_color)
            elif self.draw == "polygon":
                drawer.polygon(coords, outline=self.draw_outline_color, fill=self.draw_fill_color)
            elif self.draw == "ellipse":
                drawer.ellipse(
                    coords, outline=self.draw_outline_color, fill=self.draw_fill_color, width=self.draw_outline_width
                )
            elif self.draw == "arc":
                drawer.arc(
                    coords,
                    start=self.draw_angles[0],
                    end=self.draw_angles[1],
                    fill=self.draw_outline_color,
                    width=self.draw_outline_width,
                )
            elif self.draw == "chord":
                drawer.chord(
                    coords,
                    start=self.draw_angles[0],
                    end=self.draw_angles[1],
                    outline=self.draw_outline_color,
                    fill=self.draw_fill_color,
                    width=self.draw_outline_width,
                )
            elif self.draw == "pieslice":
                drawer.pieslice(
                    coords,
                    start=self.draw_angles[0],
                    end=self.draw_angles[1],
                    outline=self.draw_outline_color,
                    fill=self.draw_fill_color,
                    width=self.draw_outline_width,
                )

        else:
            if not (image_path := self.resolved_image_path):
                return None
            layer_image = Image.open(image_path)

        if self.crop:
            crops = {
                crop_name: self.convert_coordinate(
                    crop, "width" if crop_name in ("left", "right") else "height", source=layer_image
                )
                for crop_name, crop in self.crop.items()
            }
            layer_image = layer_image.crop((crops["left"], crops["top"], crops["right"], crops["bottom"]))

        if self.rotate:
            layer_image = layer_image.rotate(self.rotate)

        margins = self.convert_margins()
        max_width = image_size[0] - (margins["right"] + margins["left"])
        max_height = image_size[1] - (margins["top"] + margins["bottom"])
        final_image = layer_image.convert("RGBA")
        if max_width > (width := final_image.width) and max_height > (height := final_image.height):
            # as the `thumbnail` method does not enlarge image, we need to do the work ourselves
            ratio = width / height
            if (max_height - height) <= (max_width - width):
                new_height = max_height
                new_width = round(new_height * ratio)
            else:
                new_width = max_width
                new_height = round(new_width / ratio)
            final_image = final_image.resize((new_width, new_height), Image.LANCZOS)
        else:
            final_image.thumbnail((max_width, max_height), Image.LANCZOS)
        position_x = margins["left"] + round((max_width - final_image.width) / 2)
        position_y = margins["top"] + round((max_height - final_image.height) / 2)

        if self.color:
            alpha = final_image.getchannel("A")
            final_image = Image.new("RGBA", final_image.size, color=self.color)
            final_image.putalpha(alpha)

        self.apply_opacity(final_image)

        return final_image, position_x, position_y, final_image
