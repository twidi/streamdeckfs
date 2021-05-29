#!/usr/bin/env python3

"""
STREAMDECKIFY

A software to handle a Stream Deck from Elgato, via directories and files.

The concept is simple: with a basic directories structure, put files (or better, symbolic links) 
with specific names to handle images and programs/actions to run

Features:
- pages/overlays
- multi-layers key images with configuration in file name (margin, crop, rotate, color, opacity)
- image layers can be simple drawings (dot, rectangle, ellipses...)
- multi-layers key texts (also with configuration in file name: size, format, alignment, color, opacity, margin, wrapping, scrolling)
- action on start and key press/long-press/release, with delay, repeat...
- easily update from external script
- changes on the fly from the directories/files names via inotify
- references for key/images/texts/events (allow to reuse things and override only what is needed)

TODO:
- page on_open/open_close events
- conf to "animate" layer properties ?
- allow passing "part" of config via set-*-conf (like "-c coords.3 100%" to change the 3 value of the current "coords" settings; maybe it could also be used when overriding references?)



Example structure for the first page of an Stream Deck XL (4 rows of 8 keys):

/home/twidi/streamdeck-data/MYSTREAMDECKSERIAL
├── PAGE_1;name=main
│   ├── KEY_ROW_1_COL_1;ref=spotify:toggle
│   │   └── ON_LONGPRESS;page=spotify;duration-min=300
│   ├── KEY_ROW_2_COL_1;name=volume-up
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/volume/assets/icon-increase.png
│   │   ├── IMAGE;ref=ref:draw:background;fill=#29abe2
│   │   ├── IMAGE;ref=ref:img:overlay
│   │   └── ON_PRESS;every=250;max-runs=34;unique -> /home/twidi/dev/streamdeck-scripts/volume/increase.sh
│   ├── KEY_ROW_2_COL_8;name=deck-brightness-up
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/deck/assets/icon-brightness-increase.png
│   │   ├── IMAGE;ref=ref:draw:background;fill=#ffa000
│   │   ├── IMAGE;ref=ref:img:overlay
│   │   └── ON_PRESS;brightness=+10;every=250;max-runs=10
│   ├── KEY_ROW_3_COL_1;name=volume-down
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,25,15,15 -> /home/twidi/dev/streamdeck-scripts/volume/assets/icon-decrease.png
│   │   ├── IMAGE;ref=ref:img:overlay
│   │   ├── IMAGE;ref=:volume-up:background
│   │   └── ON_PRESS;every=250;max-runs=34;unique -> /home/twidi/dev/streamdeck-scripts/volume/decrease.sh
│   ├── KEY_ROW_3_COL_2;name=webcam;disabled
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=18,18,18,18 -> /home/twidi/dev/streamdeck-scripts/webcam/assets/icon.png
│   │   ├── IMAGE;ref=ref:draw:background;fill=#29abe2;colorize=red
│   │   ├── IMAGE;ref=ref:img:overlay
│   │   └── ON_START;disabled -> /home/twidi/dev/streamdeck-scripts/webcam/listen-changes.py
│   ├── KEY_ROW_3_COL_8;name=deck-brightness-down
│   │   ├── IMAGE;layer=1;name=icon;colorize=#ffffff;margin=15,20,15,20 -> /home/twidi/dev/streamdeck-scripts/deck/assets/icon-brightness-decrease.png
│   │   ├── IMAGE;ref=:deck-brightness-up:background
│   │   ├── IMAGE;ref=ref:img:overlay
│   │   └── ON_PRESS;ref=:deck-brightness-up:;brightness=-10
│   ├── KEY_ROW_4_COL_1;name=volume-mute
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/volume/assets/icon-mute.png
│   │   ├── IMAGE;layer=2;name=volume;ref=ref:draw:progress;coords=0%,92,26%,92
│   │   ├── IMAGE;ref=ref:img:overlay
│   │   ├── IMAGE;ref=:volume-up:background
│   │   ├── ON_PRESS -> /home/twidi/dev/streamdeck-scripts/volume/toggle-mute.sh
│   │   └── ON_START -> /home/twidi/dev/streamdeck-scripts/volume/listen-changes.sh
│   └── KEY_ROW_4_COL_2;ref=microphone:microphone
│       └── ON_LONGPRESS;page=microphone;duration-min=300;overlay

Note that nearly every files (`IMAGES*`, `ON_*`) are links to images and scripts hosted in another directory. It's not mandatory but better to keep things organized.
Or they can reference other images (like `IMAGE;ref=:volume-up:background`).

`ON_*` files must be executable (`chmod +x`) and can be in any language (don't forget shebangs!). Except for some actions where the file can be empty, only the name counts:
- `action=brightness` (see `KEY_ROW_3_COL_8/ON_PRESS*` and `KEY_ROW_4_COL_1/ON_PRESS*`) to change the deck brightness (with absolute or relative values)
- `action=page` (see `KEY_ROW_4_COL_8/ON_PRESS*`) to change page (a specific number or name, or thefirst, previous (-1), next (+1), or previously displayed (like the "back" action in browsers) page)


"""

import logging
import json
import os
import re
import signal
import threading
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from queue import SimpleQueue, Empty
from time import time, sleep
from typing import Tuple

import click
import click_log
import psutil
from inotify_simple import INotify, flags as f
from peak.util.proxies import ObjectWrapper  # pip install ProxyTypes
from PIL import Image, ImageEnhance, ImageFont, ImageDraw
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

try:
    from prctl import set_name as set_thread_name
except ImportError:
    set_thread_name = lambda name: None


DEFAULT_BRIGHTNESS = 30
RE_PART_0_100 = '0*(?:\d{1,2}?|100)'
RE_PART_PERCENT = '(?:\d+|\d*\.\d+)%'
RE_PART_PERCENT_OR_NUMBER = f'(?:\d+|{RE_PART_PERCENT})'
RE_PART_COLOR = '\w+|(?:#[a-fA-F0-9]{6})'
RE_PART_COLOR_WITH_POSSIBLE_ALPHA = '\w+|(?:#[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?)'

logger = logging.getLogger(__name__)
click_log.basic_config(logger)

ASSETS_PATH = Path.resolve(Path(__file__)).parent / 'assets'
LONGPRESS_DURATION_MIN = 300  # in ms

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


class Inotifier:
    def __init__(self):
        self.running = False
        self.inotify = INotify()
        self.watchers = {}
        self.watched = {}

    def stop(self):
        self.running = False
        if self.inotify:
            self.inotify.close()

    def add_watch(self, directory, owner, flags=f.CREATE | f.DELETE | f.MODIFY | f.MOVED_FROM | f.MOVED_TO):
        if directory in self.watched:
            return
        watcher = self.inotify.add_watch(directory, flags)
        if not isinstance(directory, Path):
            directory = Path(directory)
        self.watchers[watcher] = (directory, owner)
        self.watched[directory] = watcher

    def remove_watch(self, directory):
        if not isinstance(directory, Path):
            directory = Path(directory)
        watcher = self.watched.pop(directory, None)
        if not watcher:
            return
        try:
            self.inotify.rm_watch(watcher)
        except OSError:
            # can happen when a directory is deleted, the watcher is removed on the kernel side
            pass
        self.watchers.pop(watcher)

    def run(self):
        set_thread_name('FilesWatcher')
        self.running = True
        while True:
            if not self.running or self.inotify.closed:
                break
            try:
                for event in self.inotify.read(timeout=500):
                    if not self.running or self.inotify.closed:
                        break
                    watcher, name, flags = event.wd, event.name, event.mask
                    if watcher not in self.watchers:
                        continue
                    directory, owner = self.watchers[watcher]
                    if f.IGNORED & flags:
                        if watcher in self.watchers:
                            self.remove_watch(directory)
                        continue
                    logger.debug(f'{event} ; {directory} ; FLAGS: {", ".join(str(flag) for flag in f.from_mask(event.mask))}')
                    owner.on_file_change(name, flags, time())
            except ValueError:
                # happen if read while closed
                break


class FILTER_DENY: pass


class InvalidArg(Exception):
    pass


@dataclass(eq=False)
class Entity:

    is_dir = False
    path_glob = None
    main_path_re = None
    filename_re_parts = [
        re.compile('^(?P<flag>disabled)(?:=(?P<value>false|true))?$'),
        re.compile('^(?P<arg>name)=(?P<value>[^;]+)$'),
    ]
    main_filename_part = None
    name_filename_part = lambda args: f'name={args["name"]}' if args.get('name') else None
    disabled_filename_part = lambda args: 'disabled' if args.get('disabled', False) in (True, 'true', None) else None
    filename_parts = [main_filename_part, disabled_filename_part]

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
        main, args = self.get_raw_args()
        if self.reference:
            ref_main, ref_args = self.reference.get_resovled_raw_args()
            return ref_main | main, ref_args | args
        return main, args

    @classmethod
    def parse_filename(cls, name, parent):
        main, args = cls.raw_parse_filename(name, parent)
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
            args = ref_args | args

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
            'name': args.get('name') or '*unnamed*',
        }
        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        attrs = dict(
            path=path,
            path_modified_at=path_modified_at,
            name=args['name'],
            disabled=args['disabled'],
        )
        attrs[cls.parent_attr] = parent
        attrs[cls.identifier_attr] = identifier
        return cls(**attrs)

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
        if self.reference:
            return self.reference.resolved_path
        return self.path

    @staticmethod
    def get_waiting_reference_holder(deck, ref_conf):
        if isinstance(ref_conf.get('key'), Key):
            return ref_conf['key']
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
            if parent.on_file_change(path.name, f.CREATE | (f.ISDIR if self.is_dir else 0), entity_class=self.__class__):
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

        if (bool(flags & f.ISDIR) ^ entity_class.is_dir) or (flags & f.DELETE) or (flags & f.MOVED_FROM):
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

    def rename(self, new_filename):
        if (new_path := self.path.parent / new_filename) != self.path:
            self.path = self.path.replace(new_path)
            return True
        return False


@dataclass(eq=False)
class KeyFile(Entity):
    parent_attr = 'key'

    key: 'Key'

    @property
    def page(self):
        return self.key.page

    @property
    def deck(self):
        return self.page.deck

    @classmethod
    def find_reference_key(cls, parent, ref_conf):
        final_ref_conf = ref_conf.copy()
        if ref_page := ref_conf.get('page'):
            if not (page := parent.deck.find_page(ref_page)):
                return final_ref_conf, None
        else:
            final_ref_conf['page'] = page = parent.page
        if ref_key := ref_conf.get('key'):
            if not (key := page.find_key(ref_key)):
                return final_ref_conf, None
        else:
            final_ref_conf['key'] = key = parent

        return final_ref_conf, key

    @classmethod
    def iter_waiting_references_for_key(cls, check_key):
        for path, (parent, ref_conf) in check_key.waiting_child_references.get(cls, {}).items():
            yield check_key, path, parent, ref_conf
        for path, (parent, ref_conf) in check_key.page.waiting_child_references.get(cls, {}).items():
            if (key := check_key.page.find_key(ref_conf['key'])) and key.key == check_key.key:
                yield key, path, parent, ref_conf
        for path, (parent, ref_conf) in check_key.deck.waiting_child_references.get(cls, {}).items():
            if (page := check_key.deck.find_page(ref_conf['page'])) and page.number == check_key.page.number and  (key := page.find_key(ref_conf['key'])) and key.key == check_key.key:
                yield key, path, parent, ref_conf


