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

from cached_property import cached_property

from ..common import DEFAULT_BRIGHTNESS, Manager, logger
from ..threads import Delayer, Repeater
from .base import RE_PARTS, Entity, InvalidArg
from .deck import DeckContent
from .key import KeyContent
from .page import PageContent

LONGPRESS_DURATION_MIN = 300  # in ms


@dataclass(eq=False)
class BaseEvent:
    run_modes = {"path", "command", "inside"}
    non_run_args = set()
    repeat_allowed_for = {"start"}

    path_glob = "ON_*"
    common_filename_re_parts = [
        # if the process must be detached from ours (ie launch and forget)
        re.compile(r"^(?P<flag>detach)(?:=(?P<value>false|true))?$"),
        # delay before launching action
        re.compile(r"^(?P<arg>wait)=(?P<value>\d+)$"),
        # repeat every, max times (ignored if not press/start)
        re.compile(r"^(?P<arg>every)=(?P<value>\d+)$"),
        re.compile(r"^(?P<arg>max-runs)=(?P<value>\d+)$"),
        # action run
        re.compile(r"^(?P<arg>command)=(?P<value>.+)$"),
        re.compile(r"^(?P<arg>slash)=(?P<value>.+)$"),
        re.compile(r"^(?P<arg>semicolon)=(?P<value>.+)$"),
        # do not run many times the same command at the same time
        re.compile(r"^(?P<flag>unique)(?:=(?P<value>false|true))?$"),
    ]
    main_filename_part = lambda args: f'ON_{args["kind"].upper()}'

    common_filename_parts = [
        lambda args: f"command={command}" if (command := args.get("command")) else None,
        lambda args: f"slash={slash}" if (slash := args.get("slash")) else None,
        lambda args: f"semicolon={semicolon}" if (semicolon := args.get("semicolon")) else None,
        lambda args: f"wait={wait}" if (wait := args.get("wait")) else None,
        lambda args: f"every={every}" if (every := args.get("every")) else None,
        lambda args: f"max-runs={max_runs}" if (max_runs := args.get("max-runs")) else None,
        lambda args: "detach" if args.get("detach", False) in (True, "true", None) else None,
        lambda args: "unique" if args.get("unique", False) in (True, "true", None) else None,
    ]

    identifier_attr = "kind"
    parent_container_attr = "events"

    kind: str

    def __post_init__(self):
        super().__post_init__()
        self.mode = None
        self.to_stop = False
        self.repeat_every = None
        self.max_runs = None
        self.wait = 0
        self.detach = False
        self.command = None
        self.unique = False
        self.pids = []
        self.activated = False
        self.activating_parent = None
        self.repeat_thread = None
        self.wait_thread = None
        self.duration_thread = None
        self.ended_running = threading.Event()
        self.ended_running.set()

    @property
    def str(self):
        return f'EVENT {self.kind} ({self.name}{", disabled" if self.disabled else ""})'

    def __str__(self):
        return f"{self.parent}, {self.str}"

    @classmethod
    def convert_main_args(cls, args):
        if (args := super().convert_main_args(args)) is None:
            return None
        args["kind"] = args["kind"].lower()
        return args

    @classmethod
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)

        if cls.non_run_args:
            one_of = cls.non_run_args | {"command"}
            if len([1 for key in one_of if args.get(key)]) > 1:
                raise InvalidArg(
                    "Only one of these arguments must be used: %s" % (", ".join(f'"{arg}"' for arg in sorted(one_of)))
                )

        if args.get("command"):
            final_args["mode"] = "inside" if args["command"] == "__inside__" else "command"
        elif cls.non_run_args.intersection(args):
            # handled in subclass
            final_args["mode"] = None
        else:
            final_args["mode"] = "path"

        if final_args["mode"] in cls.run_modes:
            if final_args["mode"] == "command":
                final_args["command"] = cls.replace_special_chars(args["command"], args)
            final_args["detach"] = args.get("detach", False)
            final_args["unique"] = args.get("unique", True if main["kind"] in ("start", "end") else False)

        if "every" in args:
            final_args["repeat-every"] = int(args["every"])
        if "max-runs" in args:
            final_args["max_runs"] = int(args["max-runs"])
        if "wait" in args:
            final_args["wait"] = int(args["wait"])

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        event = super().create_from_args(path, parent, identifier, args, path_modified_at)
        event.mode = args.get("mode")
        if event.mode in cls.run_modes:
            event.detach = args["detach"]
            event.unique = args["unique"]
            event.to_stop = event.kind == "start" and not event.detach
            if event.mode == "command":
                event.command = args["command"].strip()
        if event.kind in cls.repeat_allowed_for:
            if args.get("repeat-every"):
                event.repeat_every = args["repeat-every"]
                event.max_runs = args.get("max_runs")
        if args.get("wait"):
            event.wait = args["wait"]
        return event

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        return main.get("find") == filter

    @classmethod
    def find_reference_parent(cls, parent, ref_conf):
        raise NotImplementedError

    def iter_waiting_references_for_parent(self, parent):
        raise NotImplementedError

    @classmethod
    def find_reference(cls, parent, ref_conf, main, args):
        final_ref_conf, parent = cls.find_reference_parent(parent, ref_conf)
        if not final_ref_conf.get("event"):
            final_ref_conf["event"] = main["kind"].lower()
        if not parent:
            return final_ref_conf, None
        return final_ref_conf, parent.find_event(final_ref_conf["event"])

    def get_waiting_references(self):
        return [
            (path, parent, ref_conf)
            for obj, path, parent, ref_conf in self.iter_waiting_references_for_parent(self.parent)
            if (event := obj.find_event(ref_conf["event"])) and event.kind == self.kind
        ]

    @property
    def thread_name_base(self):
        raise NotImplementedError

    def start_repeater(self):
        if not self.repeat_every:
            return
        if self.repeat_thread:
            return
        # use `self.max_runs - 1` because action was already run once
        max_runs = (self.max_runs - 1) if self.max_runs else None
        self.repeat_thread = Repeater(
            self.run,
            self.repeat_every / 1000,
            max_runs=max_runs,
            end_callback=self.stop_repeater,
            name=f"{self.kind.capitalize()[:4]}Rep{self.thread_name_base}",
        )
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
        self.wait_thread = Delayer(
            self.run_and_repeat,
            duration,
            end_callback=self.stop_waiter,
            name=f"{self.kind.capitalize()[:4]}Wait{self.thread_name_base}",
        )
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

    def run(self):
        try:
            return self._run()
        except Exception:
            logger.exception(f"[{self}] Failure while running the command")
        return True

    def _run(self):
        if self.mode not in self.run_modes:
            return
        if self.unique and not self.ended_running.is_set():
            if self.kind != "start":
                logger.warning(
                    f'[{self} STILL RUNNING, EXECUTION SKIPPED [PIDS: {", ".join(str(pid) for pid in self.pids if pid in Manager.processes)}]'
                )
            return True
        if self.mode == "path":
            command = self.resolved_path
            shell = False
        elif self.mode == "inside":
            command = self.resolved_path.read_text().strip()
            shell = True
        elif self.mode == "command":
            command = self.command
            shell = True
        else:
            raise ValueError("Invalid mode")
        if pid := Manager.start_process(
            command,
            register_stop=self.to_stop,
            detach=self.detach,
            shell=shell,
            done_event=self.ended_running,
            env=self.env_vars,
        ):
            self.pids.append(pid)
        return True

    def wait_run_and_repeat(self):
        if self.wait:
            self.start_waiter()
        else:
            self.run_and_repeat()

    def run_and_repeat(self):
        if not self.run():
            return
        self.start_repeater()

    def version_activated(self):
        super().version_activated()
        if self.disabled or self.has_disabled_parent:
            return
        self.activate()

    def version_deactivated(self):
        super().version_deactivated()
        if self.disabled or self.has_disabled_parent:
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
                    logger.exception(f"[{self}] Failure while stopping the command (pid {pid})")

    @property
    def is_stoppable(self):
        return self.kind == "start" and self.to_stop and self.mode in self.run_modes and self.pids

    def can_be_activated(self, parent):
        raise NotImplementedError

    def activate(self, from_parent=None):
        if from_parent is None:
            from_parent = self.parent
        if not self.deck.is_running or not self.can_be_activated(from_parent):
            return
        if self.activated:
            return
        self.activating_parent = from_parent
        self.activated = True
        if self.kind == "start" and self.mode in self.run_modes:
            self.wait_run_and_repeat()

    def deactivate(self):
        if not self.activated:
            return
        self.activated = None
        self.activating_parent = None
        self.stop()
        if self.kind == "end" and self.mode in self.run_modes:
            self.wait_run_and_repeat()

    @cached_property
    def _env_vars(self):
        return (self.activating_parent or self.parent).env_vars | self.finalize_env_vars(
            {
                "event": self.kind,
                "event_name": "" if self.name == self.unnamed else self.name,
                "event_file": self.path,
            }
        )

    @cached_property
    def env_vars(self):
        return self._env_vars


