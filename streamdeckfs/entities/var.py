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

from ..common import file_flags, logger
from .base import (
    RE_PARTS,
    VAR_PREFIX,
    VAR_RE_NAME_PART,
    EntityFile,
    InvalidArg,
    UnavailableVar,
)
from .deck import DeckContent
from .key import KeyContent
from .page import PageContent

ELIF_THEN_RE = re.compile(r"^(?:elif|then)(?:\.\d+)?$")


@dataclass(eq=False)
class BaseVar(EntityFile):
    path_glob = "VAR_*"
    main_part_re = re.compile(r"^(?P<kind>VAR)_" + VAR_RE_NAME_PART + "$")
    main_part_compose = lambda args: f'VAR_{args["name"]}'
    get_main_args = lambda self: {"name": self.name}

    allowed_args = EntityFile.allowed_args | {
        "value": re.compile(r"^(?P<arg>value)=(?P<value>.+)$"),
        "if": re.compile(r"^(?P<arg>if)=(?P<value>" + RE_PARTS["bool"] + ")$"),
        "elif": re.compile(r"^(?P<arg>elif)=(?P<value>" + RE_PARTS["bool"] + ")$"),
        "elif.": re.compile(r"^(?P<arg>elif\.\d+)=(?P<value>" + RE_PARTS["bool"] + ")$"),
        "then": re.compile(r"^(?P<arg>then)=(?P<value>.+)$"),
        "then.": re.compile(r"^(?P<arg>then\.\d+)=(?P<value>.+)$"),
        "else": re.compile(r"^(?P<arg>else)=(?P<value>.+)$"),
    }
    # no `name` arg, we already have it in main
    allowed_args.pop("name")

    allowed_partial_args = EntityFile.allowed_partial_args | {
        "then": re.compile(r"^then\.\d+$"),
        "elif": re.compile(r"^elif\.\d+$"),
    }

    unique_args_combinations = [
        ("value", "file"),
        ("value", "if"),
        ("value", "elif"),
        ("value", "then"),
        ("value", "else"),
        ("file", "if"),
        ("file", "elif"),
        ("file", "then"),
        ("file", "else"),
    ]

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
    def save_raw_arg(cls, name, value, args):
        if name == "elif":
            if "elif" not in args:
                args["elif"] = []
            args["elif"].append(value)
        elif name == "then":
            if "then" not in args:
                args["then"] = []
            args["then"].append(value)
        else:
            super().save_raw_arg(name, value, args)

    @classmethod
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)

        for unique_args in cls.unique_args_combinations:
            if len([1 for key in unique_args if args.get(key)]) > 1:
                raise InvalidArg(
                    "Only one of these arguments must be used: " + (", ".join(f'"{arg}"' for arg in unique_args))
                )
        if "if" in args or "else" in args or "then" in args:
            if "if" not in args or "else" not in args or "then" not in args:
                raise InvalidArg('"if", "then" and "else" arguments must all be present')
            all_ifs = [args["if"]] + args.get("elif", [])
            if len(args["then"]) != len(all_ifs) or None in args["then"] or None in all_ifs:
                raise InvalidArg('Invalide number of "elif" or "then"')

        del final_args["name"]

        for arg in ("value", "if", "else"):
            if arg in args:
                final_args[arg] = cls.replace_special_chars(args[arg], args)

        for arg in ("elif", "then"):
            if arg in args:
                final_args[arg] = [cls.replace_special_chars(subarg, args) for subarg in args[arg]]

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        var = super().create_from_args(path, parent, identifier, args, path_modified_at)
        if "if" in args:
            elif_ = [args["if"]] + args.get("elif", [])
            for if_, then_ in zip(elif_, args["then"]):
                if if_.lower() == "true":
                    var.value = then_
                    break
            else:
                var.value = args["else"]
        else:
            var.value = args.get("value")
        return var

    @classmethod
    def merge_partial_arg(cls, main_key, values, args):
        if main_key in ("elif", "then"):
            if main_key not in args:
                args[main_key] = []
            arg = args[main_key]
            for key, value in values.items():
                try:
                    index = int(key.split(".")[-1])
                    if index >= len(arg):
                        # complete list with `None` values
                        arg.extend([None] * (index - len(arg) + 1))
                    arg[index] = value
                except Exception:
                    pass
        else:
            super().merge_partial_arg(main_key, values, args)

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        return main.get("name") == filter

    @property
    def resolved_value(self):
        if self.cached_value is not None:
            return self.cached_value
        if self.value is not None:
            self.cached_value = self.value
        else:
            try:
                path = None
                if self.mode == "content":
                    path = self.resolved_path
                elif self.mode in ("file", "inside"):
                    path = self.get_file_path()
                assert path
            except Exception:
                logger.error(f"[{self}] File to read cannot be found{'' if path is None else ' (path: %s)' % path}")
                raise UnavailableVar
            try:
                self.cached_value = path.read_text().strip()
            except Exception:
                logger.error(f"[{self}] File could not be read: {path}")
                raise UnavailableVar

        if VAR_PREFIX in self.cached_value:
            self.cached_value = self.replace_vars_in_content(self.cached_value)
        if "{" in self.cached_value and "}" in self.cached_value:
            self.cached_value = self.replace_exprs(self.cached_value, self.path.name)
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
        try:
            new_value = self.resolved_value
        except UnavailableVar:
            pass
        else:
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
    allowed_args = BaseVar.allowed_args | {
        "ref": re.compile(
            r"^(?P<arg>ref)=(?P<page>.+):(?P<key>.+):(?P<var>.+)$"  # for internal use only, so we can enforce all parts
        ),
    }

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, key = cls.find_reference_key(parent, ref_conf)
        final_ref_conf["var"] = main["name"]
        if not key:
            return final_ref_conf, None
        return final_ref_conf, key.find_var(final_ref_conf["var"])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for key, path, parent, ref_conf in self.iter_waiting_references_for_key(self.key)
            if (var := key.find_var(ref_conf["var"])) and var.name == self.name
        ]

    def on_file_content_changed(self):
        super().on_file_content_changed()
        for reference in self.referenced_by:
            reference.on_file_content_changed()
