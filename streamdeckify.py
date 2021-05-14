#!/usr/bin/env python3

"""
STREAMDECKIFY

A software to handle a Stream Deck from Elegato, via directories and files.

The concept is simple: with a basic directories structure, put files (or better, symbolic links) 
with specific names to 

Features:
- pages
- multi-layers key images with configuration in file name (margin, crop, color, opacity)
- action on press/release
- easily update from external script

To come:
- repeatable action (every xxx milliseconds until key released)

Example structure:

/home/twidi/streamdeck-data/CL28J1A04316
├── PAGE_1;name=main
│   ├── KEY_ROW_1_COL_1;name=spotify
│   │   ├── IMAGE;layer=0;name=background -> /home/twidi/dev/streamdeck-scripts/spotify/assets/background.png
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/spotify/assets/logo.png
│   │   ├── IMAGE;layer=2;name=pause;colorize=green;margin=20,20,20,20;opacity=40 -> /home/twidi/dev/streamdeck-scripts/spotify/assets/pause.png
│   │   ├── IMAGE;layer=3;name=progress;colorize=white;margin=92%,100%,-1,-100%;crop=0,43%,100%,50% -> /home/twidi/dev/streamdeck-scripts/spotify/assets/progress-bar.png
│   │   ├── IMAGE;layer=9;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
│   │   └── ON_START -> /home/twidi/dev/streamdeck-scripts/spotify/listen-changes.py
│   │   ├── ON_PRESS -> /home/twidi/dev/streamdeck-scripts/spotify/play-pause.py
│   ├── KEY_ROW_2_COL_1;name=volume-up
│   │   ├── IMAGE;layer=0;name=background -> /home/twidi/dev/streamdeck-scripts/volume/assets/background.png
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/volume/assets/icon-increase.png
│   │   ├── IMAGE;layer=9;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
│   │   └── ON_PRESS -> /home/twidi/dev/streamdeck-scripts/volume/increase.sh
│   ├── KEY_ROW_2_COL_8;name=deck-brightness-up
│   │   ├── IMAGE;layer=0;name=background -> /home/twidi/dev/streamdeck-scripts/deck/assets/background-brightness.png
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/deck/assets/icon-brightness-increase.png
│   │   ├── IMAGE;layer=9;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
│   │   └── ON_PRESS;action=brightness;level=+10
│   ├── KEY_ROW_3_COL_1;name=volume-down
│   │   ├── IMAGE;layer=0;name=background -> /home/twidi/dev/streamdeck-scripts/volume/assets/background.png
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,25,15,15 -> /home/twidi/dev/streamdeck-scripts/volume/assets/icon-decrease.png
│   │   ├── IMAGE;layer=9;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
│   │   └── ON_PRESS -> /home/twidi/dev/streamdeck-scripts/volume/decrease.sh
│   ├── KEY_ROW_3_COL_8;name=deck-brightness-down
│   │   ├── IMAGE;layer=0;name=background -> /home/twidi/dev/streamdeck-scripts/deck/assets/background-brightness.png
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,20,15,20 -> /home/twidi/dev/streamdeck-scripts/deck/assets/icon-brightness-decrease.png
│   │   ├── IMAGE;layer=9;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
│   │   └── ON_PRESS;action=brightness;level=-10
│   ├── KEY_ROW_4_COL_1;name=volume-mute
│   │   ├── IMAGE;layer=0;name=background -> /home/twidi/dev/streamdeck-scripts/volume/assets/background.png
│   │   ├── IMAGE;layer=1;name=icon;colorize=white;margin=15,15,15,15 -> /home/twidi/dev/streamdeck-scripts/volume/assets/icon-mute.png
│   │   ├── IMAGE;layer=2;name=volume;colorize=white;margin=92%,75%,-1,-75%;crop=0,43%,100%,50% -> /home/twidi/dev/streamdeck-scripts/volume/assets/volume-bar.png
│   │   ├── IMAGE;layer=9;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
│   │   └── ON_START -> /home/twidi/dev/streamdeck-scripts/volume/listen-changes.sh
│   │   ├── ON_PRESS -> /home/twidi/dev/streamdeck-scripts/volume/toggle_mute.sh
│   └── KEY_ROW_4_COL_8;name=deck-page-next
│       ├── IMAGE;colorize=#333333 -> /home/twidi/dev/streamdeck-scripts/deck/assets/right-arrow.png
│       └── ON_PRESS;action=page;page=__next__

"""

import logging
import json
import os
import re
import signal
import threading
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from glob import glob
from math import inf
from operator import itemgetter
from pathlib import Path
from queue import SimpleQueue, Empty
from time import time, sleep
from typing import List, Dict, Tuple, Union