class NamedThread(threading.Thread):
    def __init__(self, name=None):
        self.prctl_name = name[:15] if name else None
        super().__init__(name=name)

    def run(self):
        set_thread_name(self.prctl_name)


class StopEventThread(NamedThread):
    def __init__(self, name=None):
        super().__init__(name=name)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def is_stopped(self):
        return self.stop_event.is_set()


class Delayer(StopEventThread):
    def __init__(self, func, delay, end_callback=None, name=None):
        super().__init__(name=name)
        self.func = func
        self.delay = delay
        self.run_event = threading.Event()
        self.end_callback = end_callback
        self.start_time = None
        self.duration = None

    def did_run(self):
        return self.run_event.is_set()

    def run(self):
        super().run()
        self.start_time = time()
        if not self.stop_event.wait(self.delay):
            self.run_event.set()
            self.func()
        self.duration = time() - self.start_time
        if self.end_callback:
            self.end_callback(thread=self)


class Repeater(StopEventThread):
    def __init__(self, func, every, max_runs=None, end_callback=None, wait_first=0, name=None):
        super().__init__(name=name)
        self.func = func
        self.every = every
        self.max_runs = max_runs
        self.end_callback = end_callback
        self.runs_count = 0
        self.wait_first = wait_first

    def run(self):
        super().run()
        if self.max_runs == 0:
            return
        additional_time = self.wait_first
        while not self.stop_event.wait(self.every + additional_time):
            self.func()
            self.runs_count +=1 
            additional_time = 0
            if self.max_runs is not None and self.runs_count >= self.max_runs:
                break
        if self.end_callback:
            self.end_callback(thread=self)


