#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import re
import threading
from dataclasses import dataclass

from ..common import DEFAULT_BRIGHTNESS, logger, Manager
from ..threads import Delayer, Repeater
from .base import RE_PARTS, InvalidArg
from .key import KeyFile


LONGPRESS_DURATION_MIN = 300  # in ms

RUN_MODES = ('path', 'command', 'inside')


@dataclass(eq=False)
class KeyEvent(KeyFile):
    path_glob = 'ON_*'
    main_path_re = re.compile(r'^ON_(?P<kind>PRESS|LONGPRESS|RELEASE|START)(?:;|$)')
    filename_re_parts = KeyFile.filename_re_parts + [
        # reference
        re.compile(r'^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<event>.*)$'),  # we'll use current kind if no event given
        # if the process must be detached from ours (ie launch and forget)
        re.compile(r'^(?P<flag>detach)(?:=(?P<value>false|true))?$'),
        # delay before launching action
        re.compile(r'^(?P<arg>wait)=(?P<value>\d+)$'),
        # repeat every, max times (ignored if not press/start)
        re.compile(r'^(?P<arg>every)=(?P<value>\d+)$'),
        re.compile(r'^(?P<arg>max-runs)=(?P<value>\d+)$'),
        # max duration a key must be pressed to run the action, only for press
        re.compile(r'^(?P<arg>duration-max)=(?P<value>\d+)$'),
        # min duration a key must be pressed to run the action, only for longpress/release
        re.compile(r'^(?P<arg>duration-min)=(?P<value>\d+)$'),
        # action brightness
        re.compile(r'^(?P<arg>brightness)=(?P<brightness_operation>[+-=]?)(?P<brightness_level>' + RE_PARTS["0-100"] + ')$'),
        # action page
        re.compile(r'^(?P<arg>page)=(?P<value>.+)$'),
        re.compile(r'^(?P<flag>overlay)(?:=(?P<value>false|true))?$'),
        # action run
        re.compile(r'^(?P<arg>command)=(?P<value>.+)$'),
        re.compile(r'^(?P<arg>slash)=(?P<value>.+)$'),
        re.compile(r'^(?P<arg>semicolon)=(?P<value>.+)$'),
        # do not run many times the same command at the same time
        re.compile(r'^(?P<flag>unique)(?:=(?P<value>false|true))?$'),
    ]
    main_filename_part = lambda args: f'ON_{args["kind"].upper()}'
    filename_parts = [
        KeyFile.name_filename_part,
        lambda args: f'ref={ref.get("page") or ""}:{ref.get("key") or ref.get("key_same_page") or ""}:{ref["event"]}' if (ref := args.get('ref')) else None,
        lambda args: f'brightness={brightness.get("brightness_operation", "")}{brightness["brightness_level"]}' if (brightness := args.get('brightness')) else None,
        lambda args: f'page={page}' if (page := args.get('page')) else None,
        lambda args: f'command={command}' if (command := args.get('command')) else None,
        lambda args: f'slash={slash}' if (slash := args.get('slash')) else None,
        lambda args: f'semicolon={semicolon}' if (semicolon := args.get('semicolon')) else None,
        lambda args: f'wait={wait}' if (wait := args.get('wait')) else None,
        lambda args: f'every={every}' if (every := args.get('every')) else None,
        lambda args: f'max-runs={max_runs}' if (max_runs := args.get('max-runs')) else None,
        lambda args: f'duration-min={duration_min}' if (duration_min := args.get('duration-min')) else None,
        lambda args: f'duration-max={duration_max}' if (duration_max := args.get('duration-max')) else None,
        lambda args: 'overlay' if args.get('overlay', False) in (True, 'true', None) else None,
        lambda args: 'detach' if args.get('detach', False) in (True, 'true', None) else None,
        lambda args: 'unique' if args.get('unique', False) in (True, 'true', None) else None,
        KeyFile.disabled_filename_part,
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
        self.command = None
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
        from .page import BACK
        final_args = super().convert_args(args)

        if len([1 for key in ('page', 'brightness', 'command') if args.get(key)]) > 1:
            raise InvalidArg('Only one of these arguments must be used: "page", "brightness", "command')

        if args.get('page'):
            final_args['mode'] = 'page'
        elif args.get('brightness'):
            final_args['mode'] = 'brightness'
        elif args.get('command'):
            final_args['mode'] = 'inside' if args['command'] == '__inside__' else 'command'
        else:
            final_args['mode'] = 'path'

        if final_args['mode'] in RUN_MODES:
            if final_args['mode'] == 'command':
                final_args['command'] = cls.replace_special_chars(args['command'], args)
            final_args['detach'] = args.get('detach', False)
            final_args['unique'] = args.get('unique', False)
        elif final_args['mode'] == 'page':
            final_args['page_ref'] = args['page']
            if 'page_ref' != BACK and 'overlay' in args:
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
        elif event.mode in RUN_MODES:
            event.detach = args['detach']
            event.unique = args['unique']
            event.to_stop = event.kind == 'start' and not event.detach
            if event.mode == 'command':
                event.command = args['command'].strip()
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
        self.repeat_thread = Repeater(self.run, self.repeat_every / 1000, max_runs=max_runs, end_callback=self.stop_repeater, name=f'{self.kind.capitalize()[:4]}Rep{self.page.number}.{self.key.row}{self.key.col}')
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
        if self.wait and (wait_left := self.wait / 1000 - thread.duration) > 0:
            self.start_waiter(wait_left)
        else:
            self.run_and_repeat()

    def run(self):
        try:
            if self.mode == 'brightness':
                self.deck.set_brightness(*self.brightness_level)
            elif self.mode == 'page':
                self.deck.go_to_page(self.page_ref, self.overlay)
            elif self.mode in RUN_MODES:
                if self.unique and not self.ended_running.is_set():
                    logger.warning(f'[{self} STILL RUNNING, EXECUTION SKIPPED [PIDS: {", ".join(str(pid) for pid in self.pids if pid in Manager.processes)}]')
                    return True
                if self.mode == 'path':
                    command = self.resolved_path
                    shell = False
                elif self.mode == 'inside':
                    command = self.resolved_path.read_text().strip()
                    shell = True
                elif self.mode == 'command':
                    command = self.command
                    shell = True
                else:
                    raise ValueError('Invalid mode')
                if (pid := Manager.start_process(command, register_stop=self.to_stop, detach=self.detach, shell=shell, done_event=self.ended_running)):
                    self.pids.append(pid)
        except Exception:
            logger.exception(f'[{self}] Failure while running the command')
        return True

    def wait_run_and_repeat(self, on_press=False):
        if self.duration_max:
            self.duration_thread = Delayer(lambda: None, self.duration_max / 1000, end_callback=self.run_if_less_than_duration_max, name=f'{self.kind.capitalize()[:4]}Max{self.page.number}.{self.key.row}{self.key.col}')
            self.duration_thread.start()
        elif self.kind == 'longpress' and on_press:
            # will call this function again, but with on_press False so we'll then go to start_water/run_and_repeat
            self.duration_thread = Delayer(self.wait_run_and_repeat, self.duration_min / 1000, end_callback=self.stop_duration_waiter, name=f'{self.kind.capitalize()[:4]}Min{self.page.number}.{self.key.row}{self.key.col}')
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
        return self.kind == 'start' and self.to_stop and self.mode in RUN_MODES and self.pids

    def activate(self, page=None):
        if page is None:
            page = self.page
        if not page.is_current:
            return
        if self.activated:
            return
        self.activated = True
        self.ended_running.set()
        if self.kind == 'start' and self.mode in RUN_MODES:
            self.wait_run_and_repeat()

    def deactivate(self):
        if not self.activated:
            return
        self.activated = False
        self.stop()