import click
import click_log
import psutil
from inotify_simple import INotify, flags as f
from peak.util.proxies import ObjectWrapper  # pip install ProxyTypes
from PIL import Image, ImageEnhance
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper


DEFAULT_BRIGHTNESS = 30
RE_PART_0_100 = '0*(?:\d{1,2}?|100)'
RE_PART_PERCENT = f'{RE_PART_0_100}%'
RE_PART_PERCENT_OR_NUMBER = f'(?:\d+|{RE_PART_PERCENT})'
RE_PART_COLOR = '\w+|(?:#[a-fA-F0-9]{6})'

logger = logging.getLogger(__name__)
click_log.basic_config(logger)


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
        return self.versions[key]

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

    def terminate(self):
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
        self.running = True
        while True:
            if not self.running or self.inotify.closed:
                break
            try:
                for event in self.inotify.read(timeout=500):
                    if not self.running or self.inotify.closed:
                        break
                    watcher, name, flags = event.wd, event.name, event.mask
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

def debounce(wait_time):
    """
    Decorator that will debounce a function so that it is called after wait_time seconds
    If it is called multiple times, will wait for the last call to be debounced and run only this one.
    From: https://github.com/salesforce/decorator-operations
    """

    def decorator(function):
        def debounced(*args, **kwargs):
            def call_function():
                debounced._timer = None
                return function(*args, **kwargs)

            if debounced._timer is not None:
                debounced._timer.cancel()

            debounced._timer = threading.Timer(wait_time, call_function)
            debounced._timer.start()

        debounced._timer = None
        return debounced

    return decorator


class FILTER_DENY: pass


@dataclass
class Entity:

    path_glob = None
    main_path_re = None
    filename_re_parts = [
        re.compile('^(?P<flag>disabled)(?:=(?P<value>false|true))?$'),
        re.compile('^(?P<arg>name)=(?P<name>[^;]+)$'),
    ]
    main_filename_part = None
    filename_parts = [
        lambda args: f'name={args["name"]}' if args.get('name') else None,
        lambda args: 'disabled' if args.get('disabled', False) in (True, 'true', None) else None,
    ]

    parent_attr = None
    identifier_attr = None

    path: Path
    path_modified_at: float
    name: str
    disabled: bool

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
    def parse_filename(cls, name, parent, raw=False):
        main_part, *parts = name.split(';')
        if not (match := cls.main_path_re.match(main_part)):
            return None, None

        main = match.groupdict()
        args = {}
        for part in parts:
            for regex in cls.filename_re_parts:
                if match := regex.match(part):
                    values = match.groupdict()
                    is_flag = 'flag' in values and 'arg' not in values and len(values) == 2
                    if not (arg_name := values.pop('flag' if is_flag else 'arg', None)):
                        continue
                    if len(values) == 1:
                        values = list(values.values())[0]
                        if is_flag:
                            values = values in (None, 'true')
                    args[arg_name] = values

        if raw:
            return main, args

        return cls.prepare_filename_main_args(main), cls.prepare_filename_args(args)

    @classmethod
    def prepare_filename_main_args(cls, args):
        return args

    @classmethod
    def prepare_filename_args(cls, args):
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

    def on_create(self):
        return

    def on_child_entity_change(self, path, flags, expect_dir, entity_class, data_dict, data_identifier, args, modified_at=None):
        if (bool(flags & f.ISDIR) ^ expect_dir) or (flags & f.DELETE) or (flags & f.MOVED_FROM):
            data_dict[data_identifier].remove_version(path)
            return False

        if modified_at is None:
            try:
                modified_at = path.lstat().st_ctime
            except Exception:
                return False

        if data_dict[data_identifier].has_version(path):
            data_dict[data_identifier].get_version(path).path_modified_at = modified_at
            return False

        entity = entity_class.create_from_args(
            path=path,
            parent=self,
            identifier=data_identifier,
            args=args,
            path_modified_at=modified_at
        )
        data_dict[data_identifier].add_version(path, entity)
        entity.on_create()
        return True

    def version_activated(self):
        logger.debug(f'[{self}] Version activated: {self.path}')

    def version_deactivated(self):
        logger.debug(f'[{self}] Version deactivated: {self.path}')

    @classmethod
    def find_by_identifier_or_name(cls, data, filter, to_identifier, allow_disabled=False):
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
                        result = None
                        if version.name == filter:
                            return version

        # nothing found
        return None

    def rename(self, new_filename):
        self.path = self.path.replace(self.path.parent / new_filename)


@dataclass
class KeyFile(Entity):
    parent_attr = 'key'

    key: 'Key'

    @property
    def page(self):
        return self.key.page

    @property
    def deck(self):
        return self.page.deck


