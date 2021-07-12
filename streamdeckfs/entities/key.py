#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
import re
from dataclasses import dataclass
from time import time
from typing import Tuple

from cached_property import cached_property
from PIL import Image
from StreamDeck.ImageHelpers import PILHelper

from ..common import Manager, file_flags, logger
from .base import (
    FILTER_DENY,
    NOT_HANDLED,
    Entity,
    EntityDir,
    ParseFilenameResult,
    versions_dict_factory,
)
from .page import PageContent


@dataclass(eq=False)
class Key(EntityDir, PageContent):

    path_glob = "KEY_ROW_*_COL_*"
    main_part_re = re.compile(r"^(?P<kind>KEY)_ROW_(?P<row>\d+)_COL_(?P<col>\d+)$")
    main_part_compose = lambda args: f'KEY_ROW_{args["row"]}_COL_{args["col"]}'

    allowed_args = EntityDir.allowed_args | {
        "ref": re.compile(r"^(?P<arg>ref)=(?P<page>.*):(?P<key>.*)$"),  # we'll use current row,col if no key given
    }

    identifier_attr = "key"
    parent_container_attr = "keys"

    key: Tuple[int, int]

    @cached_property
    def event_class(self):
        from . import KeyEvent

        return KeyEvent

    @cached_property
    def var_class(self):
        from . import KeyVar

        return KeyVar

    def __post_init__(self):
        super().__post_init__()
        self.compose_image_cache = None
        self.pressed_at = None
        self.layers = versions_dict_factory()
        self.text_lines = versions_dict_factory()
        self.rendered_overlay = None

    @property
    def row(self):
        return self.key[0]

    @property
    def col(self):
        return self.key[1]

    @property
    def width(self):
        return self.deck.key_width

    @property
    def height(self):
        return self.deck.key_height

    @property
    def str(self):
        return f'KEY {self.key} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f"{self.page}, {self.str}"

    @classmethod
    def convert_main_args(cls, args):
        if (args := super().convert_main_args(args)) is None:
            return None
        args["row"] = int(args["row"])
        args["col"] = int(args["col"])
        return args

    @classmethod
    def parse_filename(cls, name, parent):
        parsed = super().parse_filename(name, parent)
        if (main := parsed.main) is not None:
            if (
                main["row"] < 1
                or main["row"] > parent.deck.nb_rows
                or main["col"] < 1
                or main["col"] > parent.deck.nb_cols
            ):
                return ParseFilenameResult()
        return parsed

    def on_create(self):
        super().on_create()
        Manager.add_watch(self.path, self)
        self.read_directory()

    @property
    def resolved_layers(self):
        if not self.reference:
            return self.layers
        layers = {}
        for num_layer, layer in self.layers.items():
            if layer:
                layers[num_layer] = layer
        for num_layer, layer in self.reference.resolved_layers.items():
            if num_layer not in layers and layer:
                layers[num_layer] = layer
        return layers

    @property
    def resolved_text_lines(self):
        if not self.reference:
            return self.text_lines
        text_lines = {}
        for line, text_line in self.text_lines.items():
            if text_line:
                text_lines[line] = text_line
        for line, text_line in self.reference.resolved_text_lines.items():
            if line not in text_lines and text_line:
                text_lines[line] = text_line
        return text_lines

    def on_delete(self):
        Manager.remove_watch(self.path, self)
        for layer in self.iter_all_children_versions(self.layers):
            layer.on_delete()
        for text_line in self.iter_all_children_versions(self.text_lines):
            text_line.on_delete()
        super().on_delete()

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, page = cls.find_reference_page(parent, ref_conf)
        if not final_ref_conf.get("key"):
            final_ref_conf["key"] = str(f"{main['row']},{main['col']}")
        if not page:
            return final_ref_conf, None
        return final_ref_conf, page.find_key(final_ref_conf["key"])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for page, path, parent, ref_conf in self.iter_waiting_references_for_page(self.page)
            if (key := page.find_key(ref_conf["key"])) and key.key == self.key
        ]

    def read_directory(self):
        super().read_directory()
        if self.deck.filters.get("layer") != FILTER_DENY:
            from . import KeyImageLayer

            for image_file in sorted(self.path.glob(KeyImageLayer.path_glob)):
                self.on_file_change(
                    self.path,
                    image_file.name,
                    file_flags.CREATE | (file_flags.ISDIR if image_file.is_dir() else 0),
                    entity_class=KeyImageLayer,
                )
        if self.deck.filters.get("text_line") != FILTER_DENY:
            from . import KeyTextLine

            for text_file in sorted(self.path.glob(KeyTextLine.path_glob)):
                self.on_file_change(
                    self.path,
                    text_file.name,
                    file_flags.CREATE | (file_flags.ISDIR if text_file.is_dir() else 0),
                    entity_class=KeyTextLine,
                )

    def on_file_change(self, directory, name, flags, modified_at=None, entity_class=None):
        if directory != self.path:
            return
        if (result := super().on_file_change(directory, name, flags, modified_at, entity_class)) is not NOT_HANDLED:
            return result
        path = self.path / name
        if (layer_filter := self.deck.filters.get("layer")) != FILTER_DENY:
            from . import KeyImageLayer

            if not entity_class or entity_class is KeyImageLayer:
                if (parsed := KeyImageLayer.parse_filename(name, self)).main:
                    if layer_filter is not None and not KeyImageLayer.args_matching_filter(
                        parsed.main, parsed.args, layer_filter
                    ):
                        return None
                    return self.on_child_entity_change(
                        path=path,
                        flags=flags,
                        entity_class=KeyImageLayer,
                        data_identifier=parsed.args["layer"],
                        args=parsed.args,
                        ref_conf=parsed.ref_conf,
                        ref=parsed.ref,
                        used_vars=parsed.used_vars,
                        modified_at=modified_at,
                    )
                elif parsed.ref_conf:
                    KeyImageLayer.add_waiting_reference(self, path, parsed.ref_conf)
        if (text_line_filter := self.deck.filters.get("text_line")) != FILTER_DENY:
            from . import KeyTextLine

            if not entity_class or entity_class is KeyTextLine:
                if (parsed := KeyTextLine.parse_filename(name, self)).main:
                    if text_line_filter is not None and not KeyTextLine.args_matching_filter(
                        parsed.main, parsed.args, text_line_filter
                    ):
                        return None
                    return self.on_child_entity_change(
                        path=path,
                        flags=flags,
                        entity_class=KeyTextLine,
                        data_identifier=parsed.args["line"],
                        args=parsed.args,
                        ref_conf=parsed.ref_conf,
                        ref=parsed.ref,
                        used_vars=parsed.used_vars,
                        modified_at=modified_at,
                    )
                elif parsed.ref_conf:
                    KeyTextLine.add_waiting_reference(self, path, parsed.ref_conf)

    def on_directory_removed(self, directory):
        pass

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if (main["row"], main["col"]) == tuple(int(val) for val in filter.split(",")):
                return True
        except ValueError:
            pass
        return args.get("name") == filter

    def on_image_changed(self):
        self.compose_image_cache = None
        self.render()
        for reference in self.referenced_by:
            reference.on_image_changed()

    @property
    def image_size(self):
        return self.width, self.height

    @staticmethod
    def sort_layers(layers):
        return {num_layer: layer for num_layer, layer in sorted(layers.items()) if layer}

    @staticmethod
    def sort_text_lines(text_lines):
        return {line: text_line for line, text_line in sorted(text_lines.items()) if text_line}

    def compose_image(self, overlay_level=0):
        if not self.compose_image_cache:
            layers = self.resolved_layers
            text_lines = self.resolved_text_lines
            try:
                if not layers and not text_lines:
                    self.compose_image_cache = (None, None)
                else:
                    layers = self.sort_layers(layers) if layers else {}
                    if layers:
                        if len(layers) > 1:
                            # if more than one layer, we ignore the image used if no specific layers
                            layers.pop(-1, None)
                    text_lines = self.sort_text_lines(text_lines) if text_lines else {}
                    if text_lines:
                        if len(text_lines) > 1:
                            # if more than one text line, we ignore the one used if no specific lines
                            text_lines.pop(-1, None)
                    if not layers and not text_lines:
                        self.compose_image_cache = None, None
                    else:
                        all_layers = list(layers.values()) + list(text_lines.values())
                        final_image = Image.new("RGB", self.image_size, "black")
                        for layer in all_layers:
                            try:
                                if (composed := layer.compose()) is None:
                                    continue
                                rendered_layer, position_x, position_y, mask = composed
                            except Exception:
                                logger.error(
                                    f"[{layer}] Layer could not be rendered", exc_info=logger.level <= logging.DEBUG
                                )
                                continue  # we simply ignore a layer that couldn't be created
                            final_image.paste(rendered_layer, (position_x, position_y), mask)
                        self.compose_image_cache = final_image, PILHelper.to_native_format(
                            self.deck.device, final_image
                        )
            except Exception:
                logger.error(f"[{self}] Image could not be rendered", exc_info=logger.level <= logging.DEBUG)
                self.compose_image_cache = None, None

        if overlay_level and (image := self.compose_image_cache[0]):
            image_data = PILHelper.to_native_format(
                self.deck.device, Image.eval(image, lambda x: x / (1 + 3 * overlay_level))
            )
        else:
            image_data = self.compose_image_cache[1] if self.compose_image_cache[0] else None

        return image_data

    def has_content(self):
        if any(self.resolved_events.values()):
            return True
        if any(self.resolved_layers.values()) or any(self.resolved_text_lines.values()):
            return True  # self.compose_image() is not None
        return False

    def render(self):
        if not self.deck.is_running:
            return
        visible, overlay_level, key_below, key_above = key_visibility = self.deck.get_key_visibility(
            self.page.number, self.key
        )
        if (has_content := self.has_content()) and visible:
            self.deck.set_image(self.row, self.col, self.compose_image(overlay_level))
            for text_line in self.resolved_text_lines.values():
                if text_line:
                    text_line.start_scroller()
            self.activate_events()
            self.rendered_overlay = overlay_level
        elif not has_content:
            self.unrender(key_visibility=key_visibility)

    def unrender(self, clear_image=True, key_visibility=None):
        if self.rendered_overlay is None:
            return
        if key_visibility is None:
            key_visibility = self.deck.get_key_visibility(self.page.number, self.key)
        visible, overlay_level, key_below, key_above = key_visibility
        for text_line in self.resolved_text_lines.values():
            if text_line:
                text_line.stop_scroller()
        if visible and clear_image:
            self.deck.remove_image(self.row, self.col)
        self.deactivate_events()
        self.rendered_overlay = None
        if key_below:
            key_below.render()

    def version_activated(self):
        super().version_activated()
        if self.disabled or self.page.disabled:
            return
        self.render()

    def version_deactivated(self):
        super().version_deactivated()
        if self.disabled or self.page.disabled:
            return
        self.unrender()

    def find_layer(self, layer_filter, allow_disabled=False):
        from . import KeyImageLayer

        return KeyImageLayer.find_by_identifier_or_name(
            self.resolved_layers, layer_filter, int, allow_disabled=allow_disabled
        )

    def find_text_line(self, text_line_filter, allow_disabled=False):
        from . import KeyTextLine

        return KeyTextLine.find_by_identifier_or_name(
            self.resolved_text_lines, text_line_filter, int, allow_disabled=allow_disabled
        )

    @property
    def press_duration(self):
        # return value is in milliseconds
        if not self.pressed_at:
            return None
        return (time() - self.pressed_at) * 1000

    def pressed(self):
        self.pressed_at = time()
        events = self.resolved_events
        if longpress_event := events.get("longpress"):
            logger.debug(f"[{self}] PRESSED. WAITING LONGPRESS.")
            longpress_event.wait_run_and_repeat(on_press=True)
        if not (press_event := events.get("press")):
            logger.debug(f"[{self}] PRESSED. IGNORED (event not configured)")
            return
        logger.debug(f"[{press_event}] PRESSED.")
        press_event.wait_run_and_repeat(on_press=True)

    def released(self):
        try:
            events = self.resolved_events
            duration = self.press_duration or None
            for event_name in ("press", "longpress"):
                if event := events.get(event_name):
                    event.stop_repeater()
                    if event.duration_thread:
                        event.stop_duration_waiter()

            str_delay_part = f" (after {duration}ms)" if duration is not None else ""
            if not (release_event := events.get("release")):
                logger.debug(f"[{self}] RELEASED{str_delay_part}. IGNORED (event not configured)")
                return
            if release_event.duration_min and (duration is None or duration < release_event.duration_min):
                logger.debug(
                    f"[{release_event}] RELEASED{str_delay_part}. ABORTED (not pressed long enough, less than {release_event.duration_min}ms"
                )
            else:
                logger.debug(f"[{release_event}] RELEASED{str_delay_part}.")
                release_event.wait_run_and_repeat()
        finally:
            self.pressed_at = None

    @cached_property
    def env_vars(self):
        return self.page.env_vars | self.finalize_env_vars(
            {
                "key": f"{self.row},{self.col}",
                "key_row": self.row,
                "key_col": self.col,
                "key_name": "" if self.name == self.unnamed else self.name,
                "key_directory": self.path,
            }
        )