@dataclass(eq=False)
class DeckEvent(BaseEvent, DeckContent):
    filename_re_parts = Entity.filename_re_parts + BaseEvent.common_filename_re_parts

    main_path_re = re.compile(r"^ON_(?P<kind>START|END)(?:;|$)")
    filename_parts = (
        [KeyContent.name_filename_part] + BaseEvent.common_filename_parts + [KeyContent.disabled_filename_part]
    )

    @classmethod
    def find_reference_parent(cls, parent, ref_conf):
        return ref_conf, None

    def iter_waiting_references_for_parent(self, parent):
        return []

    @property
    def thread_name_base(self):
        return "Deck"

    def can_be_activated(self, parent):
        return True


@dataclass(eq=False)
class PageEvent(BaseEvent, PageContent):
    filename_re_parts = (
        Entity.filename_re_parts
        + [
            # reference
            re.compile(r"^(?P<arg>ref)=(?P<page>.*):(?P<event>.*)$"),  # we'll use current kind if no event given
        ]
        + BaseEvent.common_filename_re_parts
    )

    main_path_re = re.compile(r"^ON_(?P<kind>START|END)(?:;|$)")
    filename_parts = (
        [
            KeyContent.name_filename_part,
            lambda args: f'ref={ref.get("page")}:{ref["event"]}' if (ref := args.get("ref")) else None,
        ]
        + BaseEvent.common_filename_parts
        + [
            KeyContent.disabled_filename_part,
        ]
    )

    @classmethod
    def find_reference_parent(cls, parent, ref_conf):
        return cls.find_reference_page(parent, ref_conf)

    def iter_waiting_references_for_parent(self, parent):
        return self.iter_waiting_references_for_page(self.page)

    @property
    def thread_name_base(self):
        return f"{self.page.number}"

    def can_be_activated(self, parent):
        return parent.is_visible