@dataclass(eq=False)
class KeyEvent(KeyFile):
    path_glob = 'ON_*'
    main_path_re = re.compile('^ON_(?P<kind>PRESS|LONGPRESS|RELEASE|START)(?:;|$)')
    filename_re_parts = Entity.filename_re_parts + [
        # reference
        re.compile('^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<event>.*)$'),  # we'll use current kind if no event given
        # if the process must be detached from ours (ie launch and forget)
        re.compile('^(?P<flag>detach)(?:=(?P<value>false|true))?$'),
        # delay before launching action
        re.compile('^(?P<arg>wait)=(?P<value>\d+)$'),
        # repeat every, max times (ignored if not press/start)
        re.compile('^(?P<arg>every)=(?P<value>\d+)$'),
        re.compile('^(?P<arg>max-runs)=(?P<value>\d+)$'),
        # max duration a key must be pressed to run the action, only for press
        re.compile('^(?P<arg>duration-max)=(?P<value>\d+)$'),
        # min duration a key must be pressed to run the action, only for longpress/release
        re.compile('^(?P<arg>duration-min)=(?P<value>\d+)$'),
        # action brightness
        re.compile(f'^(?P<arg>brightness)=(?P<brightness_operation>[+-=]?)(?P<brightness_level>{RE_PART_0_100})$'),
        # action page
        re.compile('^(?P<arg>page)=(?P<value>.+)$'),
        re.compile('^(?P<flag>overlay)(?:=(?P<value>false|true))?$'),
        # action run
        re.compile('^(?P<arg>program)=(?P<value>.+)$'),
        re.compile('^(?P<arg>slash)=(?P<value>.+)$'),
        # do not run many times the same program at the same time
        re.compile('^(?P<flag>unique)(?:=(?P<value>false|true))?$'),
    ]
    main_filename_part = lambda args: f'ON_{args["kind"].upper()}'
    filename_parts = [
        Entity.name_filename_part,
        lambda args: f'ref={ref.get("page") or ""}:{ref.get("key") or ref.get("key_same_page") or ""}:{ref["event"]}' if (ref := args.get('ref')) else None,
        lambda args: f'brightness={brightness.get("brightness_operation", "")}{brightness["brightness_level"]}' if (brightness := args.get('brightness')) else None,
        lambda args: f'page={page}' if (page := args.get('page')) else None,
        lambda args: f'program={program}' if (program := args.get('program')) else None,
        lambda args: f'slash={slash}' if (slash := args.get('slash')) else None,
        lambda args: f'wait={wait}' if (wait := args.get('wait')) else None,
        lambda args: f'every={every}' if (every := args.get('every')) else None,
        lambda args: f'max-runs={max_runs}' if (max_runs := args.get('max-runs')) else None,
        lambda args: f'duration-min={duration_min}' if (duration_min := args.get('duration-min')) else None,
        lambda args: f'duration-max={duration_max}' if (duration_max := args.get('duration-max')) else None,
        lambda args: 'overlay' if args.get('overlay', False) in (True, 'true', None) else None,
        lambda args: 'detach' if args.get('detach', False) in (True, 'true', None) else None,
        lambda args: 'unique' if args.get('unique', False) in (True, 'true', None) else None,
        Entity.disabled_filename_part,
    ]

    identifier_attr = 'kind'
    parent_container_attr = 'events'

    kind: str

    def __post_init__(self):
        super().__post_init__()
        self.mode = None
        self.to_stop = False
        self.brightness_level = ('=', DEFAULT_BRIGHTNESS)
        self.repeat_every = None
        self.max_runs = None
        self.wait = 0
        self.duration_max = None
        self.duration_min = None
        self.overlay = False
        self.detach = False
        self.program = None
        self.unique = False
        self.pids = []
        self.activated = False
        self.repeat_thread = None
        self.wait_thread = None
        self.duration_thread = None
        self.ended_running = threading.Event()

    @property
    def str(self):
        return f'EVENT {self.kind} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f'{self.key}, {self.str}'

    @classmethod
    def convert_main_args(cls, args):
        if (args := super().convert_main_args(args)) is None:
            return None
        args['kind'] = args['kind'].lower()
        return args

    @classmethod
    def convert_args(cls, args):
        final_args = super().convert_args(args)

        if len([1 for key in ('page', 'brightness', 'program') if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "page", "brightness", "program')

        if args.get('page'):
            final_args['mode'] = 'page'
        elif args.get('brightness'):
            final_args['mode'] = 'brightness'
        elif args.get('program'):
            final_args['mode'] = 'program'
        else:
            final_args['mode'] = 'path'

        if final_args['mode'] in ('path', 'program'):
            if final_args['mode'] == 'program':
                final_args['program'] = args['program'].replace(args.get('slash', '\\\\'), '/')# double \ by default
            final_args['detach'] = args.get('detach', False)
            final_args['unique'] = args.get('unique', False)
        elif final_args['mode'] == 'page':
            final_args['page_ref'] = args['page']
            if 'page_ref' != Page.BACK and 'overlay' in args:
                final_args['overlay'] = args['overlay']
        elif final_args['mode'] == 'brightness':
            final_args['brightness_level'] = (
                args['brightness'].get('brightness_operation') or '=',
                int(args['brightness']['brightness_level'])
            )
        if 'every' in args:
            final_args['repeat-every'] = int(args['every'])
        if 'max-runs' in args:
            final_args['max_runs'] = int(args['max-runs'])
        if 'wait' in args:
            final_args['wait'] = int(args['wait'])
        if 'duration-max' in args:
            final_args['duration-max'] = int(args['duration-max'])
        if 'duration-min' in args:
            final_args['duration-min'] = int(args['duration-min'])

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        event = super().create_from_args(path, parent, identifier, args, path_modified_at)
        event.mode = args.get('mode')
        if event.mode == 'brightness':
            event.brightness_level = args['brightness_level']
        elif event.mode == 'page':
            event.page_ref = args['page_ref']
            event.overlay = args.get('overlay', False)
        elif event.mode in ('path', 'program'):
            event.detach = args['detach']
            event.unique = args['unique']
            event.to_stop = event.kind == 'start' and not event.detach
            if event.mode == 'program':
                event.program = args['program']
        if event.kind in ('press', 'start'):
            if args.get('repeat-every'):
                event.repeat_every = args['repeat-every']
                event.max_runs = args.get('max_runs')
        if args.get('wait'):
            event.wait = args['wait']
        if event.kind == 'press':
            if args.get('duration-max'):
                event.duration_max = args['duration-max']
        if event.kind in ('longpress', 'release'):
            if args.get('duration-min'):
                event.duration_min = args['duration-min']
            elif event.kind == 'longpress':
                event.duration_min = LONGPRESS_DURATION_MIN
        return event

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, key = cls.find_reference_key(parent, ref_conf)
        if not final_ref_conf.get('event'):
            final_ref_conf['event'] = main['kind'].lower()
        if not key:
            return final_ref_conf, None
        return final_ref_conf, key.find_event(final_ref_conf['event'])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for key, path, parent, ref_conf
            in self.iter_waiting_references_for_key(self.key)
            if (event := key.find_event(ref_conf['event'])) and event.kind == self.kind
        ]

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        return main.get('find') == filter

    def start_repeater(self):
        if not self.repeat_every:
            return
        if self.repeat_thread:
            return
        # use `self.max_runs - 1` because action was already run once
        max_runs = (self.max_runs - 1) if self.max_runs else None
        self.repeat_thread = Repeater(self.run, self.repeat_every/1000, max_runs=max_runs, end_callback=self.stop_repeater, name=f'{self.kind.capitalize()[:4]}Rep{self.page.number}.{self.key.row}{self.key.col}')
        self.repeat_thread.start()

    def stop_repeater(self, *args, **kwargs):
        if not self.repeat_thread:
            return
        self.repeat_thread.stop()
        self.repeat_thread = None

    def start_waiter(self, duration=None):
        if self.wait_thread:
            return
        if duration is None:
            duration = self.wait / 1000
        self.wait_thread = Delayer(self.run_and_repeat, duration, end_callback=self.stop_waiter, name=f'{self.kind.capitalize()[:4]}Wait{self.page.number}.{self.key.row}{self.key.col}')
        self.wait_thread.start()

    def stop_waiter(self, *args, **kwargs):
        if not self.wait_thread:
            return
        self.wait_thread.stop()
        self.wait_thread = None

    def stop_duration_waiter(self, *args, **kwargs):
        if not self.duration_thread:
            return
        self.duration_thread.stop()
        self.duration_thread = None

    def run_if_less_than_duration_max(self, thread):
        if thread.did_run():
            # already aborted
            self.stop_duration_waiter()
            logger.info(f'[{self}] ABORTED (pressed more than {self.duration_max}ms)')
            return
        self.stop_duration_waiter()
        # if it was stopped, it's by the release button during the duration_max time, so we know the
        # button was pressed less time than this duration_max, so we can run the action
        # but if we have a configured wait time, we must ensure we wait for it
        if self.wait and (wait_left := self.wait/1000 - thread.duration) > 0:
            self.start_waiter(wait_left)
        else:
            self.run_and_repeat()

    def run(self):
        try:
            if self.mode == 'brightness':
                self.deck.set_brightness(*self.brightness_level)
            elif self.mode == 'page':
                self.deck.go_to_page(self.page_ref, self.overlay)
            elif self.mode in ('path', 'program'):
                if self.unique and not self.ended_running.is_set():
                    logger.warning(f'[{self} STILL RUNNING, EXECUTION SKIPPED [PIDS: {", ".join(str(pid) for pid in self.pids if pid in Manager.processes)}]')
                    return True
                if self.mode == 'path':
                    program = self.resolved_path
                    shell = False
                else:
                    program = self.program
                    shell = True
                if (pid := Manager.start_process(program, register_stop=self.to_stop, detach=self.detach, shell=shell, done_event=self.ended_running)):
                    self.pids.append(pid)
        except Exception:
            logger.exception(f'[{self}] Failure while running the command')
        return True

    def wait_run_and_repeat(self, on_press=False):
        if self.duration_max:
            self.duration_thread = Delayer(lambda: None, self.duration_max/1000, end_callback=self.run_if_less_than_duration_max, name=f'{self.kind.capitalize()[:4]}Max{self.page.number}.{self.key.row}{self.key.col}')
            self.duration_thread.start()
        elif self.kind == 'longpress' and on_press:
            # will call this function again, but with on_press False so we'll then go to start_water/run_and_repeat
            self.duration_thread = Delayer(self.wait_run_and_repeat, self.duration_min/1000, end_callback=self.stop_duration_waiter, name=f'{self.kind.capitalize()[:4]}Min{self.page.number}.{self.key.row}{self.key.col}')
            self.duration_thread.start()
        elif self.wait:
            self.start_waiter()
        else:
            self.run_and_repeat()

    def run_and_repeat(self):
        if not self.run():
            return
        self.start_repeater()

    def version_activated(self):
        super().version_activated()
        if self.disabled or self.key.disabled or self.page.disabled:
            return
        self.activate()

    def version_deactivated(self):
        super().version_deactivated()
        if self.disabled or self.key.disabled or self.page.disabled:
            return
        self.deactivate()

    def stop(self):
        self.stop_waiter()
        self.stop_repeater()
        if self.is_stoppable:
            if not self.pids:
                return
            while self.pids and (pid := self.pids.pop(0)):
                try:
                    Manager.terminate_process(pid)
                except Exception:
                    logger.exception(f'[{self}] Failure while stopping the command (pid {pid})')

    @property
    def is_stoppable(self):
        return self.kind == 'start' and self.to_stop and self.mode in ('path', 'program') and self.pids

    def activate(self, page=None):
        if page is None:
            page = self.page
        if not page.is_current:
            return
        if self.activated:
            return
        self.activated = True
        self.ended_running.set()
        if self.kind == 'start' and self.mode in ('path', 'program'):
            self.wait_run_and_repeat()

    def deactivate(self):
        if not self.activated:
            return
        self.activated = False
        self.stop()


@dataclass(eq=False)
class keyImagePart(KeyFile):

    no_margins = {'top': ('int', 0), 'right': ('int', 0), 'bottom': ('int', 0), 'left': ('int', 0)}

    def __str__(self):
        return f'{self.key}, {self.str}'

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
        kind = 'int'
        if value.endswith('%'):
            kind = '%'
            value = float(value[:-1])
        else:
            value = int(value)
        return kind, value

    def convert_coordinate(self, value, dimension, source=None):
        kind, value = value
        if kind == 'int':
            return value
        if kind == '%':
            return int(value * (getattr(self.key if source is None else source, dimension) - 1) / 100)

    @staticmethod
    def convert_angle(value):
        kind, value = value
        if kind == 'int':
            return value
        if kind == '%':
            return round(value * 360 / 100)

    def convert_margins(self):
        return {
            margin_name: self.convert_coordinate(margin, 'width' if margin_name in ('left', 'right')  else 'height')
            for margin_name, margin in (self.margin or self.no_margins).items()
        }

    def apply_opacity(self, image):
        if self.opacity is None:
            return
        image.putalpha(ImageEnhance.Brightness(image.getchannel('A')).enhance(self.opacity/100))


@dataclass(eq=False)
class KeyImageLayer(keyImagePart):
    path_glob = 'IMAGE*'
    main_path_re = re.compile('^(?P<kind>IMAGE)(?:;|$)')
    filename_re_parts = Entity.filename_re_parts + [
        re.compile('^(?P<arg>layer)=(?P<value>\d+)$'),
        re.compile('^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<layer>.*)$'),  # we'll use -1 if no layer given
        re.compile(f'^(?P<arg>colorize)=(?P<value>{RE_PART_COLOR})$'),
        re.compile(f'^(?P<arg>margin)=(?P<top>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<right>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<bottom>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<left>-?{RE_PART_PERCENT_OR_NUMBER})$'),
        re.compile(f'^(?P<arg>crop)=(?P<left>{RE_PART_PERCENT_OR_NUMBER}),(?P<top>{RE_PART_PERCENT_OR_NUMBER}),(?P<right>{RE_PART_PERCENT_OR_NUMBER}),(?P<bottom>{RE_PART_PERCENT_OR_NUMBER})$'),
        re.compile(f'^(?P<arg>opacity)=(?P<value>{RE_PART_0_100})$'),
        re.compile(f'^(?P<arg>rotate)=(?P<value>-?{RE_PART_PERCENT_OR_NUMBER})$'),
        re.compile('^(?P<arg>draw)=(?P<value>line|rectangle|points|polygon|ellipse|arc|chord|pieslice)$'),
        re.compile(f'^(?P<arg>coords)=(?P<value>-?{RE_PART_PERCENT_OR_NUMBER},-?{RE_PART_PERCENT_OR_NUMBER}(?:,-?{RE_PART_PERCENT_OR_NUMBER},-?{RE_PART_PERCENT_OR_NUMBER})*)$'),
        re.compile(f'^(?P<arg>outline)=(?P<value>{RE_PART_COLOR_WITH_POSSIBLE_ALPHA})$'),
        re.compile(f'^(?P<arg>fill)=(?P<value>{RE_PART_COLOR_WITH_POSSIBLE_ALPHA})$'),
        re.compile('^(?P<arg>width)=(?P<value>\d+)$'),
        re.compile('^(?P<arg>radius)=(?P<value>\d+)$'),
        re.compile(f'^(?P<arg>angles)=(?P<value>-?{RE_PART_PERCENT_OR_NUMBER},-?{RE_PART_PERCENT_OR_NUMBER})$'),
    ]
    main_filename_part = lambda args: 'IMAGE'
    filename_parts = [
        lambda args: f'layer={layer}' if (layer := args.get('layer')) else None,
        Entity.name_filename_part,
        lambda args: f'ref={ref.get("page") or ""}:{ref.get("key") or ref.get("key_same_page") or ""}:{ref.get("layer") or ""}' if (ref := args.get('ref')) else None,
        lambda args: f'draw={draw}' if (draw := args.get('draw')) else None,
        lambda args: f'coords={coords}' if (coords := args.get('coords')) else None,
        lambda args: f'outline={color}' if (color := args.get('outline')) else None,
        lambda args: f'fill={color}' if (color := args.get('fill')) else None,
        lambda args: f'width={width}' if (width := args.get('width')) else None,
        lambda args: f'radius={radius}' if (radius := args.get('radius')) else None,
        lambda args: f'angles={angles}' if (angles := args.get('angles')) else None,
        lambda args: f'crop={crop["left"]},{crop["top"]},{crop["right"]},{crop["bottom"]}' if (crop := args.get('crop')) else None,
        lambda args: f'colorize={color}' if (color := args.get('colorize')) else None,
        lambda args: f'rotate={rotate}' if (rotate := args.get('rotate')) else None,
        lambda args: f'margin={margin["top"]},{margin["right"]},{margin["bottom"]},{margin["left"]}' if (margin := args.get('margin')) else None,
        lambda args: f'opacity={opacity}' if (opacity := args.get('opacity')) else None,
        Entity.disabled_filename_part,
    ]

    identifier_attr = 'layer'
    parent_container_attr = 'layers'

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
    def convert_args(cls, args):
        final_args = super().convert_args(args)
        final_args['layer'] = int(args['layer']) if 'layer' in args else -1  # -1 for image used if no layers
        for name in ('margin', 'crop'):
            if name not in args:
                continue
            final_args[name] = {}
            for part, val in list(args[name].items()):
                final_args[name][part] = cls.parse_value_or_percent(val)
        if 'colorize' in args:
            final_args['color'] = args['colorize']
        if 'opacity' in args:
            final_args['opacity'] = int(args['opacity'])
        if 'rotate' in args:
            # we negate the given value because PIL rotates counterclockwise
            final_args['rotate'] = -cls.convert_angle(cls.parse_value_or_percent(args['rotate']))
        if 'draw' in args:
            final_args['draw'] = args['draw']
            if 'coords' in args:
                final_args['draw_coords'] = tuple(cls.parse_value_or_percent(val) for val in args['coords'].split(','))
            final_args['draw_outline_color'] = args.get('outline') or 'white'
            if 'fill' in args:
                final_args['draw_fill_color'] = args['fill']
            final_args['draw_outline_width'] = int(args.get('width') or 1)
            if 'radius' in args:
                final_args['draw_radius'] = int(args['radius'])
            if 'angles' in args:
                # we remove 90 degres from given values because PIL starts at 3 o'clock
                final_args['draw_angles'] = tuple(cls.convert_angle(cls.parse_value_or_percent(val)) - 90 for val in args['angles'].split(','))
        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        layer = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in ('margin', 'crop', 'color', 'opacity', 'rotate', 'draw', 'draw_coords', 'draw_outline_color', 'draw_fill_color', 'draw_outline_width', 'draw_radius', 'draw_angles'):
            if key not in args:
                continue
            setattr(layer, key, args[key])
        return layer

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, key = cls.find_reference_key(parent, ref_conf)
        if not final_ref_conf.get('layer'):
            final_ref_conf['layer'] = -1
        if not key:
            return final_ref_conf, None
        return final_ref_conf, key.find_layer(final_ref_conf['layer'])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for key, path, parent, ref_conf
            in self.iter_waiting_references_for_key(self.key)
            if (layer := key.find_layer(ref_conf['layer'])) and layer.layer == self.layer
        ]

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if args['layer'] == int(filter):
                return True
        except ValueError:
            pass
        return args.get('name') == filter

    def on_changed(self):
        self.compose_cache = None
        self.key.on_image_changed()
        for reference in self.referenced_by:
            reference.on_changed()

    def _compose(self):

        image_size = self.key.image_size
        if self.draw:
            layer_image = Image.new("RGBA", image_size)
            drawer = ImageDraw.Draw(layer_image)
            coords = [
                self.convert_coordinate(coord, 'height' if index % 2 else 'width')
                for index, coord in enumerate(self.draw_coords)
            ]
            if self.draw == 'line':
                drawer.line(coords, fill=self.draw_outline_color, width=self.draw_outline_width)
            elif self.draw == 'rectangle':
                if self.draw_radius:
                    drawer.rounded_rectangle(coords, outline=self.draw_outline_color, fill=self.draw_fill_color, width=self.draw_outline_width, radius=self.draw_radius)
                else:
                    drawer.rectangle(coords, outline=self.draw_outline_color, fill=self.draw_fill_color, width=self.draw_outline_width)
            elif self.draw == 'points':
                drawer.point(coords, fill=self.draw_outline_color)
            elif self.draw == 'polygon':
                drawer.polygon(coords, outline=self.draw_outline_color, fill=self.draw_fill_color)
            elif self.draw == 'ellipse':
                drawer.ellipse(coords, outline=self.draw_outline_color, fill=self.draw_fill_color, width=self.draw_outline_width)
            elif self.draw == 'arc':
                drawer.arc(coords, start=self.draw_angles[0], end=self.draw_angles[1], fill=self.draw_outline_color, width=self.draw_outline_width)
            elif self.draw == 'chord':
                drawer.chord(coords, start=self.draw_angles[0], end=self.draw_angles[1], outline=self.draw_outline_color, fill=self.draw_fill_color, width=self.draw_outline_width)
            elif self.draw == 'pieslice':
                drawer.pieslice(coords, start=self.draw_angles[0], end=self.draw_angles[1], outline=self.draw_outline_color, fill=self.draw_fill_color, width=self.draw_outline_width)

        else:
            layer_image = Image.open(self.resolved_path)

        if self.crop:
            crops = {
                crop_name: self.convert_coordinate(crop, 'width' if crop_name in ('left', 'right')  else 'height', source=layer_image)
                for crop_name, crop in self.crop.items()
            }
            layer_image = layer_image.crop((crops['left'], crops['top'], crops['right'], crops['bottom']))

        if self.rotate:
            layer_image = layer_image.rotate(self.rotate)

        margins = self.convert_margins()
        thumbnail_max_width = image_size[0] - (margins['right'] + margins['left'])
        thumbnail_max_height = image_size[1] - (margins['top'] + margins['bottom'])
        thumbnail = layer_image.convert("RGBA")
        thumbnail.thumbnail((thumbnail_max_width, thumbnail_max_height), Image.LANCZOS)
        thumbnail_x = (margins['left'] + round((thumbnail_max_width - thumbnail.width) / 2))
        thumbnail_y = (margins['top'] + round((thumbnail_max_height - thumbnail.height) / 2))

        if self.color:
            alpha = thumbnail.getchannel('A')
            thumbnail = Image.new('RGBA', thumbnail.size, color=self.color)
            thumbnail.putalpha(alpha)

        self.apply_opacity(thumbnail)

        return thumbnail, thumbnail_x, thumbnail_y, thumbnail