@dataclass(eq=False)
class KeyContent(Entity):
    parent_attr = "key"

    key: "Key"

    @property
    def page(self):
        return self.key.page

    @property
    def deck(self):
        return self.page.deck

    @classmethod
    def find_reference_key(cls, parent, ref_conf):
        final_ref_conf = ref_conf.copy()
        if ref_page := ref_conf.get("page"):
            if not (page := parent.deck.find_page(ref_page)):
                return final_ref_conf, None
        else:
            final_ref_conf["page"] = page = parent.page
        if ref_key := ref_conf.get("key"):
            if not (key := page.find_key(ref_key)):
                return final_ref_conf, None
        else:
            final_ref_conf["key"] = key = parent

        return final_ref_conf, key

    @classmethod
    def iter_waiting_references_for_key(cls, check_key):
        for path, (parent, ref_conf) in check_key.children_waiting_for_references.get(cls, {}).items():
            yield check_key, path, parent, ref_conf
        for path, (parent, ref_conf) in check_key.page.children_waiting_for_references.get(cls, {}).items():
            if (key := check_key.page.find_key(ref_conf["key"])) and key.key == check_key.key:
                yield key, path, parent, ref_conf
        for path, (parent, ref_conf) in check_key.deck.children_waiting_for_references.get(cls, {}).items():
            if (
                (page := check_key.deck.find_page(ref_conf["page"]))
                and page.number == check_key.page.number
                and (key := page.find_key(ref_conf["key"]))
                and key.key == check_key.key
            ):
                yield key, path, parent, ref_conf
