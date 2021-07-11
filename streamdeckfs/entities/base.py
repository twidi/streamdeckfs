#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import re
from collections import defaultdict, namedtuple
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import local
from time import time

from peak.util.proxies import ObjectWrapper

from ..common import Manager, file_flags, logger
from ..py_expression_eval import Parser

thread_local = local()

RE_PARTS = {
    "0-100": r"0*(?:\d{1,2}?|100)",
    "%": r"(?:\d+|\d*\.\d+)%",
    "color": r"\w+|(?:#[a-fA-F0-9]{6})",
    "color & alpha?": r"\w+|(?:#[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?)",
    "bool": r"(?:[Ff][Aa][Ll][Ss][Ee])|(?:[Tt][Rr][Uu][Ee])",
}

RE_PARTS["% | number"] = r"(?:\d+|" + RE_PARTS["%"] + ")"

VAR_RE_NAME_PART = r"(?P<name>[A-Z][A-Z0-9_]*[A-Z0-9])"
VAR_RE = re.compile(r"\$VAR_" + VAR_RE_NAME_PART + r"(?:\[(?P<line>[^\]]+)\])?")
VAR_RE_NAME_GROUP = VAR_RE.groupindex["name"] - 1
VAR_RE_INDEX = re.compile(r"^(?:#|-?\d+)$")
VAR_PREFIX = "$VAR_"

EXPR_RE = re.compile(r"\{(?P<expr>[^}]*)\}")
EXPR_CACHE = {}

DEFAULT_SLASH_REPL = "\\\\"  # double \
DEFAULT_SEMICOLON_REPL = "^"


class FILTER_DENY:
    pass


class InvalidArg(Exception):
    pass


class UnavailableVar(Exception):
    def __init__(self, message="At least one variable is not available yet", var_names=None):
        self.var_names = var_names
        super().__init__(message)


RawParseFilenameResult = namedtuple(
    "RawParseFilenameResult", ["main", "args", "used_vars"], defaults=[None, None, None]
)
ParseFilenameResult = namedtuple(
    "ParseFilenameResult", ["ref_conf", "ref", "main", "args", "used_vars"], defaults=[None, None, None, None, None]
)


