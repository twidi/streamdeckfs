#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from time import time

from peak.util.proxies import ObjectWrapper

from ..common import logger, file_flags

RE_PARTS = {
    '0-100': r'0*(?:\d{1,2}?|100)',
    '%': r'(?:\d+|\d*\.\d+)%',
    'color': r'\w+|(?:#[a-fA-F0-9]{6})',
    'color & alpha?': r'\w+|(?:#[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?)',
}

RE_PARTS['% | number'] = r'(?:\d+|' + RE_PARTS["%"] + ')'

DEFAULT_SLASH_REPL = '\\\\'  # double \
DEFAULT_SEMICOLON_REPL = '^'


class FILTER_DENY:
    pass


class InvalidArg(Exception):
    pass


@dataclass(eq=False)
class Entity:

    is_dir = False
    path_glob = None
    main_path_re = None
    filename_re_parts = [
        re.compile(r'^(?P<flag>disabled)(?:=(?P<value>false|true))?$'),
        re.compile(r'^(?P<arg>name)=(?P<value>[^;]+)$'),
    ]
    main_filename_part = None
    name_filename_part = lambda args: f'name={args["name"]}' if args.get('name') else None
    disabled_filename_part = lambda args: 'disabled' if args.get('disabled', False) in (True, 'true', None) else None
    filename_parts = [name_filename_part, disabled_filename_part]

    unnamed = '__unnamed__'

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
        self.waiting_child_references = {}

    def __eq__(self, other):
        return id(self) == id(other)

    def __hash__(self):
        return id(self)

    @property
    def reference(self):
        return self._reference

    @reference.setter
    def reference(self, ref):
        self._reference = ref
        if ref:
            ref.referenced_by.add(self)
        else:
            ref.referenced_by.discard(self)

    @classmethod
    def compose_filename_part(cls, part, args):
        try:
            return part(args)
        except Exception:
            return None

    @classmethod
    def compose_filename_parts(cls, main, args):
        main_part = cls.compose_filename_part(cls.main_filename_part, main)
        args_parts = []
        for part in cls.filename_parts:
            if (arg_part := cls.compose_filename_part(part, args)) is not None:
                if isinstance(arg_part, list):
                    args_parts.extend(arg_part)
                else:
                    args_parts.append(arg_part)
        return main_part, args_parts

    @classmethod
    def compose_filename(cls, main, args):
        main_part, args_parts = cls.compose_filename_parts(main, args)
        return ';'.join([main_part] + args_parts)

    @classmethod
    def raw_parse_filename(cls, name, parent):
        if cls.parse_cache is None:
            cls.parse_cache = {}

        if name not in cls.parse_cache:

            main_part, *parts = name.split(';')
            if not (match := cls.main_path_re.match(main_part)):
                main, args = None, None
            else:
                main = match.groupdict()
                args = {}
                for part in parts:
                    for regex in cls.filename_re_parts:
                        if match := regex.match(part):
                            values = match.groupdict()
                            is_flag = 'flag' in values and 'arg' not in values and len(values) == 2
                            if not is_flag:
                                values = {key: value for key, value in values.items() if value}
                            if not (arg_name := values.pop('flag' if is_flag else 'arg', None)):
                                continue
                            if list(values.keys()) == ['value']:
                                values = values['value']
                                if is_flag:
                                    values = values in (None, 'true')
                            args[arg_name] = values

            cls.parse_cache[name] = (main, args)

        return cls.parse_cache[name]

    def get_raw_args(self):
        return self.raw_parse_filename(self.path.name, self.path.parent)

    def get_resovled_raw_args(self):
        main, args = map(deepcopy, self.get_raw_args())
        if self.reference:
            ref_main, ref_args = map(deepcopy, self.reference.get_resovled_raw_args())
            return ref_main | main, ref_args | args
        return main, args

    @classmethod
    def parse_filename(cls, name, parent):
        main, args = map(deepcopy, cls.raw_parse_filename(name, parent))
        if main is None or args is None:
            return None, None, None, None

        ref_conf = ref = None
        if (ref_conf := cls.parse_cache[name][1].get('ref')):
            if 'key_same_page' in ref_conf:
                ref_conf['key'] = ref_conf.pop('key_same_page')
            ref_conf, ref = cls.find_reference(parent, ref_conf, main, args)
            if not ref:
                return ref_conf, None, None, None
            ref_main, ref_args = ref.get_resovled_raw_args()
            if ref_main is None or ref_args is None:
                return ref_conf, None, None, None

            main = ref_main | main

            # do not inherit "sub arguments" (things like `margin.2` if whole argument is defined in the current conf, like, in this example, `margin`)
            sub_ref_args = {}
            for key, value in ref_args.items():
                if '.' not in key:
                    continue
                parent_key = key.split('.', 1)[0]
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
            if '.' not in key:
                continue
            parent_key = key.split('.', 1)[0]
            sub_args.setdefault(parent_key, {})[key] = value
        if sub_args:
            for parent_key in sub_args.keys():
                if parent_key in args:
                    if isinstance(args[parent_key], str):
                        try:
                            parts = args[parent_key].split(',')
                            for key, value in sub_args[parent_key].items():
                                try:
                                    index = int(key.split('.')[-1])
                                    parts[index] = value
                                except Exception:
                                    continue
                            args[parent_key] = ','.join(parts)
                        except Exception:
                            pass
                    elif isinstance(args[parent_key], dict):
                        try:
                            parts = list(args[parent_key].keys())
                            for key, value in sub_args[parent_key].items():
                                try:
                                    part = key.split('.')[-1]
                                    try:
                                        index = int(part)
                                    except ValueError:
                                        if part in args[parent_key]:
                                            args[parent_key][part] = value
                                    else:
                                        args[parent_key][parts[index]] = value
                                except Exception:
                                    pass
                        except Exception:
                            pass

        try:
            main = cls.convert_main_args(main)
            if main is not None:
                if (args := cls.convert_args(args)) is not None:
                    return ref_conf, ref, main, args
        except InvalidArg as exc:
            logger.error(f'[{parent}] [{name}] {exc}')

        return ref_conf, None, None, None

    @classmethod
    def convert_main_args(cls, args):
        return args

    @classmethod
    def convert_args(cls, args):
        final_args = {
            'disabled': args.get('disabled', False),
            'name': args.get('name') or cls.unnamed,
        }
        return final_args

    @classmethod
    def get_create_base_args(cls, path, parent, identifier, args=None, path_modified_at=None):
        if args is None:
            args = {}
        return {
            'path': path,
            'path_modified_at': path_modified_at or time(),
            'name': args.get('name') or cls.unnamed,
            'disabled': args.get('disabled', False),
            cls.parent_attr: parent,
            cls.identifier_attr: identifier,
        }

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        return cls(**cls.get_create_base_args(path, parent, identifier, args, path_modified_at))

    @property
    def identifier(self):
        return getattr(self, self.identifier_attr)

    @property
    def parent(self):
        return getattr(self, self.parent_attr)

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
        if isinstance(ref_conf.get('key'), Key):
            return ref_conf['key']
        from .page import Page
        if isinstance(ref_conf.get('page'), Page):
            return ref_conf['page']
        return deck

    @classmethod
    def add_waiting_reference(cls, parent, path, ref_conf):
        cls.get_waiting_reference_holder(parent.deck, ref_conf).waiting_child_references.setdefault(cls, {})[path] = (parent, ref_conf)

    @classmethod
    def remove_waiting_reference(cls, deck, path, ref_conf):
        cls.get_waiting_reference_holder(deck, ref_conf).waiting_child_references.setdefault(cls, {}).pop(path, None)

    def get_waiting_references(self):
        return []

    def on_create(self):
        for path, parent, ref_conf in self.get_waiting_references():
            if parent.on_file_change(parent.path, path.name, file_flags.CREATE | (file_flags.ISDIR if self.is_dir else 0), entity_class=self.__class__):
                self.remove_waiting_reference(self.deck, path, ref_conf)

    def on_changed(self):
        pass

    def on_delete(self):
        for ref in list(self.referenced_by):
            ref.on_reference_deleted()
        if self.reference:
            self.reference.referenced_by.remove(self)

    def on_reference_deleted(self):
        if self.reference:
            self.add_waiting_reference(self.parent, self.path, self.ref_conf)
        self.on_delete()
        self.parent_container[self.identifier].remove_version(self.path)

    def on_child_entity_change(self, path, flags, entity_class, data_identifier, args, ref_conf, ref, modified_at=None):
        data_dict = getattr(self, entity_class.parent_container_attr)

        if (bool(flags & file_flags.ISDIR) ^ entity_class.is_dir) or (flags & file_flags.DELETE) or (flags & file_flags.MOVED_FROM):
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
            entity.on_changed()
            return False

        entity = entity_class.create_from_args(
            path=path,
            parent=self,
            identifier=data_identifier,
            args=args,
            path_modified_at=modified_at
        )
        if ref:
            entity.reference = ref
            entity.ref_conf = ref_conf
        data_dict[data_identifier].add_version(path, entity)
        entity.on_create()
        return True

    def version_activated(self):
        logger.debug(f'[{self}] Version activated: {self.path}')

    def version_deactivated(self):
        logger.debug(f'[{self}] Version deactivated: {self.path}')

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
        return value.replace(args.get('slash', DEFAULT_SLASH_REPL), '/').replace(args.get('semicolon', DEFAULT_SEMICOLON_REPL), ';')


class VersionProxy(ObjectWrapper):
    versions = None
    sort_key_func = None

    def __init__(self, sort_key_func):
        super().__init__(None)
        self.versions = {}
        self.sort_key_func = sort_key_func

    def add_version(self, key, value):
        assert key not in self.versions, f'Key {key} already in available versions'
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
            if old_subject and hasattr(old_subject, 'version_deactivated'):
                old_subject.version_deactivated()
            if new_subject and hasattr(new_subject, 'version_activated'):
                new_subject.version_activated()


VersionProxyMostRecent = partial(VersionProxy, sort_key_func=lambda key_and_obj: key_and_obj[1].path_modified_at)
versions_dict_factory = lambda: defaultdict(VersionProxyMostRecent)