@dataclass(eq=False)
class KeyEvent(BaseEvent, KeyContent):
    non_run_args = {"page", "brightness"}
    repeat_allowed_for = BaseEvent.repeat_allowed_for | {"press"}

    filename_re_parts = (
        Entity.filename_re_parts
        + [
            # reference
            re.compile(
                r"^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<event>.*)$"  # we'll use current kind if no event given
            )
        ]
        + BaseEvent.common_filename_re_parts
        + [
            # max duration a key must be pressed to run the action, only for press
            re.compile(r"^(?P<arg>duration-max)=(?P<value>\d+)$"),
            # min duration a key must be pressed to run the action, only for longpress/release
            re.compile(r"^(?P<arg>duration-min)=(?P<value>\d+)$"),
            # action brightness
            re.compile(
                r"^(?P<arg>brightness)=(?P<brightness_operation>[+-=]?)(?P<brightness_level>"
                + RE_PARTS["0-100"]
                + ")$"
            ),
            # action page
            re.compile(r"^(?P<arg>page)=(?P<value>.+)$"),
            re.compile(r"^(?P<flag>overlay)(?:=(?P<value>false|true))?$"),
        ]
    )
    main_path_re = re.compile(r"^ON_(?P<kind>PRESS|LONGPRESS|RELEASE|START|END)(?:;|$)")

    filename_parts = (
        [
            KeyContent.name_filename_part,
            lambda args: f'ref={ref.get("page") or ""}:{ref.get("key") or ref.get("key_same_page") or ""}:{ref["event"]}'
            if (ref := args.get("ref"))
            else None,
            lambda args: f'brightness={brightness.get("brightness_operation", "")}{brightness["brightness_level"]}'
            if (brightness := args.get("brightness"))
            else None,
            lambda args: f"page={page}" if (page := args.get("page")) else None,
            lambda args: "overlay" if args.get("overlay", False) in (True, "true", None) else None,
        ]
        + BaseEvent.common_filename_parts
        + [
            lambda args: f"duration-min={duration_min}" if (duration_min := args.get("duration-min")) else None,
            lambda args: f"duration-max={duration_max}" if (duration_max := args.get("duration-max")) else None,
            KeyContent.disabled_filename_part,
        ]
    )

    def __post_init__(self):
        super().__post_init__()
        self.brightness_level = ("=", DEFAULT_BRIGHTNESS)
        self.page_ref = None
        self.overlay = False
        self.duration_max = None
        self.duration_min = None

    @classmethod
    def convert_args(cls, main, args):
        from .page import BACK

        final_args = super().convert_args(main, args)

        if args.get("page"):
            final_args["mode"] = "page"
            final_args["page_ref"] = args["page"]
            if "page_ref" != BACK and "overlay" in args:
                final_args["overlay"] = args["overlay"]
        elif args.get("brightness"):
            final_args["mode"] = "brightness"
            final_args["brightness_level"] = (
                args["brightness"].get("brightness_operation") or "=",
                int(args["brightness"]["brightness_level"]),
            )
        else:
            if "duration-max" in args:
                final_args["duration-max"] = int(args["duration-max"])
            if "duration-min" in args:
                final_args["duration-min"] = int(args["duration-min"])

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        event = super().create_from_args(path, parent, identifier, args, path_modified_at)
        event.mode = args.get("mode")
        if event.mode == "brightness":
            event.brightness_level = args["brightness_level"]
        elif event.mode == "page":
            event.page_ref = args["page_ref"]
            event.overlay = args.get("overlay", False)
        if event.kind == "press":
            if args.get("duration-max"):
                event.duration_max = args["duration-max"]
        if event.kind in ("longpress", "release"):
            if args.get("duration-min"):
                event.duration_min = args["duration-min"]
            elif event.kind == "longpress":
                event.duration_min = LONGPRESS_DURATION_MIN
        return event

    @classmethod
    def find_reference_parent(cls, parent, ref_conf):
        return cls.find_reference_key(parent, ref_conf)

    def iter_waiting_references_for_parent(self, parent):
        return self.iter_waiting_references_for_key(self.key)

    @property
    def thread_name_base(self):
        return f"{self.page.number}.{self.key.row}{self.key.col}"

    def run_if_less_than_duration_max(self, thread):
        if thread.did_run():
            # already aborted
            self.stop_duration_waiter()
            logger.info(f"[{self}] ABORTED (pressed more than {self.duration_max}ms)")
            return
        self.stop_duration_waiter()
        # if it was stopped, it's by the release button during the duration_max time, so we know the
        # button was pressed less time than this duration_max, so we can run the action
        # but if we have a configured wait time, we must ensure we wait for it
        if self.wait and (wait_left := self.wait / 1000 - thread.duration) > 0:
            self.start_waiter(wait_left)
        else:
            self.run_and_repeat()

    def _run(self):
        if self.mode == "brightness":
            self.deck.set_brightness(*self.brightness_level)
        elif self.mode == "page":
            self.deck.go_to_page(self.page_ref, self.overlay)
        else:
            return super()._run()

    def wait_run_and_repeat(self, on_press=False):
        if self.duration_max:
            self.duration_thread = Delayer(
                lambda: None,
                self.duration_max / 1000,
                end_callback=self.run_if_less_than_duration_max,
                name=f"{self.kind.capitalize()[:4]}Max{self.thread_name_base}",
            )
            self.duration_thread.start()
        elif self.kind == "longpress" and on_press:
            # will call this function again, but with on_press False so we'll then go to start_water/run_and_repeat
            self.duration_thread = Delayer(
                self.wait_run_and_repeat,
                self.duration_min / 1000,
                end_callback=self.stop_duration_waiter,
                name=f"{self.kind.capitalize()[:4]}Min{self.thread_name_base}",
            )
            self.duration_thread.start()
        else:
            super().wait_run_and_repeat()

    def can_be_activated(self, parent):
        return parent.page.is_visible

    @property
    def env_vars(self):
        env_vars = self._env_vars

        if self.kind not in ("start", "end"):
            env_vars = env_vars | self.finalize_env_vars(
                {
                    "pressed_at": self.key.pressed_at,
                    "press_duration": self.key.press_duration,
                }
            )

        return env_vars