@dataclass(eq=False)
class Entity:

    is_dir = False

    path_glob = None
    main_part_re = None
    main_part_compose = None

    allowed_args = {
        "disabled": re.compile(r"^(?P<flag>disabled)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
        "name": re.compile(r"^(?P<arg>name)=(?P<value>[^;]+)$"),
    }
    allowed_partial_args = {}

    unnamed = "__unnamed__"

    parent_attr = None
    identifier_attr = None
    parent_container_attr = None

    path: Path
    path_modified_at: float
    name: str
    disabled: bool

    parse_cache = None

    def __post_init__(self):
        self.ref_conf = None
        self._reference = None
        self.referenced_by = set()
        self.children_waiting_for_references = {}
        self._used_vars = set()

    def __eq__(self, other):
        return id(self) == id(other)

    def __hash__(self):
        return id(self)

    @property
    def reference(self):
        return self._reference

    @reference.setter
    def reference(self, ref):
        if self._reference:
            self._reference.referenced_by.discard(self)
        self._reference = ref
        if ref:
            ref.referenced_by.add(self)

    @property
    def used_vars(self):
        return self._used_vars

    @used_vars.setter
    def used_vars(self, vars):
        for var in self._used_vars:
            var.used_by.discard(self)
        self._used_vars = vars or set()
        for var in vars:
            var.used_by.add(self)

    @classmethod
    def compose_main_part(cls, args):
        return cls.main_part_compose(args)

    @classmethod
    def replace_var(cls, match, vars, filename, parent, used_vars):
        data = match.groupdict()
        name = data["name"]

        if name.startswith("SDFS_"):
            try:
                return parent.env_vars[name]
            except KeyError:
                return match.group(0)

        if name not in vars:
            return match.group(0)
        value = (var := vars[name]).resolved_value

        if line := data.get("line"):
            if VAR_PREFIX in line:
                line = cls.replace_vars(line, filename, parent, used_vars)[0]
            if not VAR_RE_INDEX.match(line):
                raise IndexError
            if line == "#":
                value = str(len(value.splitlines()))
            elif line:
                value = value.splitlines()[int(line)]

        used_vars.add(var)
        return value

    @classmethod
    def replace_vars(cls, value, filename, parent, used_vars=None):
        if used_vars is None:
            used_vars = set()
        var_names = set()
        vars = {}
        replace = partial(cls.replace_var, vars=vars, filename=filename, parent=parent, used_vars=used_vars)

        while VAR_PREFIX in value and (matches := VAR_RE.findall(value)):
            count_before = len(matches)
            current_var_names = {match[VAR_RE_NAME_GROUP] for match in matches}
            var_names |= current_var_names

            try:
                vars |= {
                    name: var
                    for name in current_var_names
                    if name not in vars and (var := parent.get_var(name, default_none=True)) is not None
                }
                # will raise IndexError if wanted to access an invalid line number
                value = VAR_RE.sub(replace, value)

                if len(VAR_RE.findall(value)) == count_before:
                    # we had matches, but none were replaced, we can end the loop
                    raise UnavailableVar

            except (UnavailableVar, IndexError) as exc:
                if isinstance(exc, UnavailableVar) and exc.var_names:
                    var_names |= exc.var_names
                parent.add_waiting_for_vars(cls, filename, var_names)
                raise UnavailableVar(var_names=var_names)

        return value, used_vars

    @classmethod
    def get_expr_parser(cls):
        # the Parser object is not thread safe so we must have one per thread
        try:
            return thread_local.expr_parser
        except AttributeError:
            thread_local.expr_parser = Parser()
            return thread_local.expr_parser

    @classmethod
    def replace_expr(cls, match, name):
        expr = match.groupdict()["expr"]
        try:
            if expr in EXPR_CACHE:
                if (result := EXPR_CACHE[expr]) is None:
                    raise ValueError
            else:
                EXPR_CACHE[expr] = result = str(cls.get_expr_parser().evaluate(expr, {}))
        except Exception:
            EXPR_CACHE[expr] = None
            logger.warning(f'`{expr}` is not a valid expression in "{name}"')
            raise
        return result

    @classmethod
    def replace_exprs(cls, value, name):
        replace = partial(cls.replace_expr, name=name)
        return EXPR_RE.sub(replace, value)

    @classmethod
    def save_raw_arg(cls, name, value, args):
        args[name] = value

    @classmethod
    def raw_parse_filename(cls, name, parent, use_cache_if_vars=False):
        if cls.parse_cache is None:
            cls.parse_cache = {}

        if name in cls.parse_cache:
            if not (parse_cache := cls.parse_cache[name]).used_vars or use_cache_if_vars:
                return parse_cache

        used_vars = set()

        main_part, *__, conf_part = name.partition(";")
        if not (match := cls.main_part_re.match(main_part)):
            main, args = None, None
        else:
            main = match.groupdict()
            args = {}

            if conf_part:
                try:
                    conf_part, used_vars = cls.replace_vars(conf_part, name, parent)
                except UnavailableVar:
                    return RawParseFilenameResult()  # we don't cache the result

                if "{" in conf_part and "}" in conf_part:
                    try:
                        conf_part = cls.replace_exprs(conf_part, name)
                    except Exception:
                        return RawParseFilenameResult()  # we don't cache the result

                parts = conf_part.split(";")
                for part in parts:
                    for regex in cls.allowed_args.values():
                        if match := regex.match(part):
                            values = match.groupdict()
                            is_flag = "flag" in values and "arg" not in values and len(values) == 2
                            if not is_flag:
                                values = {key: value for key, value in values.items() if value}
                            if not (arg_name := values.pop("flag" if is_flag else "arg", None)):
                                continue
                            if list(values.keys()) == ["value"]:
                                values = values["value"]
                                if is_flag:
                                    values = values is None or isinstance(values, str) and values.lower() == "true"
                            cls.save_raw_arg(arg_name, values, args)

        cls.parse_cache[name] = RawParseFilenameResult(main, args, used_vars)
        return cls.parse_cache[name]

    def get_raw_args(self):
        parse_cache = self.raw_parse_filename(self.path.name, self.path.parent, use_cache_if_vars=True)
        return parse_cache.main, parse_cache.args

    def get_resovled_raw_args(self):
        main, args = map(deepcopy, self.get_raw_args())
        if self.reference:
            ref_main, ref_args = map(deepcopy, self.reference.get_resovled_raw_args())
            return ref_main | main, ref_args | args
        return main, args

    @classmethod
    def parse_filename(cls, name, parent):
        main, args, used_vars = cls.raw_parse_filename(name, parent)
        main, args = map(deepcopy, (main, args))
        if main is None or args is None:
            return ParseFilenameResult()

        ref_conf = ref = None
        if ref_conf := args.get("ref"):
            if "key_same_page" in ref_conf:
                ref_conf["key"] = ref_conf.pop("key_same_page")
            ref_conf, ref = cls.find_reference(parent, ref_conf, main, args)
            if not ref:
                return ParseFilenameResult(ref_conf)
            ref_main, ref_args = ref.get_resovled_raw_args()
            if ref_main is None or ref_args is None:
                return ParseFilenameResult(ref_conf)

            main = ref_main | main

            # do not inherit "sub arguments" (things like `margin.2` if whole argument is defined in the current conf, like, in this example, `margin`)
            sub_ref_args = {}
            for key, value in ref_args.items():
                if "." not in key:
                    continue
                parent_key = key.split(".", 1)[0]
                sub_ref_args.setdefault(parent_key, {})[key] = value
            if sub_ref_args:
                ref_args = ref_args.copy()
                for parent_key in sub_ref_args.keys():
                    if parent_key in args:
                        for key, value in sub_ref_args[parent_key].items():
                            ref_args.pop(key)

            args = ref_args | args

        # merge "sub arguments" in their main arguments
        sub_args = {}
        for key, value in args.items():
            if "." not in key:
                continue
            parent_key = key.split(".", 1)[0]
            sub_args.setdefault(parent_key, {})[key] = value
        for parent_key in sub_args.keys():
            cls.merge_partial_arg(parent_key, sub_args[parent_key], args)

        try:
            main = cls.convert_main_args(main)
            if main is not None:
                if (args := cls.convert_args(main, args)) is not None:
                    return ParseFilenameResult(ref_conf, ref, main, args, used_vars)
        except InvalidArg as exc:
            logger.error(f"[{parent}] [{name}] {exc}")

        return ParseFilenameResult(ref_conf)

    @classmethod
    def merge_partial_arg(cls, main_key, values, args):
        if main_key not in args:
            return
        arg = args[main_key]
        if isinstance(arg, str):
            try:
                parts = arg.split(",")
                for key, value in values.items():
                    try:
                        index = int(key.split(".")[-1])
                        parts[index] = value
                    except Exception:
                        continue
                args[main_key] = ",".join(parts)
            except Exception:
                pass
        elif isinstance(arg, dict):
            try:
                parts = list(arg.keys())
                for key, value in values.items():
                    try:
                        part = key.split(".")[-1]
                        try:
                            index = int(part)
                        except ValueError:
                            if part in arg:
                                arg[part] = value
                        else:
                            arg[parts[index]] = value
                    except Exception:
                        pass
            except Exception:
                pass

    def make_new_filename(self, update_args, remove_args):
        parts = self.path.name.split(";")
        main_part = parts.pop(0)
        final_parts = [main_part]
        seen_names = set()
        for part in parts:
            if not part:
                continue
            name, *__, value = part.partition("=")
            if name in seen_names:
                continue
            seen_names.add(name)
            if name in remove_args:
                continue
            if name not in update_args:
                final_parts.append(part)
                continue
            if isinstance(update_args[name], list):
                for sub_value in update_args.pop(name):
                    final_parts.append(f"{name}={sub_value}")
            else:
                final_parts.append(f"{name}={update_args.pop(name)}")
        for name, value in update_args.items():
            if isinstance(value, list):
                for sub_value in value:
                    final_parts.append(f"{name}={sub_value}")
            else:
                final_parts.append(f"{name}={value}")
        return ";".join(final_parts)

    def rename(self, new_filename=None, new_path=None, check_only=False):
        assert (new_filename or new_path) and not (new_filename and new_path)
        if new_filename:
            new_path = self.path.parent / new_filename
        if new_path != self.path:
            if not check_only:
                self.path = self.path.replace(new_path)
            return True, new_path
        return False, new_path

    @classmethod
    def convert_main_args(cls, args):
        return args

    @classmethod
    def convert_args(cls, main, args):
        final_args = {
            "disabled": args.get("disabled", False),
            "name": args.get("name") or cls.unnamed,
        }
        return final_args

    @classmethod
    def get_create_base_args(cls, path, parent, identifier, args=None, path_modified_at=None):
        if args is None:
            args = {}
        return {
            "path": path,
            "path_modified_at": path_modified_at or time(),
            "name": args.get("name") or cls.unnamed,
            "disabled": args.get("disabled", False),
            cls.parent_attr: parent,
            cls.identifier_attr: identifier,
        }

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        return cls(**cls.get_create_base_args(path, parent, identifier, args, path_modified_at))

    @classmethod
    def create_basic(cls, parent, main_args, identifier):
        filename = cls.compose_main_part(main_args)
        return cls(**cls.get_create_base_args(parent.path / filename, parent, identifier))

    @property
    def identifier(self):
        return getattr(self, self.identifier_attr)

    @property
    def parent(self):
        return getattr(self, self.parent_attr, None) if self.parent_attr else None

    @property
    def has_disabled_parent(self):
        if parent := self.parent:
            return parent.disabled or parent.has_disabled_parent
        return False

    @property
    def parent_container(self):
        return getattr(self.parent, self.parent_container_attr)

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        return ref_conf, None

    @property
    def resolved_path(self):
        try:
            is_empty = self.path.stat().st_size == 0
        except Exception:
            is_empty = False
        if is_empty and self.reference:
            return self.reference.resolved_path
        return self.path

    @staticmethod
    def get_waiting_reference_holder(deck, ref_conf):
        from .key import Key

        if isinstance(ref_conf.get("key"), Key):
            return ref_conf["key"]

        from .page import Page

        if isinstance(ref_conf.get("page"), Page):
            return ref_conf["page"]
        return deck

    @classmethod
    def add_waiting_reference(cls, parent, path, ref_conf):
        ref_holder = cls.get_waiting_reference_holder(parent.deck, ref_conf)
        ref_holder.children_waiting_for_references.setdefault(cls, {})[path] = (parent, ref_conf)

    @classmethod
    def remove_waiting_reference(cls, deck, path, ref_conf):
        ref_holder = cls.get_waiting_reference_holder(deck, ref_conf)
        ref_holder.children_waiting_for_references.setdefault(cls, {}).pop(path, None)

    def get_waiting_references(self):
        return []

    def on_create(self):
        for path, parent, ref_conf in self.get_waiting_references():
            if not path.exists() or parent.on_file_change(
                parent.path,
                path.name,
                file_flags.CREATE | (file_flags.ISDIR if self.is_dir else 0),
                entity_class=self.__class__,
            ):
                self.remove_waiting_reference(self.deck, path, ref_conf)

    def on_file_content_changed(self):
        pass

    def on_delete(self):
        for ref in list(self.referenced_by):
            ref.on_reference_deleted()
        self.reference = None
        self.used_vars = set()
        if (parse_cache := self.__class__.parse_cache.get(self.name)) and parse_cache.used_vars:
            self.__class__.parse_cache.pop(self.name, None)

    def on_reference_deleted(self):
        if self.reference:
            self.add_waiting_reference(self.parent, self.path, self.ref_conf)
        self.on_delete()
        self.parent_container[self.identifier].remove_version(self.path)

    def on_var_deleted(self):
        self.parent.add_waiting_for_vars(self.__class__, self.path.name, {var.name for var in self.used_vars})
        self.on_delete()
        self.parent_container[self.identifier].remove_version(self.path)

    def on_child_entity_change(
        self, path, flags, entity_class, data_identifier, args, ref_conf, ref, used_vars, modified_at=None
    ):
        data_dict = getattr(self, entity_class.parent_container_attr)

        if (
            (bool(flags & file_flags.ISDIR) ^ entity_class.is_dir)
            or (flags & file_flags.DELETE)
            or (flags & file_flags.MOVED_FROM)
        ):
            if entity := data_dict[data_identifier].get_version(path):
                entity.on_delete()
                data_dict[data_identifier].remove_version(path)
            if ref_conf:
                entity_class.remove_waiting_reference(self.deck, path, ref_conf)
            return False

        if modified_at is None:
            try:
                modified_at = path.lstat().st_ctime
            except Exception:
                return False

        if entity := data_dict[data_identifier].get_version(path):
            entity.path_modified_at = modified_at
            entity.on_file_content_changed()
            return False

        entity = entity_class.create_from_args(
            path=path, parent=self, identifier=data_identifier, args=args, path_modified_at=modified_at
        )

        if ref:
            entity.reference = ref
            entity.ref_conf = ref_conf

        entity.used_vars = used_vars

        data_dict[data_identifier].add_version(path, entity)
        entity.on_create()
        return True

    def version_activated(self):
        logger.debug(f"[{self}] Version activated: {self.path}")

    def version_deactivated(self):
        logger.debug(f"[{self}] Version deactivated: {self.path}")

    @classmethod
    def find_by_identifier_or_name(cls, data, filter, to_identifier, allow_disabled=False):
        if not filter:
            return None

        try:
            # find by identifier
            if (identifier := to_identifier(filter)) not in data:
                raise ValueError()
            if entry := data[identifier]:
                # we have an entry not disabled, we return the active version
                return entry
            if entry.is_empty or not allow_disabled:
                # there is no version, or, if asked, no enabled one, for this entry, we'll search by name
                raise ValueError()
            for version in entry.iter_versions(reverse=True):
                # we have at least one version but disabled, we get the most recent
                return version

        except Exception:
            sorted_entries = sorted(data.items())
            # find by name, first using only active and not disabled entries
            for __, entry in sorted_entries:
                if entry and entry.name == filter:
                    return entry
            if allow_disabled:
                # then by going through all versions of all entries until we find one
                for __, entry in sorted_entries:
                    if entry.is_empty:
                        continue
                    for version in entry.iter_versions(reverse=True):
                        if version.name == filter:
                            return version

        # nothing found
        return None

    @staticmethod
    def replace_special_chars(value, args):
        return value.replace(args.get("slash", DEFAULT_SLASH_REPL), "/").replace(
            args.get("semicolon", DEFAULT_SEMICOLON_REPL), ";"
        )

    @staticmethod
    def finalize_env_vars(env_vars, post_prefix=None):
        return {
            f"SDFS_{post_prefix or ''}{key.upper()}": str(value)
            for key, value in env_vars.items()
            if value is not None
        }


class VersionProxy(ObjectWrapper):
    versions = None
    sort_key_func = None

    def __init__(self, sort_key_func):
        super().__init__(None)
        self.versions = {}
        self.sort_key_func = sort_key_func

    def add_version(self, key, value):
        assert key not in self.versions, f"Key {key} already in available versions"
        self.versions[key] = value
        self.reset_subject()

    def has_version(self, key):
        return key in self.versions

    @property
    def is_empty(self):
        return not self.versions

    def get_version(self, key):
        return self.versions.get(key)

    @property
    def all_versions(self):
        return self.versions.values()

    def iter_versions(self, reverse=False, exclude_disabled=False):
        versions = self.versions.items()
        if exclude_disabled:
            versions = [(key, value) for key, value in versions if not value.disabled]
        for key, value in sorted(versions, key=self.sort_key_func, reverse=reverse):
            yield value

    def remove_version(self, key):
        value = self.versions.pop(key, None)
        self.reset_subject()
        return value

    def reset_subject(self):
        old_subject = self.__subject__
        try:
            new_subject = self.__subject__ = next(self.iter_versions(reverse=True, exclude_disabled=True))
        except StopIteration:
            new_subject = self.__subject__ = None
        if new_subject != old_subject:
            if old_subject and hasattr(old_subject, "version_deactivated"):
                old_subject.version_deactivated()
            if new_subject and hasattr(new_subject, "version_activated"):
                new_subject.version_activated()


VersionProxyMostRecent = partial(VersionProxy, sort_key_func=lambda key_and_obj: key_and_obj[1].path_modified_at)
versions_dict_factory = lambda: defaultdict(VersionProxyMostRecent)


file_char_allowed_args = {
    "slash": re.compile(r"^(?P<arg>slash)=(?P<value>.+)$"),
    "semicolon": re.compile(r"^(?P<arg>semicolon)=(?P<value>.+)$"),
}


@dataclass(eq=False)
class EntityFile(Entity):
    allowed_args = (
        Entity.allowed_args
        | file_char_allowed_args
        | {
            "file": re.compile(r"^(?P<arg>file)=(?P<value>.+)$"),
        }
    )

    filename_file_parts = [
        lambda args: f"file={file}" if (file := args.get("file")) else None,
        lambda args: f"slash={slash}" if (slash := args.get("slash")) else None,
        lambda args: f"semicolon={semicolon}" if (semicolon := args.get("semicolon")) else None,
    ]

    def __post_init__(self):
        super().__post_init__()
        self.mode = None
        self.file = None
        self.watched_directory = False
        self._used_vars_in_content = set()

    @classmethod
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)
        final_args["mode"] = "content"
        if "file" in args:
            if args["file"] == "__inside__":
                final_args["mode"] = "inside"
            else:
                final_args["mode"] = "file"
                try:
                    final_args["file"] = Path(cls.replace_special_chars(args["file"], args))
                    try:
                        final_args["file"] = final_args["file"].expanduser()
                    except Exception:
                        pass
                except Exception:
                    final_args["file"] = None
        return final_args

    def check_file_exists(self):
        if not self.deck.is_running:
            return
        if self.mode == "file" and self.file and not self.file.exists():
            logger.warning(f'[{self}] File "{self.file}" does not exist')
        elif self.mode == "inside" and (path := self.get_inside_path()) and not path.exists():
            logger.warning(f'[{self}] File "{path}" does not exist')

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        obj = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in ("mode", "file"):
            if key not in args:
                continue
            setattr(obj, key, args[key])
        return obj

    def start_watching_directory(self, directory):
        if self.watched_directory and self.watched_directory != directory:
            self.stop_watching_directory()
        if not self.watched_directory:
            self.watched_directory = directory
            Manager.add_watch(directory, self)

    def stop_watching_directory(self):
        if watched_directory := self.watched_directory:
            self.watched_directory = None
            Manager.remove_watch(watched_directory, self)

    def track_symlink_dir(self):
        if not self.watched_directory and self.path.is_symlink():
            self.start_watching_directory(self.path.resolve().parent)

    def get_inside_path(self):
        if self.mode != "inside":
            return None
        with self.resolved_path.open() as f:
            path = f.readline().strip()
        if path:
            path = self.replace_vars_in_content(path)
        if path:
            path = Path(path)
            try:
                path = path.expanduser()
            except Exception:
                pass
        return path

    def get_file_path(self):
        if self.mode == "inside":
            path = self.get_inside_path()
        elif self.file:
            path = self.file

        if not path:
            self.stop_watching_directory()
            return None

        self.start_watching_directory(path.parent)

        if not path.exists() or path.is_dir():
            return None

        return path

    def on_file_change(self, directory, name, flags, modified_at=None, entity_class=None):
        path = directory / name
        if (self.file and path == self.file) or (
            not self.file and self.path.is_symlink() and path == self.path.resolve()
        ):
            self.on_file_content_changed()

    def on_directory_removed(self, directory):
        self.on_file_content_changed()

    def version_deactivated(self):
        super().version_deactivated()
        self.stop_watching_directory()

    def get_var(self, name, cascading=True, default_none=False):
        return self.parent.get_var(name, cascading=cascading, default_none=default_none)

    def get_available_vars_values(self, exclude=None):
        return self.parent.get_available_vars_values(exclude)

    @property
    def used_vars(self):
        return self._used_vars | self._used_vars_in_content

    @used_vars.setter
    def used_vars(self, vars):
        for var in self._used_vars - self._used_vars_in_content:
            var.used_by.discard(self)
        self._used_vars = vars or set()
        for var in vars:
            var.used_by.add(self)

    @property
    def used_vars_in_content(self):
        return self._used_vars_in_content

    @used_vars_in_content.setter
    def used_vars_in_content(self, vars):
        for var in self._used_vars_in_content - self._used_vars:
            var.used_by.discard(self)
        self._used_vars_in_content = vars or set()
        for var in vars:
            var.used_by.add(self)

    def on_delete(self):
        super().on_delete()
        self.used_vars_in_content = set()

    def replace_vars_in_content(self, content):
        if self.mode != "text" and content:
            try:
                content, used_vars = self.replace_vars(content, self.path.name, self.parent)
            except UnavailableVar:
                content = None
            else:
                self.used_vars_in_content = used_vars
        return content


