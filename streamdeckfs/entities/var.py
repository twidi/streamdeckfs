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

from ..common import file_flags
from .base import VAR_RE_NAME_PART, EntityFile, InvalidArg, UnavailableVar
from .deck import DeckContent
from .key import KeyContent
from .page import PageContent


@dataclass(eq=False)
class BaseVar(EntityFile):
    path_glob = "VAR_*"
    main_part_re = re.compile(r"^(?P<kind>VAR)_" + VAR_RE_NAME_PART + "$")
    main_part_compose = lambda args: f'VAR_{args["name"]}'

    allowed_args = EntityFile.allowed_args | {
        "value": re.compile(r"^(?P<arg>value)=(?P<value>.+)$"),
    }
    # no `name` arg, we already have it in main
    allowed_args.pop("name")

    identifier_attr = "name"
    parent_container_attr = "vars"

    def __post_init__(self):
        super().__post_init__()
        self.value = None
        self.cached_value = None
        self.used_by = set()

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
            final_args["value"] = cls.replace_special_chars(args["value"], args)
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
        if self.cached_value is not None:
            return self.cached_value
        if self.value:
            self.cached_value = self.value
        else:
            if self.mode == "content":
                self.track_symlink_dir()
                try:
                    self.cached_value = self.resolved_path.read_text().strip()
                except Exception:
                    pass
            elif self.mode in ("file", "inside"):
                if path := self.get_file_path():
                    try:
                        self.cached_value = path.read_text().strip()
                    except Exception:
                        pass
        self.cached_value = self.replace_vars_in_content(self.cached_value)
        return self.cached_value

    def iterate_children_dirs(self):
        return []

    def activate(self, root=None):
        if root is None:
            root = self.parent
        for vars_holder in root.iterate_vars_holders():
            for entity_class, name, var_names in vars_holder.get_waiting_for_vars(self.name):
                path = vars_holder.path / name
                if not path.exists() or vars_holder.on_file_change(
                    vars_holder.path,
                    name,
                    file_flags.CREATE | (file_flags.ISDIR if path.is_dir() else 0),
                    entity_class=entity_class,
                ):
                    vars_holder.remove_waiting_for_vars(name)

    def deactivate(self, root=None):
        for entity in list(self.used_by):
            if root is not None and not entity.path.is_relative_to(root.path):
                continue
            entity.on_var_deleted()

    def version_activated(self):
        super().version_activated()
        # if we have a variable at a upper level with the same name,
        # (our parent is the one holding us, so we want our grand-parent)
        if grand_parent := self.parent.parent:
            try:
                grand_parent_var = grand_parent.get_var(self.name)
            except UnavailableVar:
                pass
            else:
                # then we deactivate it, but only for our current var holder (our parent)
                grand_parent_var.deactivate(self.parent)

    def on_create(self):
        super().on_create()
        self.activate()

    def on_delete(self):
        super().on_delete()
        self.deactivate()

    def version_deactivated(self):
        super().version_deactivated()
        # if we have one at a upper level with the same name,
        # (our parent is the one holding us, so we want our grand-parent)
        if grand_parent := self.parent.parent:
            try:
                grand_parent_var = grand_parent.get_var(self.name)
            except UnavailableVar:
                pass
            else:
                # then we use it to re-render the var just unrendered
                grand_parent_var.activate()

    def on_file_content_changed(self):
        super().on_file_content_changed()
        current_value = self.cached_value
        self.cached_value = None
        new_value = self.resolved_value
        if new_value != current_value:
            for entity in list(self.used_by):
                entity.on_var_deleted()
            self.activate()


@dataclass(eq=False)
class DeckVar(BaseVar, DeckContent):
    pass


@dataclass(eq=False)
class PageVar(BaseVar, PageContent):
    pass


@dataclass(eq=False)
class KeyVar(BaseVar, KeyContent):
    pass