@dataclass
class KeyEvent(KeyFile):
    path_glob = 'ON_*'
    main_path_re = re.compile('^ON_(?P<kind>PRESS|RELEASE|START)(?:;|$)')
    filename_re_parts = Entity.filename_re_parts + [
        re.compile('^(?P<arg>action)=(?P<type>brightness)$'),
        re.compile(f'^(?P<arg>level)=(?P<brightness_operation>[+-=]?)(?P<brightness_level>{RE_PART_0_100})$'),
        re.compile('^(?P<arg>action)=(?P<type>page)$'),
        re.compile(f'^(?P<arg>page)=(?P<page_ref>.+)$'),
    ]
    main_filename_part = lambda args: f'ON_{args["kind"].upper()}'
    filename_parts = KeyFile.filename_parts + [
        lambda args: f'action={action}' if (action := args.get('action')) else None,
        lambda args: f'level={level.get("brightness_operation", "")}{level["brightness_level"]}' if args.get('action') == 'brightness' and (level := args.get('level')) else None,
        lambda args: f'page={args["page_ref"]}' if args.get('action') == 'page' else None,
    ]

    identifier_attr = 'kind'
    kind: str

    mode : str = None
    pid: int = None
    to_stop: bool = False
    brightness_level : Tuple[str, int] = ('=', DEFAULT_BRIGHTNESS)
    activated: bool = False

    @property
    def str(self):
        return f'EVENT {self.kind} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f'{self.key}, {self.str}'

    @classmethod
    def prepare_filename_main_args(cls, args):
        args = super().prepare_filename_main_args(args)
        args['kind'] = args['kind'].lower()
        return args

    @classmethod
    def prepare_filename_args(cls, args):
        final_args = super().prepare_filename_args(args)
        final_args['mode'] = None
        if 'action' not in args:
            final_args['mode'] = 'program'
        elif args['action'] == 'page':
            final_args['mode'] = 'page'
            final_args['page_ref'] = args['page']
        elif args['action'] == 'brightness':
            try:
                if 'brightness_level' in args['level']:
                    final_args['mode'] = 'brightness'
                    final_args['brightness_level'] = (
                        args['level'].get('brightness_operation') or '=',
                        int(args['level']['brightness_level'])
                    )
            except KeyError:
                pass
        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        event = super().create_from_args(path, parent, identifier, args, path_modified_at)
        event.mode = args.get('mode')
        if event.mode == 'brightness':
            event.brightness_level = args['brightness_level']
        elif event.mode == 'page':
            event.page_ref = args['page_ref']
        elif event.mode == 'program':
            event.to_stop = event.kind == 'start'
        return event

    def run(self):
        try:
            if self.mode == 'brightness':
                self.deck.set_brightness(*self.brightness_level)
            elif self.mode == 'page':
                self.deck.go_to_page(self.page_ref)
            elif self.mode == 'program':
                self.pid = Manager.start_process(self.path, register_stop=self.to_stop)
        except Exception:
            logger.exception(f'[{self}] Failure while running the command')

    def version_activated(self):
        super().version_activated()
        self.activate()

    def version_deactivated(self):
        super().version_deactivated()
        self.deactivate()

    def stop(self):
        if not self.pid:
            return
        try:
            Manager.terminate_process(self.pid)
        except Exception:
            logger.exception(f'[{self}] Failure while stopping the command')

    @property
    def is_stoppable(self):
        return self.kind == 'start' and self.mode == 'program' and self.pid and self.to_stop

    def activate(self):
        if not self.page.is_current:
            return
        if self.activated:
            return
        self.activated = True
        if self.kind == 'start' and self.mode == 'program':
            self.run()

    def deactivate(self):
        if not self.page.is_current:
            return
        if not self.activated:
            return
        self.activated = False
        if self.is_stoppable:
            self.stop()


