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

from cached_property import cached_property

from ..common import Manager, file_flags
from .base import FILTER_DENY, Entity, versions_dict_factory
from .deck import Deck

FIRST = "__first__"
BACK = "__back__"
PREVIOUS = "__prev__"
NEXT = "__next__"

PAGE_CODES = (FIRST, BACK, PREVIOUS, NEXT)


@dataclass(eq=False)
class Page(Entity):

    is_dir = True
    path_glob = "PAGE_*"
    dir_template = "PAGE_{page}"
    main_path_re = re.compile(r"^(?P<kind>PAGE)_(?P<page>\d+)(?:;|$)")
    main_filename_part = lambda args: f'PAGE_{args["page"]}'

    parent_attr = "deck"
    identifier_attr = "number"
    parent_container_attr = "pages"

    deck: "Deck"
    number: int

    def __post_init__(self):
        super().__post_init__()
        self.keys = versions_dict_factory()

    @property
    def str(self):
        return f'PAGE {self.number} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f"{self.deck}, {self.str}"

    @classmethod
    def convert_main_args(cls, args):
        args = super().convert_main_args(args)
        args["page"] = int(args["page"])
        return args

    def on_create(self):
        super().on_create()
        Manager.add_watch(self.path, self)
        self.read_directory()

    def on_delete(self):
        Manager.remove_watch(self.path, self)
        for key_versions in self.keys.values():
            for key in key_versions.all_versions:
                key.on_delete()
        super().on_delete()

    def read_directory(self):
        if self.deck.filters.get("key") != FILTER_DENY:
            from .key import Key

            for key_dir in sorted(self.path.glob(Key.path_glob)):
                self.on_file_change(
                    self.path, key_dir.name, file_flags.CREATE | (file_flags.ISDIR if key_dir.is_dir() else 0)
                )

    def on_file_change(self, directory, name, flags, modified_at=None, entity_class=None):
        if directory != self.path:
            return
        path = self.path / name
        if (key_filter := self.deck.filters.get("key")) != FILTER_DENY:
            from .key import Key

            if not entity_class or entity_class is Key:
                ref_conf, ref, main, args = Key.parse_filename(name, self)
                if main:
                    if key_filter is not None and not Key.args_matching_filter(main, args, key_filter):
                        return None
                    return self.on_child_entity_change(
                        path=path,
                        flags=flags,
                        entity_class=Key,
                        data_identifier=(main["row"], main["col"]),
                        args=args,
                        ref_conf=ref_conf,
                        ref=ref,
                        modified_at=modified_at,
                    )
                elif ref_conf:
                    Key.add_waiting_reference(self, path, ref_conf)

    def on_directory_removed(self, directory):
        pass

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if main["page"] == int(filter):
                return True
        except ValueError:
            pass
        return args.get("name") == filter

    @property
    def is_current(self):
        return self.number == self.deck.current_page_number

    def iter_keys(self):
        for row_col, key in sorted(self.keys.items()):
            if key and key.has_content:
                yield key

    @property
    def is_visible(self):
        return self.deck.is_page_visible(self)

    def render(self, render_above=True, render_below=True, rendered_keys=None):
        if not self.is_visible:
            return

        if rendered_keys is None:
            rendered_keys = set()
        elif len(rendered_keys) == self.deck.nb_keys:
            return

        if render_above and (page_above := self.deck.get_page_above(self)):
            page_above.render(render_above=True, render_below=False, rendered_keys=rendered_keys)

        level, transparent = self.deck.get_page_overlay_level(self)

        for key in self.iter_keys():
            if key.key in rendered_keys:
                continue
            key.render()
            rendered_keys.add(key.key)

        if len(rendered_keys) == self.deck.nb_keys:
            return

        if render_below:
            if page_below := self.deck.get_page_below(self):
                page_below.render(render_above=False, render_below=True, rendered_keys=rendered_keys)
            else:
                # we have no page left below, we remove images on the remaining keys
                for row in range(1, self.deck.nb_rows + 1):
                    for col in range(1, self.deck.nb_cols + 1):
                        if (row, col) not in rendered_keys:
                            self.deck.remove_image(row, col)
                            rendered_keys.add((row, col))

    def unrender(self, clear_images=True):
        if not self.is_visible:
            return
        for key in self.iter_keys():
            key.unrender(clear_image=clear_images)

    @property
    def sorted_keys(self):
        keys = {}
        for row_col, key in sorted((key.key, key) for key in self.iter_keys()):
            keys[row_col] = key
        return keys

    def find_key(self, key_filter, allow_disabled=False):
        from .key import Key

        return Key.find_by_identifier_or_name(
            self.keys,
            key_filter,
            lambda filter: tuple(int(val) for val in filter.split(",")),
            allow_disabled=allow_disabled,
        )

    def version_activated(self):
        super().version_activated()
        if self.disabled:
            return
        self.render()
        if self.deck.is_running and not self.deck.current_page_number:
            self.deck.go_to_page(self.number, None)

    def version_deactivated(self):
        is_current_page_number = self.deck.current_page_number == self.number
        super().version_deactivated()
        if self.disabled:
            return
        self.unrender()
        if is_current_page_number:
            self.deck.go_to_page(BACK, None)

    @cached_property
    def env_vars(self):
        return self.deck.env_vars | self.finalize_env_vars(
            {
                "page": str(self.number),
                "page_name": "" if self.name == self.unnamed else self.name,
                "page_directory": self.path,
            }
        )