@dataclass(eq=False)
class KeyTextLine(keyImagePart):
    path_glob = 'TEXT*'
    main_path_re = re.compile('^(?P<kind>TEXT)(?:;|$)')
    filename_re_parts = Entity.filename_re_parts + [
        re.compile('^(?P<arg>line)=(?P<value>\d+)$'),
        re.compile('^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<text_line>.*)$'),  # we'll use -1 if no line given
        re.compile('^(?P<arg>text)=(?P<value>.+)$'),
        re.compile('^(?P<arg>size)=(?P<value>\d+)$'),
        re.compile('^(?P<arg>weight)(?:=(?P<value>thin|light|regular|medium|bold|black))?$'),
        re.compile('^(?P<flag>italic)(?:=(?P<value>false|true))?$'),
        re.compile('^(?P<arg>align)(?:=(?P<value>left|center|right))?$'),
        re.compile('^(?P<arg>valign)(?:=(?P<value>top|middle|bottom))?$'),
        re.compile(f'^(?P<arg>color)=(?P<value>{RE_PART_COLOR})$'),
        re.compile(f'^(?P<arg>opacity)=(?P<value>{RE_PART_0_100})$'),
        re.compile('^(?P<flag>wrap)(?:=(?P<value>false|true))?$'),
        re.compile(f'^(?P<arg>margin)=(?P<top>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<right>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<bottom>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<left>-?{RE_PART_PERCENT_OR_NUMBER})$'),
        re.compile('^(?P<arg>scroll)=(?P<value>\d+)$'),
        # to read text from a file
        re.compile('^(?P<arg>file)=(?P<value>.+)$'),
        re.compile('^(?P<arg>slash)=(?P<value>.+)$'),
    ]
    main_filename_part = lambda args: 'TEXT'
    filename_parts = [
        lambda args: f'line={line}' if (line := args.get('line')) else None,
        Entity.name_filename_part,
        lambda args: f'ref={ref.get("page") or ""}:{ref.get("key") or ref.get("key_same_page") or ""}:{ref.get("text_line") or ""}' if (ref := args.get('ref')) else None,
        lambda args: f'file={file}' if (file := args.get('file')) else None,
        lambda args: f'slash={slash}' if (slash := args.get('slash')) else None,
        lambda args: f'text={text}' if (text := args.get('text')) else None,
        lambda args: f'size={size}' if (size := args.get('size')) else None,
        lambda args: f'weight={weight}' if (weight := args.get('weight')) else None,
        lambda args: 'italic' if args.get('italic', False) in (True, 'true', None) else None,
        lambda args: f'color={color}' if (color := args.get('color')) else None,
        lambda args: f'align={align}' if (align := args.get('align')) else None,
        lambda args: f'valign={valign}' if (valign := args.get('valign')) else None,
        lambda args: f'margin={margin["top"]},{margin["right"]},{margin["bottom"]},{margin["left"]}' if (margin := args.get('margin')) else None,
        lambda args: f'opacity={opacity}' if (opacity := args.get('opacity')) else None,
        lambda args: f'scroll={scroll}' if (scroll := args.get('scroll')) else None,
        lambda args: 'wrap' if args.get('wrap', False) in (True, 'true', None) else None,
        Entity.disabled_filename_part,
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
        self.mode = None
        self.text = None
        self.file = None
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
        if 'file' in args:
            if args['file'] == '__self__':
                final_args['mode'] = 'content'
            else:
                raise InvalidArg('Argument "file" used with something else than "__self__" is not allowed yet.')
                final_args['mode'] = 'file'
                final_args['file'] = args['file'].replace(args.get('slash', '\\\\'), '/')# double \ by default
        else:
            final_args['mode'] = 'text'
            final_args['text'] = args.get('text') or ''
        final_args['size'] = int(args.get('size') or 20)
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
            final_args['scroll'] = int(args['scroll'])

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        line = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in ('mode', 'file', 'text', 'size', 'weight', 'italic', 'align', 'valign', 'color', 'opacity', 'wrap', 'margin', 'scroll'):
            if key not in args:
                continue
            setattr(line, key, args[key])
        if not line.deck.scroll_activated:
            line.scroll = None
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
                try:
                    with open(self.path) as f:
                        self.text = f.read()
                except:
                    pass
                if not self.text and self.reference:
                    self.text = self.reference.resolved_text
            elif self.mode == 'file':
                try:
                    with open(self.file) as f:
                        self.text = f.read()
                except:
                    pass
        return self.text

    @classmethod
    def  get_text_size_drawer(cls):
        if cls.text_size_drawer is None:
            cls.text_size_drawer = ImageDraw.Draw(Image.new("RGB", (100,100)))
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
        But with split word in parts if it os too long to fit on a line (always start on its own line, but the last of its lines 
        can be followed by another word)
        '''
        line_width, line_height = cls.get_text_size(text, font)
        if line_width <= max_width:
            return [text]
        remaining = max_width
        space_width = cls.get_text_size(' ', font)[0]
        lines = []
        for word in text.split(None):
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
                        elif word_width  <= remaining:
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

        font = self.get_font(self.get_font_path(), self.size)
        text = text.replace('\n', ' ')
        if self.wrap:
            lines = self.split_text_in_lines(text, max_width, font)
        else:
            lines = [text]

        # compute all sizes
        lines_with_dim = [(line, ) + self.get_text_size(line, font) for line  in lines]
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
                if self.scroll and not self.wrap:
                    align = 'left'
                    self.scrollable = 'left'
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
                if self.scroll and self.wrap:
                    valign = 'top'
                    self.scrollable = 'top'
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
                    if self.scrollable == 'left':
                        if self.scrolled >= ci['total_width']:
                            self.scrolled = -ci['max_width']
                        crop['left'] += self.scrolled
                        crop['right'] = max(crop['right'] + self.scrolled, ci['total_width'])
                    else:  #  top
                        if self.scrolled >= ci['total_height']:
                            self.scrolled = -ci['max_height']
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

    def start_scroller(self):
        if self.scroll_thread or not self.scrollable:
            return
        self.scrolled = 0
        self.scrolled_at = time() + self.SCROLL_WAIT
        self.scroll_thread = Repeater(self.do_scroll, max(RENDER_IMAGE_DELAY, 1/self.scroll), wait_first=self.SCROLL_WAIT, name=f'TxtScrol{self.page.number}.{self.key.row}{self.key.col}{(".%s" % self.line) if self.line else ""}')
        self.scroll_thread.start()

    def stop_scroller(self, *args, **kwargs):
        if not self.scroll_thread:
            return
        self.scroll_thread.stop()
        self.scroll_thread = None

    def do_scroll(self):
        # we scroll `self.scroll` pixels each second, so we compute the nb of pixels to move since the last move
        time_passed = (now := time()) - self.scrolled_at
        nb_pixels = round(time_passed * self.scroll)
        if not nb_pixels:
            return
        self.scrolled += nb_pixels
        self.scrolled_at = now
        self.key.on_image_changed()

    def version_deactivated(self):
        super().version_deactivated()
        self.stop_scroller()

@dataclass(eq=False)
class Key(Entity):

    is_dir = True
    path_glob = 'KEY_ROW_*_COL_*'
    dir_template = 'KEY_ROW_{row}_COL_{col}'
    main_path_re = re.compile('^(?P<kind>KEY)_ROW_(?P<row>\d+)_COL_(?P<col>\d+)(?:;|$)')
    filename_re_parts = Entity.filename_re_parts + [
        re.compile('^(?P<arg>ref)=(?P<page>.*):(?P<key>.*)$'),  # we'll use current row,col if no key given
    ]
    main_filename_part = lambda args: f'KEY_ROW_{args["row"]}_COL_{args["col"]}'
    filename_parts = [
        Entity.name_filename_part,
        lambda args: f'ref={ref.get("page")}:{ref["key"]}' if (ref := args.get('ref')) else None,
        Entity.disabled_filename_part,
    ]

    parent_attr = 'page'
    identifier_attr = 'key'
    parent_container_attr = 'keys'

    page: 'Page'
    key: Tuple[int, int]

    def __post_init__(self):
        super().__post_init__()
        self.compose_image_cache = None
        self.pressed_at = None
        self.events = versions_dict_factory()
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
    def deck(self):
        return self.page.deck

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
        return f'{self.page}, {self.str}'

    @classmethod
    def convert_main_args(cls, args):
        if (args := super().convert_main_args(args)) is None:
            return None
        args['row'] = int(args['row'])
        args['col'] = int(args['col'])
        return args

    @classmethod
    def parse_filename(cls, name, parent):
        ref_conf, ref, main, args = super().parse_filename(name, parent)
        if main is not None and parent.deck.device:
            if main['row'] < 1 or main['row'] > parent.deck.nb_rows or main['col'] < 1 or main['col'] > parent.deck.nb_cols:
                return None, None, None, None
        return ref_conf, ref, main, args

    def on_create(self):
        super().on_create()
        self.read_directory()
        Manager.add_watch(self.path, self)

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
        Manager.remove_watch(self.path)
        for layer_versions in self.layers.values():
            for layer in layer_versions.all_versions:
                layer.on_delete()
        for text_line_versions in self.text_lines.values():
            for text_line in text_line_versions.all_versions:
                text_line.on_delete()
        for event_versions in self.events.values():
            for event in event_versions.all_versions:
                event.on_delete()
        super().on_delete()

    @classmethod
    def find_reference_page(cls, parent, ref_conf):
        final_ref_conf = ref_conf.copy()
        if ref_page := ref_conf.get('page'):
            if not (page := parent.deck.find_page(ref_page)):
                return final_ref_conf, None
        else:
            final_ref_conf['page'] = page = parent
        return final_ref_conf, page

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, page = cls.find_reference_page(parent, ref_conf)
        if not final_ref_conf.get('key'):
            final_ref_conf['key'] = str(f"{main['row']},{main['col']}")
        if not page:
            return final_ref_conf, None
        return final_ref_conf, page.find_key(final_ref_conf['key'])

    @classmethod
    def iter_waiting_references_for_page(cls, check_page):
        for path, (parent, ref_conf) in check_page.waiting_child_references.get(cls, {}).items():
            yield check_page, path, parent, ref_conf
        for path, (parent, ref_conf) in check_page.deck.waiting_child_references.get(cls, {}).items():
            if (page := check_page.deck.find_page(ref_conf['page'])) and page.number == check_page.number:
                yield page, path, parent, ref_conf

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for page, path, parent, ref_conf
            in self.iter_waiting_references_for_page(self.page)
            if (key := page.find_key(ref_conf['key'])) and key.key == self.key
        ]

    def read_directory(self):
        if self.deck.filters.get('event') != FILTER_DENY:
            for event_file in sorted(self.path.glob(KeyEvent.path_glob)):
                self.on_file_change(event_file.name, f.CREATE | (f.ISDIR if event_file.is_dir() else 0), entity_class=KeyEvent)
        if self.deck.filters.get('layer') != FILTER_DENY:
            for image_file in sorted(self.path.glob(KeyImageLayer.path_glob)):
                self.on_file_change(image_file.name, f.CREATE | (f.ISDIR if image_file.is_dir() else 0), entity_class=KeyImageLayer)
        if self.deck.filters.get('text_line') != FILTER_DENY:
            for text_file in sorted(self.path.glob(KeyTextLine.path_glob)):
                self.on_file_change(text_file.name, f.CREATE | (f.ISDIR if text_file.is_dir() else 0), entity_class=KeyTextLine)

    def on_file_change(self, name, flags, modified_at=None, entity_class=None):
        path = self.path / name
        if (event_filter := self.deck.filters.get('event')) != FILTER_DENY:
            if not entity_class or entity_class is KeyEvent:
                ref_conf, ref, main, args = KeyEvent.parse_filename(name, self)
                if main:
                    if event_filter is not None and not KeyEvent.args_matching_filter(main, args, event_filter):
                        return None
                    return self.on_child_entity_change(path=path, flags=flags, entity_class=KeyEvent, data_identifier=main['kind'], args=args, ref_conf=ref_conf, ref=ref, modified_at=modified_at)
                elif ref_conf:
                    KeyEvent.add_waiting_reference(self, path, ref_conf)
        if (layer_filter := self.deck.filters.get('layer')) != FILTER_DENY:
            if not entity_class or entity_class is KeyImageLayer:
                ref_conf, ref, main, args = KeyImageLayer.parse_filename(name, self)
                if main:
                    if layer_filter is not None and not KeyImageLayer.args_matching_filter(main, args, layer_filter):
                        return None
                    return self.on_child_entity_change(path=path, flags=flags, entity_class=KeyImageLayer, data_identifier=args['layer'], args=args, ref_conf=ref_conf, ref=ref, modified_at=modified_at)
                elif ref_conf:
                    KeyImageLayer.add_waiting_reference(self, path, ref_conf)
        if (text_line_filter := self.deck.filters.get('text_line')) != FILTER_DENY:
            if not entity_class or entity_class is KeyTextLine:
                ref_conf, ref, main, args = KeyTextLine.parse_filename(name, self)
                if main:
                    if text_line_filter is not None and not KeyTextLine.args_matching_filter(main, args, text_line_filter):
                        return None
                    return self.on_child_entity_change(path=path, flags=flags, entity_class=KeyTextLine, data_identifier=args['line'], args=args, ref_conf=ref_conf, ref=ref, modified_at=modified_at)
                elif ref_conf:
                    KeyTextLine.add_waiting_reference(self, path, ref_conf)
    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if (main['row'], main['col']) == tuple(int(val) for val in filter.split(',')):
                return True
        except ValueError:
            pass
        return args.get('name') == filter

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
                        final_image = Image.new("RGB", self.image_size, 'black')
                        for layer in all_layers:
                            try:
                                thumbnail, thumbnail_x, thumbnail_y, mask = layer.compose()
                            except Exception:
                                logger.exception(f'[{layer}] Layer could not be rendered')
                                continue  # we simply ignore a layer that couldn't be created
                            final_image.paste(thumbnail, (thumbnail_x, thumbnail_y), mask)
                        self.compose_image_cache = final_image, PILHelper.to_native_format(self.deck.device, final_image)
            except Exception:
                logger.exception(f'[{self}] Image could not be rendered')
                self.compose_image_cache = None, None

        if overlay_level and (image := self.compose_image_cache[0]):
            image_data = PILHelper.to_native_format(self.deck.device, Image.eval(image, lambda x: x/(1+3*overlay_level)))
        else:
            image_data = self.compose_image_cache[1] if self.compose_image_cache[0] else None

        return image_data

    @property
    def is_visible(self):
        return self.deck.is_key_visible(self.page.number, self.key)

    def has_content(self):
        if any(self.resolved_events.values()):
            return True
        if any(self.resolved_layers.values()) or any(self.resolved_text_lines.values()):
            return True  #self.compose_image() is not None
        return False

    def render(self, overlay_level=None):
        if not self.deck.is_running:
            return
        if overlay_level is None:
            visible, overlay_level = self.deck.get_key_visibility(self)
        else:
            visible = self.has_content()
        if visible:
            self.deck.set_image(self.row, self.col, self.compose_image(overlay_level))
            for text_line in self.resolved_text_lines.values():
                if text_line:
                    text_line.start_scroller()
            if self.page.is_current:
                for event in self.resolved_events.values():
                    if event:
                        event.activate(self.page)
            self.rendered_overlay = overlay_level
        else:
            self.unrender()

    def unrender(self):
        if (overlay_level := self.rendered_overlay) is None:
            return
        for text_line in self.resolved_text_lines.values():
            if text_line:
                text_line.stop_scroller()
        self.deck.remove_image(self.row, self.col)
        for event in self.resolved_events.values():
            if event:
                event.deactivate()
        self.rendered_overlay = None
        # if page is overlay, we render a key that may be below
        below_key, below_overlay_level = self.deck.find_visible_key(self.row, self.col, min_level=overlay_level+1)
        if below_key:
            below_key.render(overlay_level=below_overlay_level)

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
        return KeyImageLayer.find_by_identifier_or_name(self.resolved_layers, layer_filter, int, allow_disabled=allow_disabled)

    def find_text_line(self, text_line_filter, allow_disabled=False):
        return KeyTextLine.find_by_identifier_or_name(self.resolved_text_lines, text_line_filter, int, allow_disabled=allow_disabled)

    def find_event(self, event_filter, allow_disabled=False):
        return KeyEvent.find_by_identifier_or_name(self.resolved_events, event_filter, str, allow_disabled=allow_disabled)

    @property
    def press_duration(self):
        # return value is in milliseconds
        if not self.pressed_at:
            return None
        return (time() - self.pressed_at) * 1000

    def pressed(self):
        events = self.resolved_events
        if longpress_event := events.get('longpress'):
            logger.debug(f'[{self}] PRESSED. WAITING LONGPRESS.')
            longpress_event.wait_run_and_repeat(on_press=True)
        if not (press_event := events.get('press')):
            logger.debug(f'[{self}] PRESSED. IGNORED (event not configured)')
            return
        logger.info(f'[{press_event}] PRESSED.')
        self.pressed_at = time()
        press_event.wait_run_and_repeat(on_press=True)

    def released(self):
        events = self.resolved_events
        duration = self.press_duration or None
        for event_name in ('press', 'longpress'):
            if event := events.get(event_name):
                event.stop_repeater()
                if event.duration_thread:
                    event.stop_duration_waiter()

        str_delay_part = f' (after {duration}ms)' if duration is not None else ''
        if not (release_event := events.get('release')):
            logger.debug(f'[{self}] RELEASED{str_delay_part}. IGNORED (event not configured)')
            return
        if release_event.duration_min and (duration is None or duration < release_event.duration_min):
            logger.info(f'[{release_event}] RELEASED{str_delay_part}. ABORTED (not pressed long enough, less than {release_event.duration_min}ms')
        else:
            logger.info(f'[{release_event}] RELEASED{str_delay_part}.')
            release_event.run()
        self.pressed_at = None


@dataclass(eq=False)
class Page(Entity):

    is_dir = True
    path_glob = 'PAGE_*'
    dir_template = 'PAGE_{page}'
    main_path_re = re.compile('^(?P<kind>PAGE)_(?P<page>\d+)(?:;|$)')
    main_filename_part = lambda args: f'PAGE_{args["page"]}'

    FIRST = '__first__'
    BACK = '__back__'
    PREVIOUS = '__prev__'
    NEXT = '__next__'

    parent_attr = 'deck'
    identifier_attr = 'number'
    parent_container_attr = 'pages'

    deck: 'Deck'
    number: int

    def __post_init__(self):
        super().__post_init__()
        self.keys = versions_dict_factory()

    @property
    def str(self):
        return f'PAGE {self.number} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f'{self.deck}, {self.str}'

    @classmethod
    def convert_main_args(cls, args):
        args = super().convert_main_args(args)
        args['page'] = int(args['page'])
        return args

    def on_create(self):
        super().on_create()
        self.read_directory()
        Manager.add_watch(self.path, self)

    def on_delete(self):
        Manager.remove_watch(self.path)
        for key_versions in self.keys.values():
            for key in key_versions.all_versions:
                key.on_delete()
        super().on_delete()

    def read_directory(self):
        if self.deck.filters.get('key') != FILTER_DENY:
            for key_dir in sorted(self.path.glob(Key.path_glob)):
                self.on_file_change(key_dir.name, f.CREATE | (f.ISDIR if key_dir.is_dir() else 0))

    def on_file_change(self, name, flags, modified_at=None, entity_class=None):
        path = self.path / name
        if (key_filter := self.deck.filters.get('key')) != FILTER_DENY:
            if not entity_class or entity_class is Key:
                ref_conf, ref, main, args = Key.parse_filename(name, self)
                if main:
                    if key_filter is not None and not Key.args_matching_filter(main, args, key_filter):
                        return None
                    return self.on_child_entity_change(path=path, flags=flags, entity_class=Key, data_identifier=(main['row'], main['col']), args=args, ref_conf=ref_conf, ref=ref, modified_at=modified_at)
                elif ref_conf:
                    Key.add_waiting_reference(self, path, ref_conf)

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        try:
            if main['page'] == int(filter):
                return True
        except ValueError:
            pass
        return args.get('name') == filter

    @property
    def is_current(self):
        return self.number == self.deck.current_page_number

    def iter_keys(self):
        for row_col, key in sorted(self.keys.items()):
            if key:
                yield key

    @property
    def is_visible(self):
        return self.deck.is_page_visible(self)

    def render(self, overlay_level=0, pages_below=None, ignore_keys=None):
        if not self.is_visible:
            return
        if ignore_keys is None:
            ignore_keys = set()
        for key in self.iter_keys():
            if key.key in ignore_keys:
                continue
            if not key.has_content():
                continue
            key.render(overlay_level)
            ignore_keys.add(key.key)
        if pages_below:
            page_number, pages_below = pages_below[0], pages_below[1:]
            if (page := self.deck.pages.get(page_number)):
                page.render(overlay_level+1, pages_below, ignore_keys)


    def unrender(self):
        if not self.is_visible:
            return
        for key in self.iter_keys():
            key.unrender()

    @property
    def sorted_keys(self):
        keys = {}
        for row_col, key in sorted((key.key, key) for key in self.iter_keys()):
            keys[row_col] = key
        return keys

    def find_key(self, key_filter, allow_disabled=False):
        return Key.find_by_identifier_or_name(self.keys, key_filter, lambda filter: tuple(int(val) for val in filter.split(',')), allow_disabled=allow_disabled)

    def version_activated(self):
        super().version_activated()
        if self.disabled:
            return
        self.render()

    def version_deactivated(self):
        super().version_deactivated()
        if self.disabled:
            return
        self.unrender()
        self.deck.go_to_page(Page.BACK, None)


@dataclass(eq=False)
class Deck(Entity):
    is_dir = True

    device: StreamDeck
    scroll_activated : bool

    def __post_init__(self):
        super().__post_init__()
        self.serial = self.device.info['serial'] if self.device else None
        self.nb_cols = self.device.info['cols'] if self.device else None
        self.nb_rows = self.device.info['rows'] if self.device else None
        self.key_width = self.device.info['key_width'] if self.device else None
        self.key_height = self.device.info['key_height'] if self.device else None
        self.brightness = DEFAULT_BRIGHTNESS
        self.pages = versions_dict_factory()
        self.current_page_number = None
        self.current_page_is_transparent = False
        self.waiting_images = {}
        self.render_images_thread = None
        self.render_images_queue = None
        self.filters = {}
        self.page_history = []
        self.visible_pages = []
        self.pressed_key = None
        self.is_running = False

    @property
    def str(self):
        return f'DECK {self.serial or self.name}{", disabled" if self.disabled else ""}'

    def __str__(self):
        return self.str

    @property
    def deck(self):
        return self

    def key_to_index(self, row, col=None):
        if col is None:  # when key as (row, col) is passed instead of *key
            row, col = row
        return (row - 1) * self.nb_cols + (col - 1)

    def index_to_key(self, index):
        return index // self.nb_cols + 1, index % self.nb_cols + 1

    def on_create(self):
        self.read_directory()
        Manager.add_watch(self.path, self)

    def run(self):
        self.is_running = True
        self.device.set_key_callback(self.on_key_pressed)
        self.go_to_page(Page.FIRST, False)

    def read_directory(self):
        if self.filters.get('page') != FILTER_DENY:
            for page_dir in sorted(self.path.glob(Page.path_glob)):
                self.on_file_change(page_dir.name, f.CREATE | (f.ISDIR if page_dir.is_dir() else 0))

    def on_file_change(self, name, flags, modified_at=None, entity_class=None):
        path = self.path / name
        if (page_filter := self.filters.get('page')) != FILTER_DENY:
            if not entity_class or entity_class is Page:
                ref_conf, ref, main, args = Page.parse_filename(name, self)
                if main:
                    if page_filter is not None and not Page.args_matching_filter(main, args, page_filter):
                        return None
                    return self.on_child_entity_change(path=path, flags=flags, entity_class=Page, data_identifier=main['page'], args=args, ref_conf=ref_conf, ref=ref, modified_at=modified_at)

    def update_visible_pages_stack(self):
        # first page in `visible_pages` is the current one, then the one below, etc... 
        # (the last one is the first one not being an overlay, so if the first one is not an overlay, it's the only 
        # one in the stack)
        stack = []
        for page_number, transparent in reversed(self.page_history):
            stack.append(page_number)
            if not transparent:
                break
        self.visible_pages = stack

    def append_to_history(self, page, transparent=False):
        transparent = bool(transparent)
        page_key = (page.number, transparent)
        if not self.page_history or self.page_history[-1] != page_key:
            self.page_history.append(page_key)
        self.current_page_number = page.number
        self.current_page_is_transparent = transparent
        self.update_visible_pages_stack()

    def pop_from_history(self):
        page = None
        transparent = None
        while True:
            if not self.page_history:
                break
            page_num, transparent = self.page_history.pop()
            if (page_num, transparent) == (self.current_page_number, self.current_page_is_transparent):
                continue
            if (page := self.pages.get(page_num)):
                break
        return page, transparent

    def go_to_page(self, page_ref, transparent):
        transparent = bool(transparent)

        logger.debug(f'[{self}] Asking to go to page {page_ref} (current={self.current_page_number})')

        if page_ref is None:
            return

        current = (self.current_page_number, self.current_page_is_transparent)

        if isinstance(page_ref, int):
            if (page_ref, transparent) == current:
                return
            if not (page := self.pages.get(page_ref)):
                return

        elif page_ref == Page.FIRST:
            if not (possible_pages := sorted([(number, page) for number, page in self.pages.items() if page])):
                return
            page = possible_pages[0][1]

        elif page_ref == Page.BACK:
            page, transparent = self.pop_from_history()
            if not page:
                self.update_visible_pages_stack()  # because we may have updated the history
                return

        elif page_ref == Page.PREVIOUS:
                if not self.current_page_number:
                    return
                if not (page := self.pages.get(self.current_page_number - 1)):
                    return

        elif page_ref == Page.NEXT:
                if not self.current_page_number:
                    return
                if not (page := self.pages.get(self.current_page_number + 1)):
                    return

        elif not (page := self.find_page(page_ref, allow_disabled=False)):
            return

        if (page.number, transparent) == current:
            return

        if (current_page := self.current_page):
            if page_ref == Page.BACK:
                if current[1]:
                    logger.info(f'[{self}] Closing overlay for page [{current_page.str}], going back to {"overlay " if transparent else ""}[{page.str}]')
                else:
                    logger.info(f'[{self}] Going back to [{page.str}] from [{current_page.str}]')
            elif transparent:
                logger.info(f'[{self}] Adding [{page.str}] as an overlay over [{current_page.str}]')
            else:
                logger.info(f'[{self}] Changing current page from [{current_page.str}] to [{page.str}]')
            if not transparent or page_ref == Page.BACK:
                current_page.unrender()
        else:
            logger.info(f'[{self}] Setting current page to [{page.str}]')

        self.append_to_history(page, transparent)
        page.render(0, self.visible_pages[1:])

    def is_page_visible(self, page):
        number = page.number
        return self.current_page_number == number or number in self.visible_pages

    def get_key_visibility(self, key):
        if not key.page.is_visible:
            return False, None

        visible = False
        key_level = None

        key_page_number = key.page.number
        key_row_col = key.key

        for level, page_number in enumerate(self.visible_pages):
            if page_number == key_page_number:
                page_key = key
            else:
                if not (page := self.pages.get(page_number)):
                    continue
                if not (page_key := page.keys.get(key_row_col)):
                    continue
            visible = page_key.has_content()
            if visible or page_number == key_page_number:
                key_level = level
                break

        return visible, key_level

    def find_visible_key(self, row, col, min_level=0):
        for level, page_number in enumerate(self.visible_pages):
            if level < min_level:
                continue
            if not (page := self.pages.get(page_number)):
                continue
            if not (key := page.keys.get((row, col))):
                continue
            if key.has_content():
                return key, level
        return None, None


    @property
    def current_page(self):
        return self.pages.get(self.current_page_number) or None

    def on_key_pressed(self, deck, index, pressed):
        row, col = row_col = self.index_to_key(index)

        if pressed:
            if self.pressed_key:
                logger.warning('Multiple press is not supported yet. Press ignored.')
                return

            if not (page := self.current_page):
                logger.debug(f'[{self}, KEY ({row}, {col})] PRESSED. IGNORED (no current page)')
                return

            if not (key := page.keys[row_col]):
                logger.debug(f'[{page}, KEY ({row}, {col})] PRESSED. IGNORED (key not configured)')
                return

            self.pressed_key = key
            key.pressed()

        else:
            if not self.pressed_key or self.pressed_key.key != row_col:
                return
            pressed_key, self.pressed_key = self.pressed_key, None
            pressed_key.released()

    def set_brightness(self, operation, level):
        old_brightness = self.brightness
        if operation == '=':
            self.brightness = level
        elif operation == '+':
            self.brightness = min(100, old_brightness + level)
        elif operation == '-':
            self.brightness = max(0, old_brightness - level)
        if self.brightness == old_brightness:
            return
        logger.info(f"[{self}] Changing brightness from {old_brightness} to {self.brightness}")
        self.device.set_brightness(self.brightness)

    def render(self):
        if not (page := self.current_page):
            return
        page.render()

    def unrender(self):
        for page_number in self.visible_pages:
            if (page := self.pages.get(page_number)):
                page.unrender()
        if self.render_images_thread is not None:
            self.render_images_queue.put(None)
            self.render_images_thread.join()
            self.render_images_thread = self.render_images_queue = None

    def set_image(self, row, col, image):
        if self.render_images_thread is None:
            self.render_images_queue = SimpleQueue()
            self.render_images_thread = threading.Thread(target=render_deck_images, args=(self.device, self.render_images_queue))
            self.render_images_thread.start()

        self.render_images_queue.put((self.key_to_index(row, col), image))

    def remove_image(self, row, col):
        self.set_image(row, col, None)

    def find_page(self, page_filter, allow_disabled=False):
        return Page.find_by_identifier_or_name(self.pages, page_filter, int, allow_disabled=allow_disabled)


RENDER_IMAGE_DELAY = 0.02
def render_deck_images(deck, queue):
    set_thread_name('ImgRenderer')
    delay = RENDER_IMAGE_DELAY
    future_margin = RENDER_IMAGE_DELAY / 10
    timeout = None
    images = {}

    def get_ordered():
        return sorted((ts, index) for index, (ts, image) in images.items())

    def extract_ready(ordered):
        if not ordered:
            return []
        limit = time() + future_margin
        return [(index, images.pop(index)) for ts, index in ordered if ts < limit]

    def render(force_all=False):
        if force_all:
            ready = list(images.items())
            images.clear()
            ordered = []
        else:
            ordered = get_ordered()
            ready = extract_ready(ordered)

        if ready:
            with deck:
                for index, (ts, image) in ready:
                    deck.set_key_image(index, image)
            ordered = get_ordered()

        return ordered[0] if ordered else (None, None)

    next_ts, next_index = None, None
    while True:
        if next_ts is None or next_ts <= time():
            while True:
                next_ts, next_index = render()
                if next_ts is None or next_ts > time():
                    break
        timeout = max(0, next_ts - time()) if next_ts else None
        try:
            work = queue.get(timeout=timeout)
        except Empty:
            # timeout expired because we waited a certain timeout to render the next waiting image
            continue
        else:
            if work is None:
                # we were asked to exit, so we render waiting ones then we exit
                render(force_all=True)
                break
            # we have some work: we received a new image to queue
            index, image = work
            images[index] = (time() + delay, image)
            if index == next_index:
                next_ts = next_index = None


class Manager:
    decks = {}
    manager = None
    files_watcher = None
    files_watcher_thread = None
    processes = {}
    processes_checker_thread = None

    @classmethod
    def get_manager(cls):
        if not cls.manager:
            cls.manager = DeviceManager()
        return cls.manager

    @classmethod
    def get_decks(cls):
        if not cls.decks:
            for deck in cls.get_manager().enumerate():
                try:
                    deck.open()
                except Exception:
                    return cls.exit(1, f'Stream Deck "{deck.deck_type()}" (ID {deck.id()}) cannot be accessed. Maybe a program is already connected to it.')
                deck.reset()
                deck.set_brightness(DEFAULT_BRIGHTNESS)
                serial = deck.get_serial_number()
                cls.decks[serial] = deck
                deck.info = {
                    'serial': serial,
                    'id': deck.id(),
                    'type': deck.deck_type(),
                    'firmware': deck.get_firmware_version(),
                    'nb_keys': deck.key_count(),
                    'rows': (layout := deck.key_layout())[0],
                    'cols': layout[1],
                    'format': (image_format := deck.key_image_format())['format'],
                    'key_width': image_format['size'][0],
                    'key_height': image_format['size'][1],
                }
                deck.reset()  # see https://github.com/abcminiuser/python-elgato-streamdeck/issues/38
        if not len(cls.decks):
            Manager.exit(1, 'No Stream Deck detected. Aborting.')
        return cls.decks

    @classmethod
    def get_deck(cls, serial):
        if len(serial) > 1:
            return cls.exit(1, f'Invalid serial "{" ".join(serial)}".')
        serial = serial[0] if serial else None
        decks = cls.get_decks()
        if not serial:
            if len(decks) > 1:
                return cls.exit(1, f'{len(decks)} Stream Decks detected, you need to specify the serial. Use the "inspect" command to list all available decks.')
            return list(decks.values())[0]
        if serial not in decks:
            return cls.exit(1, f'No Stream Deck found with the serial "{serial}". Use the "inspect" command to list all available decks.')
        return decks[serial]

    @classmethod
    def add_watch(cls, directory, owner):
        if not cls.files_watcher:
            return
        cls.files_watcher.add_watch(directory, owner)

    @classmethod
    def remove_watch(cls, directory):
        if not cls.files_watcher:
            return
        cls.files_watcher.remove_watch(directory)

    @classmethod
    def start_files_watcher(cls):
        if cls.files_watcher:
            return
        cls.files_watcher = Inotifier()
        cls.files_watcher_thread = threading.Thread(target=cls.files_watcher.run)
        cls.files_watcher_thread.start()

    @classmethod
    def end_files_watcher(cls):
        if not cls.files_watcher:
            return
        cls.files_watcher.stop()
        cls.files_watcher_thread.join()
        cls.files_watcher = cls.files_watcher_thread = None

    @classmethod
    def exit(cls, status=0, msg=None, msg_level=None, log_exception=False):
        if msg is not None:
            if msg_level is None:
                msg_level = 'info' if status == 0 else 'critical'
            getattr(logger, msg_level)(msg, exc_info=log_exception)

        cls.end_files_watcher()
        cls.end_processes_checker()

        if cls.decks:
            for serial, deck in list(cls.decks.items()):
                try:
                    deck.reset()
                    deck.close()
                except Exception:
                    pass
                cls.decks.pop(serial)

            sleep(0.01) # needed to avoid error!!

        exit(status)

    @staticmethod
    def validate_brightness_level(ctx, param, value):
        if 0 <= value <= 100:
            return value
        raise click.BadParameter('Should be between 0 and 100 (inclusive)')

    @staticmethod
    def validate_positive_integer(ctx, param, value):
        if value <= 0:
            raise click.BadParameter("Should be a positive integer")
        return value

    @staticmethod
    def normalize_deck_directory(directory, serial):
        if not isinstance(directory, Path):
            directory = Path(directory)
        if serial and directory.name != serial:
            directory /= serial
        return directory

    @classmethod
    def check_running_processes(cls):
        for pid, process_info in list(cls.processes.items()):
            if (return_code := process_info['process'].poll()) is not None:
                logger.info(f'[PROCESS] `{process_info["program"]}`{" (launched in detached mode)" if process_info["detached"] else ""} ended [PID={pid}; ReturnCode={return_code}]')
                cls.processes.pop(pid, None)
                if (event := process_info.get('done_event')):
                    event.set()

    @classmethod
    def start_processes_checker(cls):
        if  cls.processes_checker_thread:
            return
        cls.processes_checker_thread = Repeater(cls.check_running_processes, 0.1, name='ProcessChecker')
        cls.processes_checker_thread.start()

    @classmethod
    def end_processes_checker(cls):
        if not cls.processes_checker_thread:
            return
        cls.processes_checker_thread.stop()
        cls.processes_checker_thread.join()
        cls.processes_checker_thread = None

    @classmethod
    def start_process(cls, program, register_stop=False, detach=False, shell=False, done_event=None):
        if done_event is not None:
            done_event.clear()
        if not cls.processes_checker_thread:
            cls.start_processes_checker()

        base_str = f'[PROCESS] Launching `{program}`{" (in detached mode)" if detach else ""}'
        logger.info(f'{base_str}...')
        try:
            process = psutil.Popen(program, start_new_session=bool(detach), shell=bool(shell))
            cls.processes[process.pid] = {
                'pid': process.pid,
                'program': program,
                'process' : process,
                'to_stop': bool(register_stop),
                'detached': detach,
                'done_event': done_event,
            }
            logger.info(f'{base_str} [ok PID={process.pid}]')
            return None if detach else process.pid 
        except Exception:
            logger.exception(f'{base_str} [failed]')
            return None

    @classmethod
    def kill_proc_tree(cls, pid, sig=signal.SIGTERM, include_parent=True,
                       timeout=None, on_terminate=None):
        """Kill a process tree (including grandchildren) with signal
        "sig" and return a (gone, still_alive) tuple.
        "on_terminate", if specified, is a callback function which is
        called as soon as a child terminates.
        https://psutil.readthedocs.io/en/latest/index.html#kill-process-tree
        """
        assert pid != os.getpid(), "won't kill myself"
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            return (), ()
        if include_parent:
            children.append(parent)
        for p in children:
            try:
                p.send_signal(sig)
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(children, timeout=timeout,
                                        callback=on_terminate)
        return (gone, alive)

    @classmethod
    def terminate_process(cls, pid):
        if not (process_info := cls.processes.pop(pid, None)):
            return
        if not psutil.pid_exists(pid):
            return
        base_str = f"[PROCESS {pid}] Terminating `{process_info['program']}`"
        logger.info(f'{base_str}...')
        gone, alive = cls.kill_proc_tree(pid, timeout=5)
        if alive:
            # TODO: handle the remaining processes
            logger.error(f'{base_str} [FAIL: still running: {" ".join([p.pid for p in alive])} ]')
        else:
            logger.info(f'{base_str} [done]')


class NaturalOrderGroup(click.Group):
    def list_commands(self, ctx):
        return self.commands.keys()


@click.group(cls=NaturalOrderGroup)
def cli():
    pass


common_options = {
    'optional_deck': click.argument('deck', nargs=-1, required=False),
    'verbosity': click_log.simple_verbosity_option(logger, help='Either CRITICAL, ERROR, WARNING, INFO or DEBUG', show_default=True),
}

@cli.command()
@common_options['verbosity']
def inspect():
    """
Get information about all connected Stream Decks.
    """
    decks = Manager.get_decks()

    click.echo(f"Found {len(decks)} Stream Deck(s):")

    for deck in decks.values():
        info = deck.info
        click.echo(f"* Deck {info['serial']}")
        click.echo(f"\t - Type: {info['type']}")
        click.echo(f"\t - ID: {info['id']}")
        click.echo(f"\t - Serial: {info['serial']}")
        click.echo(f"\t - Firmware Version: '{info['firmware']}'")
        click.echo(f"\t - Key Count: {info['nb_keys']} (in a {info['rows']}x{info['cols']} grid)")
        click.echo(f"\t - Key Images: {info['key_width']}x{info['key_height']} pixels, {info['format']} format")

        deck.close()

@cli.command()
@common_options['optional_deck']
@click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True))
@click.option('-p', '--pages', type=int, default=1, callback=Manager.validate_positive_integer, help="Number of pages to generate. Default to 1.")
@click.option('-y', '--yes', is_flag=True, help='Automatically answer yes to confirmation demand')
@common_options['verbosity']
def make_dirs(deck, directory, pages, yes):
    """Create keys directories for a Stream Deck.

    Arguments:

    DECK: Serial number of the Stream Deck to handle. Optional if only one Stream Deck.\n
    DIRECTORY: Path of the directory where to create pages and keys directories. If it does not ends with a subdirectory matching the SERIAL, it will be added.
    """
    deck = Manager.get_deck(deck)
    serial = deck.info['serial']
    directory = Manager.normalize_deck_directory(directory, serial)
    if directory.exists() and not directory.is_dir():
        return Manager.exit(1, f'"{directory}" exists but is not a directory.')

    if not yes:
        if not click.confirm(f'Create directories for Stream Deck "{serial}" in directory "{directory}" ({pages} page(s))?', default=True):
            click.echo('Aborting.')
            return

    def create_dir(directory, desc, relative_to='', print_prefix=''):
        directory_repr = directory.relative_to(relative_to) if relative_to else directory
        click.echo(f"{print_prefix}{directory_repr}   ({desc})... ", nl=False)
        if directory.exists():
            click.echo("Already exists.")
            return False
        try:
            pass
            directory.mkdir(parents=True)
        except Exception:
            return Manager.exit(1, f'"{directory}" could not be created', log_exception=True)
        click.echo("Created.")
        return True

    create_dir(directory, f'Main directory for Stream Deck "{serial}"')
    click.echo('Subdirectories:')

    for page in range(1, pages+1):
        page_dir = directory / Page.Page.dir_template.format(page=page)
        create_dir(page_dir, f'Directory for content of page {page}', directory, "\t")
        for row in range(1, deck.info['rows']+1):
            for col in range(1, deck.info['cols']+1):
                key_dir = page_dir / Key.dir_template.format(row=row, col=col)
                create_dir(key_dir, f'Directory for key {col} on row {row} on page {page}', page_dir, "\t\t")


@cli.command()
@common_options['optional_deck']
@click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=True))
@click.option('--scroll/--no-scroll', default=True, help='If scroll in keys is activated. Default to true.')
@common_options['verbosity']
def run(deck, directory, scroll):
    """Run, Forrest, Run!"""

    device = Manager.get_deck(deck)
    serial = device.info['serial']
    directory = Manager.normalize_deck_directory(directory, serial)
    if not directory.exists() or not directory.is_dir():
        return Manager.exit(1, f"{directory} does not exist or is not a directory")
    logger.info(f'[DECK {serial}] Running in directory "{directory}"')

    Manager.start_files_watcher()

    deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=serial, disabled=False, device=device, scroll_activated=scroll)
    deck.on_create()
    deck.run()

    ended = False
    def end(signum, frame):
        nonlocal ended
        logger.info(f'Ending ({signal.strsignal(signum)})...')
        ended = True
        signal.signal(signal.SIGTERM, sigterm_handler)
        signal.signal(signal.SIGINT, sigint_handler)

    sigterm_handler = signal.getsignal(signal.SIGTERM)
    sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)

    while not ended:
        sleep(1)

    Manager.end_files_watcher()
    Manager.end_processes_checker()

    deck.unrender()

    deck.device.reset()
    deck.device.close()

    main_thread = threading.currentThread()
    for t in threading.enumerate():
        if t is not main_thread and t.is_alive():
            t.join()

class FilterCommands:
    options = {
        'directory':  click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=True)),
        'page':  click.option('-p', '--page', 'page_filter', type=str, required=True, help='A page number or a name'),
        'key':  click.option('-k', '--key', 'key_filter', type=str, required=True, help='A key as `(row,col)` or a name'),
        'layer':  click.option('-l', '--layer', 'layer_filter', type=str, required=False, help='A layer number (do not pass it to use the default image)'),  # if not given we'll use ``-1``
        'text_line':  click.option('-l', '--line', 'text_line_filter', type=str, required=False, help='A text line (do not pass it to use the default text)'),  # if not given we'll use ``-1``
        'event':  click.option('-e', '--event', 'event_filter', type=str, required=True, help='An event name (press/longpress/release/start)'),
        'names':  click.option('-c', '--conf', 'names', type=str, multiple=True, required=False, help='Names to get the values from the configuration "---conf name1 --conf name2..."'),
        'names_and_values':  click.option('-c', '--conf', 'names_and_values', type=(str, str), multiple=True, required=True, help='Pair of names and values to set for the configuration "---conf name1 value1 --conf name2 value2..."'),
        'verbosity':  click_log.simple_verbosity_option(logger, default='WARNING', help='Either CRITICAL, ERROR, WARNING, INFO or DEBUG', show_default=True),
    }

    @classmethod
    def combine_options(cls):
        options = {}
        options['page_filter'] = lambda func: cls.options['directory'](cls.options['page'](func))
        options['key_filter'] = lambda func: options['page_filter'](cls.options['key'](func))
        options['layer_filter'] = lambda func: options['key_filter'](cls.options['layer'](func))
        options['text_line_filter'] = lambda func: options['key_filter'](cls.options['text_line'](func))
        options['event_filter'] = lambda func: options['key_filter'](cls.options['event'](func))

        def add_verbosity(option):
            return lambda func: option(cls.options['verbosity'](func))

        def set_command(name, option):
            cls.options[name] = add_verbosity(option)
            arg_key = f'{name}_arg'
            options[arg_key] = lambda func: option(cls.options['name'](func))
            cls.options[arg_key] = add_verbosity(options[arg_key])

            key = f'{name}_with_names'
            options[key] = lambda func: option(cls.options['names'](func))
            cls.options[key] = add_verbosity(options[key])

            key = f'{name}_with_names_and_values'
            options[key] = lambda func: option(cls.options['names_and_values'](func))
            cls.options[key] = add_verbosity(options[key])

        for name, option in list(options.items()):
            set_command(name, option)

    @staticmethod
    def get_deck(directory, page_filter=None, key_filter=None, event_filter=None, layer_filter=None, text_line_filter=None):
        directory = Manager.normalize_deck_directory(directory, None)
        deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=directory.name, disabled=False, device=None, scroll_activated=False)
        if page_filter is not None:
            deck.filters['page'] = page_filter
        if key_filter is not None:
            deck.filters['key'] = key_filter
        if event_filter is not None:
            deck.filters['event'] = event_filter
        if layer_filter is not None:
            deck.filters['layer'] = layer_filter
        if text_line_filter is not None:
            deck.filters['text_line'] = text_line_filter
        deck.on_create()
        return deck

    @staticmethod
    def find_page(deck, page_filter):
        if not (page := deck.find_page(page_filter, allow_disabled=True)):
            Manager.exit(1, f'[{deck}] Page `{page_filter}` not found')
        return page

    @staticmethod
    def find_key(page, key_filter):
        if not (key := page.find_key(key_filter, allow_disabled=True)):
            Manager.exit(1, f'[{page}] Key `{key_filter}` not found')
        return key

    def find_layer(key, layer_filter):
        if not (layer := key.find_layer(layer_filter or -1, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Layer `{layer_filter}` not found')
        return layer

    def find_text_line(key, text_line_filter):
        if not (text_line := key.find_text_line(text_line_filter or -1, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Text line `{text_line_filter}` not found')
        return text_line

    @staticmethod
    def find_event(key, event_filter):
        if not (event := key.find_event(event_filter, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Event `{event_filter}` not found')
        return event

    @classmethod
    def get_args(cls, obj, resolve=True):
        return obj.get_resovled_raw_args() if resolve else obj.get_raw_args()

    @classmethod
    def get_args_as_json(cls, obj, only_names=None):
        main, args = cls.get_args(obj)
        data = main | args
        if only_names is not None:
            data = {k: v for k, v in data.items() if k in only_names}
        return json.dumps(data)

    @staticmethod
    def compose_filename(obj, main, args):
        return obj.compose_filename(main, args)

    @classmethod
    def get_update_args_filename(cls, obj, names_and_values):
        main, args = cls.get_args(obj, resolve=False)
        final_args = deepcopy(args)
        for (name, value) in names_and_values:
            if ';' in name:
                Manager.exit(1, f'[{obj}] Configuration name `{name}` is not valid')
            if ';' in value:
                Manager.exit(1, f'[{obj}] Configuration value for `{name}` is not valid')
            if name in main:
                Manager.exit(1, f'[{obj}] Configuration name `{name}` cannot be changed')
            if not value:
                final_args.pop(name, None)
            else:
                try:
                    final_args[name] = obj.raw_parse_filename(cls.compose_filename(obj, main, {}) + f';{name}={value}', obj.path.parent)[1][name]
                except KeyError:
                    Manager.exit(1, f'[{obj}] Configuration `{name} {value}` is not valid')

        return cls.compose_filename(obj, main, final_args)


FC = FilterCommands
FC.combine_options()


@cli.command()
@FC.options['page_filter']
def get_page_path(directory, page_filter):
    """Get the path of a page."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(page.path)

@cli.command()
@FC.options['page_filter_with_names']
def get_page_conf(directory, page_filter, names):
    """Get the configuration of a page, in json."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.get_args_as_json(page, names or None))

@cli.command()
@FC.options['page_filter_with_names_and_values']
def set_page_conf(directory, page_filter, names_and_values):
    """Set the value of some entries of a page configuration."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    page.rename(FC.get_update_args_filename(page, names_and_values))

@cli.command()
@FC.options['key_filter']
def get_key_path(directory, page_filter, key_filter):
    """Get the path of a key."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(key.path)

@cli.command()
@FC.options['key_filter_with_names']
def get_key_conf(directory, page_filter, key_filter, names):
    """Get the configuration of a key, in json."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.get_args_as_json(key, names or None))

@cli.command()
@FC.options['key_filter_with_names_and_values']
def set_key_conf(directory, page_filter, key_filter, names_and_values):
    """Set the value of some entries of a key configuration."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    key.rename(FC.get_update_args_filename(key, names_and_values))

@cli.command()
@FC.options['layer_filter']
def get_image_path(directory, page_filter, key_filter, layer_filter):
    """Get the path of an image/layer."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(layer.path)

@cli.command()
@FC.options['layer_filter_with_names']
def get_image_conf(directory, page_filter, key_filter, layer_filter, names):
    """Get the configuration of an image/layer, in json."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(FC.get_args_as_json(layer, names or None))

@cli.command()
@FC.options['layer_filter_with_names_and_values']
def set_image_conf(directory, page_filter, key_filter, layer_filter, names_and_values):
    """Set the value of some entries of an image configuration."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    layer.rename(FC.get_update_args_filename(layer, names_and_values))

@cli.command()
@FC.options['text_line_filter']
def get_text_path(directory, page_filter, key_filter, text_line_filter):
    """Get the path of an image/layer."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(text_line.path)

@cli.command()
@FC.options['text_line_filter_with_names']
def get_text_conf(directory, page_filter, key_filter, text_line_filter, names):
    """Get the configuration of an image/layer, in json."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(FC.get_args_as_json(text_line, names or None))

@cli.command()
@FC.options['text_line_filter_with_names_and_values']
def set_text_conf(directory, page_filter, key_filter, text_line_filter, names_and_values):
    """Set the value of some entries of an image configuration."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    text_line.rename(FC.get_update_args_filename(text_line, names_and_values))

@cli.command()
@FC.options['event_filter']
def get_event_path(directory, page_filter, key_filter, event_filter):
    """Get the path of an event."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    print(event.path)

@cli.command()
@FC.options['event_filter_with_names']
def get_event_conf(directory, page_filter, key_filter, event_filter, names):
    """Get the configuration of an event, in json."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    print(FC.get_args_as_json(event, names or None))

@cli.command()
@FC.options['event_filter_with_names_and_values']
def set_event_conf(directory, page_filter, key_filter, event_filter, names_and_values):
    """Set the value of some entries of an event configuration."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    event.rename(FC.get_update_args_filename(event, names_and_values))


@cli.command()
@common_options['optional_deck']
@click.argument('level', type=int, callback=Manager.validate_brightness_level)
@common_options['verbosity']
def brightness(deck, level):
    """Set the brightness level of a Stream Deck.

    Arguments:

    LEVEL: Brightness level, from 0 (no light) to 100 (brightest)
    """
    deck = Manager.get_deck(deck)
    deck.set_brightness(level)


if __name__ == '__main__':
    try:
        start = time()
        cli()
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code == 0:
            Manager.exit(0, 'Bye.')
        else:
            Manager.exit(exc.code if isinstance(exc, SystemExit) else 1, 'Oops...', log_exception=True)
    else:
        Manager.exit(0, 'Bye.')