@dataclass
class KeyImageLayer(KeyFile):
    path_glob = 'IMAGE*'
    main_path_re = re.compile('^(?P<kind>IMAGE)(?:;|$)')
    filename_re_parts = Entity.filename_re_parts + [
        re.compile('^(?P<arg>layer)=(?P<layer>\d+)$'),
        re.compile(f'^(?P<arg>colorize)=(?P<color>{RE_PART_COLOR})$'),
        re.compile(f'^(?P<arg>margin)=(?P<top>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<right>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<bottom>-?{RE_PART_PERCENT_OR_NUMBER}),(?P<left>-?{RE_PART_PERCENT_OR_NUMBER})$'),
        re.compile(f'^(?P<arg>crop)=(?P<left>{RE_PART_PERCENT_OR_NUMBER}),(?P<top>{RE_PART_PERCENT_OR_NUMBER}),(?P<right>{RE_PART_PERCENT_OR_NUMBER}),(?P<bottom>{RE_PART_PERCENT_OR_NUMBER})$'),
        re.compile(f'^(?P<arg>opacity)=(?P<opacity>{RE_PART_0_100})$'),
    ]
    main_filename_part = lambda args: 'IMAGE'
    filename_parts = KeyFile.filename_parts + [
        lambda args: f'layer={layer}' if (layer := args.get('layer')) else None,
        lambda args: f'colorize={color}' if (color := args.get('colorize')) else None,
        lambda args: f'margin={margin["top"]},{margin["right"]},{margin["bottom"]},{margin["left"]}' if (margin := args.get('margin')) else None,
        lambda args: f'crop={crop["left"]},{crop["top"]},{crop["right"]},{crop["bottom"]}' if (crop := args.get('crop')) else None,
        lambda args: f'opacity={opacity}' if (opacity := args.get('opacity')) else None,
    ]

    no_margins = {'top': ('int', 0), 'right': ('int', 0), 'bottom': ('int', 0), 'left': ('int', 0)}

    identifier_attr = 'layer'
    layer: int

    color: str = None
    margin: dict = None
    crop: dict = None
    opacity: int = None

    compose_cache: Tuple[Image.Image, int, int] = None

    @property
    def str(self):
        return f'LAYER {self.layer} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f'{self.key}, {self.str}'

    @classmethod
    def prepare_filename_args(cls, args):
        final_args = super().prepare_filename_args(args)
        final_args['layer'] = int(args['layer']) if 'layer' in args else -1  # -1 for image used if no layers
        for name in ('margin', 'crop'):
            if name not in args:
                continue
            final_args[name] = {}
            for part, val in list(args[name].items()):
                kind = 'int'
                if val.endswith('%'):
                    kind = '%'
                    val = val[:-1]
                final_args[name][part] = (kind, int(val))
            continue
        if 'colorize' in args:
            final_args['color'] = args['colorize']
        if 'opacity' in args:
            final_args['opacity'] = int(args['opacity'])
        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        layer = super().create_from_args(path, parent, identifier, args, path_modified_at)
        for key in ('margin', 'crop', 'color', 'opacity'):
            if key not in args:
                continue
            setattr(layer, key, args[key])
        return layer

    def version_activated(self):
        super().version_activated()
        self.key.on_image_changed()

    def version_deactivated(self):
        super().version_deactivated()
        self.key.on_image_changed()

    def compose(self):
        if not self.compose_cache:

            image_size = self.key.image_size
            layer_image = Image.open(self.path)

            if self.crop:
                crops = {}
                for crop_name, (crop_kind, crop) in self.crop.items():
                    if crop_kind == '%':
                        size = layer_image.width if crop_name in ('left', 'right') else layer_image.height
                        crop = int(crop * size / 100)
                    crops[crop_name] = crop

                layer_image = layer_image.crop((crops['left'], crops['top'], crops['right'], crops['bottom']))

            margins = {}
            for margin_name, (margin_kind, margin) in (self.margin or self.no_margins).items():
                if margin_kind == '%':
                    size = image_size[0 if margin_name in ('left', 'right') else 1]
                    margin = int(margin * size / 100)
                margins[margin_name] = margin

            thumbnail_max_width = image_size[0] - (margins['right'] + margins['left'])
            thumbnail_max_height = image_size[1] - (margins['top'] + margins['bottom'])
            thumbnail = layer_image.convert("RGBA")
            thumbnail.thumbnail((thumbnail_max_width, thumbnail_max_height), Image.LANCZOS)
            thumbnail_x = (margins['left'] + (thumbnail_max_width - thumbnail.width) // 2)
            thumbnail_y = (margins['top'] + (thumbnail_max_height - thumbnail.height) // 2)

            if self.color:
                alpha = thumbnail.getchannel('A')
                thumbnail = Image.new('RGBA', thumbnail.size, color=self.color)
                thumbnail.putalpha(alpha)

            if self.opacity:
                thumbnail.putalpha(ImageEnhance.Brightness(thumbnail.getchannel('A')).enhance(self.opacity/100))

            self.compose_cache = (thumbnail, thumbnail_x, thumbnail_y)

        return self.compose_cache


@dataclass
class Key(Entity):

    path_glob = 'KEY_ROW_*_COL_*'
    dir_template = 'KEY_ROW_{row}_COL_{col}'
    main_path_re = re.compile('^(?P<kind>KEY)_ROW_(?P<row>\d+)_COL_(?P<col>\d+)(?:;|$)')
    main_filename_part = lambda args: f'KEY_ROW_{args["row"]}_COL_{args["col"]}'

    parent_attr = 'page'
    identifier_attr = 'key'

    page: 'Page'
    key: Tuple[int, int]

    events: Dict = field(default_factory=versions_dict_factory)
    layers: Dict = field(default_factory=versions_dict_factory)

    compose_image_cache: Tuple[bool, Union[None, memoryview]] = None

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
    def prepare_filename_main_args(cls, args):
        args = super().prepare_filename_main_args(args)
        args['row'] = int(args['row'])
        args['col'] = int(args['col'])
        return args

    @classmethod
    def parse_filename(cls, name, parent, raw=False):
        main, args = super().parse_filename(name, parent, raw=raw)
        if raw:
            return main, args
        if main is not None and parent.deck.device:
            if main['row'] < 1 or main['row'] > parent.deck.nb_rows or main['col'] < 1 or main['col'] > parent.deck.nb_cols:
                return None, None
        return main, args

    def on_create(self):
        super().on_create()
        self.read_directory()
        Manager.add_watch(self.path, self)

    def read_directory(self):
        if self.deck.filters.get('event') != FILTER_DENY:
            for event_file in sorted(self.path.glob(KeyEvent.path_glob)):
                self.on_file_change(event_file.name, f.CREATE | (f.ISDIR if event_file.is_dir() else 0), entity_class=KeyEvent)
        if self.deck.filters.get('layer') != FILTER_DENY:
            for image_file in sorted(self.path.glob(KeyImageLayer.path_glob)):
                self.on_file_change(image_file.name, f.CREATE | (f.ISDIR if image_file.is_dir() else 0), entity_class=KeyImageLayer)

    def on_file_change(self, name, flags, modified_at=None, entity_class=None):
        path = self.path / name
        if (event_filter := self.deck.filters.get('event')) != FILTER_DENY:
            if not entity_class or entity_class is KeyEvent:
                main, args = KeyEvent.parse_filename(name, self)
                if main:
                    if event_filter is not None:
                        if main.get('kind') != event_filter:
                            return
                    return self.on_child_entity_change(path=path, flags=flags, expect_dir=False, entity_class=KeyEvent, data_dict=self.events, data_identifier=main['kind'], args=args, modified_at=modified_at)
        if (layer_filter := self.deck.filters.get('layer')) != FILTER_DENY:
            if not entity_class or entity_class is KeyImageLayer:
                main, args = KeyImageLayer.parse_filename(name, self)
                if main:
                    if layer_filter is not None:
                        try:
                            is_matching = args['layer'] == int(layer_filter)
                        except ValueError:
                            is_matching = False
                        is_matching = is_matching or args.get('name') == layer_filter
                        if not is_matching:
                            return
                    return self.on_child_entity_change(path=path, flags=flags, expect_dir=False, entity_class=KeyImageLayer, data_dict=self.layers, data_identifier=args['layer'], args=args, modified_at=modified_at)

    def on_image_changed(self):
        self.compose_image_cache = None
        self.render()

    @property
    def image_size(self):
        return self.width, self.height

    @property
    def sorted_layers(self):
        return {num_layer: layer for num_layer, layer in sorted(self.layers.items()) if layer}

    def compose_image(self):
        if not self.compose_image_cache:
            try:
                if not self.layers:
                    self.compose_image_cache = (False, None)
                else:
                    layers = self.sorted_layers
                    if len(layers) > 1:
                        # if more than one layer, we ignore the image used if no specific layers
                        layers.pop(-1, None)
                    if not layers:
                        self.compose_image_cache = False, None
                    else:
                        final_image = Image.new("RGB", self.image_size, 'black')
                        for index, layer in layers.items():
                            try:
                                thumbnail, thumbnail_x, thumbnail_y = layer.compose()
                            except Exception:
                                logger.exception(f'[{layer}] Layer could not be rendered')
                                continue  # we simply ignore a layer that couldn't be created
                            final_image.paste(thumbnail, (thumbnail_x, thumbnail_y), thumbnail)
                        self.compose_image_cache = True, PILHelper.to_native_format(self.deck.device, final_image)
            except Exception:
                logger.exception(f'[{self}] Image could not be rendered')
                self.compose_image_cache = False, None

        return self.compose_image_cache[1] if self.compose_image_cache[0] else None

    def render(self):
        if not self.page.is_current:
            return
        self.deck.set_image(self.row, self.col, self.compose_image())
        for event in self.events.values():
            if event:
                event.activate()

    def unrender(self):
        if not self.page.is_current:
            return
        self.deck.remove_image(self.row, self.col)
        for event in self.events.values():
            if event:
                event.deactivate()

    def version_activated(self):
        super().version_activated()
        self.render()

    def version_deactivated(self):
        super().version_deactivated()
        self.unrender()

    def find_layer(self, layer_filter, allow_disabled=False):
        return KeyImageLayer.find_by_identifier_or_name(self.layers, layer_filter, int, allow_disabled=allow_disabled)

    def find_event(self, event_filter, allow_disabled=False):
        return KeyEvent.find_by_identifier_or_name(self.events, event_filter, str, allow_disabled=allow_disabled)


@dataclass
class Page(Entity):

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

    deck: 'Deck'
    number: int
    keys: Dict = field(default_factory=versions_dict_factory)

    @property
    def str(self):
        return f'PAGE {self.number} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f'{self.deck}, {self.str}'

    @classmethod
    def prepare_filename_main_args(cls, args):
        args = super().prepare_filename_main_args(args)
        args['page'] = int(args['page'])
        return args

    def on_create(self):
        super().on_create()
        self.read_directory()
        Manager.add_watch(self.path, self)

    def read_directory(self):
        if self.deck.filters.get('key') != FILTER_DENY:
            for key_dir in sorted(self.path.glob(Key.path_glob)):
                self.on_file_change(key_dir.name, f.CREATE | (f.ISDIR if key_dir.is_dir() else 0))

    def on_file_change(self, name, flags, modified_at=None, entity_class=None):
        path = self.path / name
        if (key_filter := self.deck.filters.get('key')) != FILTER_DENY:
            if not entity_class or entity_class is Key:
                main, args = Key.parse_filename(name, self)
                if main:
                    if key_filter is not None:
                        try:
                            is_matching = (main['row'], main['col']) == tuple(int(val) for val in key_filter.split(','))
                        except ValueError:
                            is_matching = False
                        is_matching = is_matching or args.get('name') == key_filter
                        if not is_matching:
                            return
                    return self.on_child_entity_change(path=path, flags=flags, expect_dir=True, entity_class=Key, data_dict=self.keys, data_identifier=(main['row'], main['col']), args=args, modified_at=modified_at)

    @property
    def is_current(self):
        return self.number == self.deck.current_page_number

    def iter_keys(self):
        for row_col, key in sorted(self.keys.items()):
            if key:
                yield key

    def render(self):
        if not self.is_current:
            return
        for key in self.iter_keys():
            key.render()

    def unrender(self):
        if not self.is_current:
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
        self.render()

    def version_deactivated(self):
        super().version_deactivated()
        self.unrender()
        self.deck.go_to_page(Page.BACK)


@dataclass
class Deck(Entity):
    device: StreamDeck
    pages: Dict = field(default_factory=versions_dict_factory)
    current_page_number: int = None
    brightness: int = DEFAULT_BRIGHTNESS

    def __post_init__(self):
        self.serial = self.device.info['serial'] if self.device else None
        self.nb_cols = self.device.info['cols'] if self.device else None
        self.nb_rows = self.device.info['rows'] if self.device else None
        self.key_width = self.device.info['key_width'] if self.device else None
        self.key_height = self.device.info['key_height'] if self.device else None
        self.waiting_images = {}
        self.render_images_thread = None
        self.render_images_queue = None
        self.filters = {}
        self.page_number_history = []

    @property
    def str(self):
        return f'DECK {self.serial or self.name}{", disabled" if self.disabled else ""}'

    def __str__(self):
        return self.str

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
        self.device.set_key_callback(self.on_key_pressed)
        self.go_to_page(Page.FIRST)

    def read_directory(self):
        if self.filters.get('page') != FILTER_DENY:
            for page_dir in sorted(self.path.glob(Page.path_glob)):
                self.on_file_change(page_dir.name, f.CREATE | (f.ISDIR if page_dir.is_dir() else 0))

    def on_file_change(self, name, flags, modified_at=None, entity_class=None):
        path = self.path / name
        if (page_filter := self.filters.get('page')) != FILTER_DENY:
            if not entity_class or entity_class is Page:
                main, args = Page.parse_filename(name, self)
                if main:
                    if page_filter is not None:
                        try:
                            is_matching = main['page'] == int(page_filter)
                        except ValueError:
                            is_matching = False
                        is_matching = is_matching or args.get('name') == page_filter
                        if not is_matching:
                            return
                    return self.on_child_entity_change(path=path, flags=flags, expect_dir=True, entity_class=Page, data_dict=self.pages, data_identifier=main['page'], args=args, modified_at=modified_at)

    def go_to_page(self, page_ref):
        if page_ref is None:
            return
        if isinstance(page_ref, int):
            if page_ref == self.current_page_number:
                return
            if not (page := self.pages.get(page_ref)):
                return
        elif page_ref == Page.FIRST:
            if not (possible_pages := sorted([(number, page) for number, page in self.pages.items() if page])):
                return
            page = possible_pages[0][1]
        elif page_ref == Page.BACK:
            while True:
                if not self.page_number_history:
                    return
                if (page_num := self.page_number_history.pop()) == self.current_page_number:
                    continue
                if (page := self.pages.get(page_num)):
                    break
            else:
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
        if page.number == self.current_page_number:
            return
        if self.current_page_number and (current_page := self.current_page):
            logger.info(f'[{self}] Changing current page from [{current_page}] to [{page}] (Asked: {page_ref})')
            self.current_page.unrender()
        else:
            logger.info(f'[{self}] Setting current page to [{page}] (Asked: {page_ref})')
        self.current_page_number = page.number
        if not self.page_number_history or self.page_number_history[-1] != page.number:
            self.page_number_history.append(page.number)
        page.render()

    @property
    def current_page(self):
        return self.pages.get(self.current_page_number) or None

    def on_key_pressed(self, deck, index, pressed):
        row, col = row_col = self.index_to_key(index)

        page = self.current_page
        if not page:
            logger.debug(f'[{self}, KEY ({row}, {col})] {"PRESSED" if pressed else "RELEASED"}. IGNORED (no current page)')
            return

        key = page.keys[row_col]
        if not key:
            logger.debug(f'[{page}, KEY ({row}, {col})] {"PRESSED" if pressed else "RELEASED"}. IGNORED (key not configured)')
            return

        key_event = key.events['press' if pressed else 'release']
        if not key_event:
            logger.debug(f'[{key}] {"PRESSED" if pressed else "RELEASED"}. IGNORED (event not configured)')
            return

        logger.info(f'[{key_event}] {"PRESSED" if pressed else "RELEASED"}.')
        key_event.run()

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
        if (page := self.current_page):
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


def render_deck_images(deck, queue):
    delay = 0.02
    future_margin = 0.002
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
            Manager.exit(1, f'No Stream Deck detected. Aborting.')
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
    def init_files_watcher(cls):
        cls.files_watcher = Inotifier()
        cls.files_watcher_thread = threading.Thread(target=cls.files_watcher.run)
        cls.files_watcher_thread.start()

    @classmethod
    def exit(cls, status=0, msg=None, msg_level=None, log_exception=False):
        if msg is not None:
            if msg_level is None:
                msg_level = 'info' if status == 0 else 'critical'
            getattr(logger, msg_level)(msg, exc_info=log_exception)

        for serial, deck in list(cls.decks.items()):
            if cls.files_watcher:
                cls.files_watcher.terminate()
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
    def start_process(cls, path, register_stop=False):
        base_str = f'[PROCESS] Launching `{path}`'
        logger.info(f'{base_str}...')
        try:
            process = psutil.Popen(path)
            cls.processes[process.pid] = {
                'pid': process.pid,
                'path': path,
                'process' : process,
                'to_stop': bool(register_stop),
            }
            logger.info(f'{base_str} [ok PID={process.pid}]')
            return process.pid
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
        process = process_info['process']
        base_str = f"[PROCESS {pid}] Terminating `{process_info['path']}`"
        logger.info(f'{base_str}...')
        gone, alive = cls.kill_proc_tree(pid, timeout=5)
        if alive:
            # TODO: handle the remaining processes
            logger.error(f'{base_str} [FAIL: still running: {" ".join([p.pid for p in alive])} ]')
        else:
            logger.info(f'{base_str} [done]')


@click.group()
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
@click.argument('level', type=int, callback=Manager.validate_brightness_level)
@common_options['verbosity']
def brightness(deck, level):
    """Set the brightness level of a Stream Deck.

    Arguments:

    LEVEL: Brightness level, from 0 (no light) to 100 (brightest)
    """
    deck = Manager.get_deck(deck)
    deck.set_brightness(level)



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
@common_options['verbosity']
def run(deck, directory):
    """Run, Forrest, Run!"""

    device = Manager.get_deck(deck)
    serial = device.info['serial']
    directory = Manager.normalize_deck_directory(directory, serial)
    if not directory.exists() or not directory.is_dir():
        return Manager.exit(1, f"{directory} does not exist or is not a directory")
    logger.info(f'[DECK {serial}] Running in directory "{directory}"')

    Manager.init_files_watcher()

    deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=serial, disabled=False, device=device)
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

    if Manager.files_watcher:
        Manager.files_watcher.terminate()

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
        'event':  click.option('-e', '--event', 'event_filter', type=str, required=True, help='An event name (press/release/start)'),
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
    def get_deck(directory, page_filter=None, key_filter=None, event_filter=None, layer_filter=None):
        directory = Manager.normalize_deck_directory(directory, None)
        deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=directory.name, disabled=False, device=None)
        if page_filter is not None:
            deck.filters['page'] = page_filter
        if key_filter is not None:
            deck.filters['key'] = key_filter
        if event_filter is not None:
            deck.filters['event'] = event_filter
        if layer_filter is not None:
            deck.filters['layer'] = layer_filter
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

    @staticmethod
    def find_event(key, event_filter):
        if not (event := key.find_event(event_filter, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Event `{event_filter}` not found')
        return event

    @classmethod
    def get_args(cls, obj):
        return obj.parse_filename(obj.path.name, obj.path.parent, raw=True)

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
        main, args = cls.get_args(obj)
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
                    final_args[name] = obj.parse_filename(cls.compose_filename(obj, main, {}) + f';{name}={value}', obj.path.parent, raw=True)[1][name]
                except KeyError:
                    Manager.exit(1, f'[{obj}] Configuration `{name} {value}` is not valid')

        return cls.compose_filename(obj, main, final_args)


FC = FilterCommands
FC.combine_options()


@cli.command()
@FC.options['page_filter']
def get_page_path(directory, page_filter):
    """Get the path of a page"""
    page = FC.find_page(FC.get_deck(directory, page_filter, FILTER_DENY, FILTER_DENY,FILTER_DENY), page_filter)
    print(page.path)

@cli.command()
@FC.options['page_filter_with_names']
def get_page_conf(directory, page_filter, names):
    """Get the configuration of a page, as json"""
    page = FC.find_page(FC.get_deck(directory, page_filter, FILTER_DENY, FILTER_DENY,FILTER_DENY), page_filter)
    print(FC.get_args_as_json(page, names or None))

@cli.command()
@FC.options['page_filter_with_names_and_values']
def set_page_conf(directory, page_filter, names_and_values):
    """Set the value of some entries in the configuration of a page"""
    page = FC.find_page(FC.get_deck(directory, page_filter, FILTER_DENY, FILTER_DENY, FILTER_DENY), page_filter)
    page.rename(FC.get_update_args_filename(page, names_and_values))

@cli.command()
@FC.options['key_filter']
def get_key_path(directory, page_filter, key_filter):
    """Get the path of a key"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, FILTER_DENY, FILTER_DENY), page_filter), key_filter)
    print(key.path)

@cli.command()
@FC.options['key_filter_with_names']
def get_key_conf(directory, page_filter, key_filter, names):
    """Get the configuration of a key, as json"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, FILTER_DENY, FILTER_DENY), page_filter), key_filter)
    print(FC.get_args_as_json(key, names or None))

@cli.command()
@FC.options['key_filter_with_names_and_values']
def set_key_conf(directory, page_filter, key_filter, names_and_values):
    """Set the value of some entries in the configuration of a key"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, FILTER_DENY, FILTER_DENY), page_filter), key_filter)
    key.rename(FC.get_update_args_filename(key, names_and_values))

@cli.command()
@FC.options['layer_filter']
def get_image_path(directory, page_filter, key_filter, layer_filter):
    """Get the path of an image/layer"""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, event_filter=FILTER_DENY, layer_filter=layer_filter), page_filter), key_filter), layer_filter)
    print(layer.path)

@cli.command()
@FC.options['layer_filter_with_names']
def get_image_conf(directory, page_filter, key_filter, layer_filter, names):
    """Get the configuration of an image/layer, as json"""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, event_filter=FILTER_DENY, layer_filter=layer_filter), page_filter), key_filter), layer_filter)
    print(FC.get_args_as_json(layer, names or None))

@cli.command()
@FC.options['layer_filter_with_names_and_values']
def set_image_conf(directory, page_filter, key_filter, layer_filter, names_and_values):
    """Set the value of some entries in the configuration of an layer"""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, event_filter=FILTER_DENY, layer_filter=layer_filter), page_filter), key_filter), layer_filter)
    layer.rename(FC.get_update_args_filename(layer, names_and_values))

@cli.command()
@FC.options['event_filter']
def get_event_path(directory, page_filter, key_filter, event_filter):
    """Get the path of an event"""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, layer_filter=FILTER_DENY, event_filter=event_filter), page_filter), key_filter), event_filter)
    print(event.path)

@cli.command()
@FC.options['event_filter_with_names']
def get_event_conf(directory, page_filter, key_filter, event_filter, names):
    """Get the configuration of an event, as json"""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, layer_filter=FILTER_DENY, event_filter=event_filter), page_filter), key_filter), event_filter)
    print(FC.get_args_as_json(event, names or None))

@cli.command()
@FC.options['event_filter_with_names_and_values']
def set_event_conf(directory, page_filter, key_filter, event_filter, names_and_values):
    """Set the value of some entries in the configuration of an event, as json"""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, page_filter, key_filter, layer_filter=FILTER_DENY, event_filter=event_filter), page_filter), key_filter), event_filter)
    event.rename(FC.get_update_args_filename(event, names_and_values))


if __name__ == '__main__':
    try:
        cli()
    except Exception:
        Manager.exit(1, 'Oops...', log_exception=True)
    else:
        Manager.exit(0, 'Bye.')