class NOT_HANDLED:
    pass


@dataclass(eq=False)
class EntityDir(Entity):
    is_dir = True

    event_class = None
    var_class = None

    def __post_init__(self):
        super().__post_init__()
        self.events = versions_dict_factory()
        self.vars = versions_dict_factory()
        self.children_waiting_for_vars = {}

    @property
    def resolved_events(self):
        if not self.reference:
            return self.events
        events = {}
        for kind, event in self.events.items():
            if event:
                events[kind] = event
        for kind, event in self.reference.resolved_events.items():
            if kind not in events and event:
                events[kind] = event
        return events

    def on_delete(self):
        for event in self.iter_all_children_versions(self.events):
            event.on_delete()
        for var in self.iter_all_children_versions(self.vars):
            var.on_delete()
        super().on_delete()

    def activate_events(self):
        for event in self.resolved_events.values():
            if event:
                event.activate(self)

    def deactivate_events(self):
        for event in self.resolved_events.values():
            if event:
                event.deactivate()

    def find_event(self, event_filter, allow_disabled=False):
        return self.event_class.find_by_identifier_or_name(
            self.resolved_events, event_filter, str, allow_disabled=allow_disabled
        )

    def find_var(self, var_filter, allow_disabled=False):
        return self.var_class.find_by_identifier_or_name(self.vars, var_filter, str, allow_disabled=allow_disabled)

    def read_directory(self):
        if self.deck.filters.get("event") != FILTER_DENY:
            for event_file in sorted(self.path.glob(self.event_class.path_glob)):
                self.on_file_change(
                    self.path,
                    event_file.name,
                    file_flags.CREATE | (file_flags.ISDIR if event_file.is_dir() else 0),
                    entity_class=self.event_class,
                )
        if self.deck.filters.get("var") != FILTER_DENY:
            for var_file in sorted(self.path.glob(self.var_class.path_glob)):
                self.on_file_change(
                    self.path,
                    var_file.name,
                    file_flags.CREATE | (file_flags.ISDIR if var_file.is_dir() else 0),
                    entity_class=self.var_class,
                )

    def on_file_change(self, directory, name, flags, modified_at=None, entity_class=None):
        if (event_filter := self.deck.filters.get("event")) != FILTER_DENY:
            if not entity_class or entity_class is self.event_class:
                path = self.path / name
                if (parsed := self.event_class.parse_filename(name, self)).main:
                    if event_filter is not None and not self.event_class.args_matching_filter(
                        parsed.main, parsed.args, event_filter
                    ):
                        return None
                    return self.on_child_entity_change(
                        path=path,
                        flags=flags,
                        entity_class=self.event_class,
                        data_identifier=parsed.main["kind"],
                        args=parsed.args,
                        ref_conf=parsed.ref_conf,
                        ref=parsed.ref,
                        used_vars=parsed.used_vars,
                        modified_at=modified_at,
                    )
                elif parsed.ref_conf:
                    self.event_class.add_waiting_reference(self, path, parsed.ref_conf)
        if (var_filter := self.deck.filters.get("var")) != FILTER_DENY:
            if not entity_class or entity_class is self.var_class:
                if (parsed := self.var_class.parse_filename(name, self)).main:
                    if var_filter is not None and not self.var_class.args_matching_filter(
                        parsed.main, parsed.args, var_filter
                    ):
                        return None
                    path = self.path / name
                    return self.on_child_entity_change(
                        path=path,
                        flags=flags,
                        entity_class=self.var_class,
                        data_identifier=parsed.main["name"],
                        args=parsed.args,
                        ref_conf=None,
                        ref=None,
                        used_vars=parsed.used_vars,
                        modified_at=modified_at,
                    )
        return NOT_HANDLED

    def iter_all_children_versions(self, content):
        for versions in content.values():
            yield from versions.all_versions

    def get_var(self, name, cascading=True, default_none=False):
        if var := self.vars.get(name):
            return var
        if cascading and (parent := self.parent):
            return parent.get_var(name, cascading=True, default_none=default_none)

        if default_none:
            return None

        raise UnavailableVar

    def get_available_vars_values(self, exclude=None):
        if not exclude:
            exclude = set()
        result = {}
        for name, var in self.vars.items():
            if name in exclude or not var:
                continue
            exclude.add(name)
            result[name] = var.resolved_value
        if parent := self.parent:
            result |= parent.get_available_vars_values(exclude=exclude)
        return result

    def add_waiting_for_vars(self, entity_class, name, var_names):
        if name in self.children_waiting_for_vars:
            var_names |= self.children_waiting_for_vars[name][1]
        self.children_waiting_for_vars[name] = (entity_class, var_names)

    def remove_waiting_for_vars(self, name):
        self.children_waiting_for_vars.pop(name, None)

    def get_waiting_for_vars(self, for_var_name=None):
        for name, (entity_class, var_names) in list(self.children_waiting_for_vars.items()):
            if for_var_name and for_var_name not in var_names:
                continue
            yield entity_class, name, var_names

    def iterate_vars_holders(self):
        yield self
