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

from .base import Entity, EntityFile, InvalidArg
from .deck import DeckContent
from .key import KeyContent
from .page import PageContent


@dataclass(eq=False)
class BaseVar(EntityFile):
    path_glob = "VAR_*"
    main_path_re = re.compile(r"^(?P<kind>VAR)_(?P<name>[A-Z0-9_]+)(?:;|$)")
    filename_re_parts = EntityFile.common_filename_re_parts + [
        # no `name` arg, we already have it in main
        re.compile(r"^(?P<arg>value)=(?P<value>.+)$"),
        Entity.filename_re_part_disabled,
    ]
    main_filename_part = lambda args: f'VAR_{args["name"]}'
    filename_parts = EntityFile.filename_file_parts + [
        lambda args: f"value={value}" if (value := args.get("value")) else None,
        Entity.disabled_filename_part,
    ]

    identifier_attr = "name"
    parent_container_attr = "vars"

    def __post_init__(self):
        super().__post_init__()
        self.value = None

    @property
    def str(self):
        return f'VAR {self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f"{self.parent}, {self.str}"

    @classmethod
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)

        if len([1 for key in ("value", "file") if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "value", "file"')

        del final_args["name"]
        if "value" in args:
            final_args["value"] = args["value"]
        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        var = super().create_from_args(path, parent, identifier, args, path_modified_at)
        var.value = args.get("value")
        return var

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        return main.get("name") == filter

    @property
    def resolved_value(self):
        if self.value is None:
            if self.mode == "content":
                self.track_symlink_dir()
                try:
                    self.value = self.resolved_path.read_text().strip()
                except Exception:
                    pass
            elif self.mode in ("file", "inside"):
                if path := self.get_file_path():
                    try:
                        self.value = path.read_text().strip()
                    except Exception:
                        pass
        return self.value


@dataclass(eq=False)
class DeckVar(BaseVar, DeckContent):
    pass


@dataclass(eq=False)
class PageVar(BaseVar, PageContent):
    pass


@dataclass(eq=False)
class KeyVar(BaseVar, KeyContent):
    pass
