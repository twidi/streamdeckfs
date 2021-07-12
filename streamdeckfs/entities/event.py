#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
import re
import threading
from dataclasses import dataclass

from cached_property import cached_property

from ..common import DEFAULT_BRIGHTNESS, Manager, logger
from ..threads import Delayer, Repeater
from .base import (
    RE_PARTS,
    VAR_RE_NAME_PART,
    Entity,
    EntityFile,
    InvalidArg,
    file_char_allowed_args,
)
from .deck import DeckContent
from .key import KeyContent
from .page import PageContent

LONGPRESS_DURATION_MIN = 300  # in ms


@dataclass(eq=False)
class BaseEvent(EntityFile):
    run_modes = {"path", "command", "inside"}
    non_run_args = set()
    repeat_allowed_for = {"start"}

    path_glob = "ON_*"
    main_part_re = re.compile(r"^ON_(?P<kind>START|END)$")
    main_part_compose = lambda args: f'ON_{args["kind"].upper()}'

    allowed_args = (
        Entity.allowed_args
        | {
            # if the process must be detached from ours (ie launch and forget)
            "detach": re.compile(r"^(?P<flag>detach)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
            # delay before launching action
            "wait": re.compile(r"^(?P<arg>wait)=(?P<value>\d+)$"),
            # repeat every, max times (ignored if not press/start)
            "every": re.compile(r"^(?P<arg>every)=(?P<value>\d+)$"),
            "max-runs": re.compile(r"^(?P<arg>max-runs)=(?P<value>\d+)$"),
            # action run
            "command": re.compile(r"^(?P<arg>command)=(?P<value>.+)$"),
            # do not run many times the same command at the same time
            "unique": re.compile(r"^(?P<flag>unique)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
            # do not show logs about runs in log
            "quiet": re.compile(r"^(?P<flag>quiet)(?:=(?P<value>" + RE_PARTS["bool"] + "))?$"),
        }
        | file_char_allowed_args
    )

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
        self.quiet = False
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

        final_args["quiet"] = args.get("quiet", False)

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
        if args.get("quiet"):
            event.quiet = args["quiet"]
        return event

    @staticmethod
    def args_matching_filter(main, args, filter):
        if filter is None:
            return True
        return filter in (main.get("kind"), args.get("name"))

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
            logger.error(f"[{self}] Failure while running the command", exc_info=logger.level <= logging.DEBUG)
        return True

    def _run(self):
        if self.mode not in self.run_modes:
            return
        if self.unique and not self.ended_running.is_set():
            if self.kind != "start":
                if logger.level <= logging.DEBUG:
                    logger.warning(
                        f'[{self} STILL RUNNING, EXECUTION SKIPPED [PIDS: {", ".join(str(pid) for pid in self.pids if pid in Manager.processes)}]'
                    )
                elif not self.quiet:
                    logger.warning(f"[{self}] Still running. Execution skipped.")
            return True
        if self.mode == "path":
            command = self.resolved_path
            if not command.stat().st_size:
                return False
            shell = False
        elif self.mode == "inside":
            command = self.resolved_path.read_text().strip()
            if not command:
                return False
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
            env=self.env_vars | self.finalize_env_vars(self.get_available_vars_values(), "VAR_"),
            working_dir=self.activating_parent.path,
            quiet=self.quiet,
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
                    logger.error(
                        f"[{self}] Failure while stopping the command [PID={pid}]",
                        exc_info=logger.level <= logging.DEBUG,
                    )

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
        if self.kind == "start":
            self.wait_run_and_repeat()

    def deactivate(self):
        if not self.activated:
            return
        self.activated = None
        self.activating_parent = None
        self.stop()
        if self.kind == "end":
            self.wait_run_and_repeat()

    @cached_property
    def _env_vars(self):
        return (self.activating_parent or self.parent).env_vars | self.finalize_env_vars(
            {
                "event": self.kind,
                "event_name": "" if self.name == self.unnamed else self.name,
                "event_file": self.path,
                "quiet": "True" if self.quiet else "",
            }
        )

    @cached_property
    def env_vars(self):
        return self._env_vars


@dataclass(eq=False)
class DeckEvent(BaseEvent, DeckContent):
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
    allowed_args = BaseEvent.allowed_args | {
        # reference
        "ref": re.compile(r"^(?P<arg>ref)=(?P<page>.*):(?P<event>.*)$"),  # we'll use current kind if no event given
    }

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


VAR_RE_DEST_PART = r"""
(?P<dest>
    (?P<same_page>:)  # ":VAR_" on the current page directory
    |
    (?P<deck>::)  # "::VAR_" on the deck directory
    |
    (?::(?P<other_key>[^:]+):)  # ":key:VAR_" on the "key" directory of the current page
    |
    (?:::(?P<other_page>[^:]+):)  # "::page:VAR_" on the "page" directory
    |
    (?:::(?P<other_page_key_page>[^:]+):(?P<other_page_key_key>[^:]+):)  # "::page:key:VAR_" on the "key" directory of the "page" directory
)?  # if not => "VAR_" on the current key directory
"""


@dataclass(eq=False)
class KeyEvent(BaseEvent, KeyContent):
    non_run_args = {"page", "brightness", "VAR"}
    repeat_allowed_for = BaseEvent.repeat_allowed_for | {"press"}

    main_part_re = re.compile(r"^ON_(?P<kind>PRESS|LONGPRESS|RELEASE|START|END)$")

    allowed_args = BaseEvent.allowed_args | {
        # reference
        "ref": re.compile(  # we'll use current kind if no event given
            r"^(?P<arg>ref)=(?:(?::(?P<key_same_page>.*))|(?:(?P<page>.+):(?P<key>.+))):(?P<event>.*)$"
        ),
        # max duration a key must be pressed to run the action, only for press
        "duration-max": re.compile(r"^(?P<arg>duration-max)=(?P<value>\d+)$"),
        # min duration a key must be pressed to run the action, only for longpress/release
        "duration-min": re.compile(r"^(?P<arg>duration-min)=(?P<value>\d+)$"),
        # action brightness
        "brightness": re.compile(
            r"^(?P<arg>brightness)=(?P<brightness_operation>[+-=]?)(?P<brightness_level>" + RE_PARTS["0-100"] + ")$"
        ),
        # action page
        "page": re.compile(r"^(?P<arg>page)=(?P<value>.+)$"),
        # action set var
        "VAR": re.compile(
            r"^"
            + VAR_RE_DEST_PART
            + r"(?P<arg>VAR_"
            + VAR_RE_NAME_PART
            + r"""
        )
        (?P<infile><)?=  # if `=` we set on value in file name; if `<=` we set in file content
        (?P<value>.*)
        $
        """,
            re.VERBOSE,
        ),
    }

    def __post_init__(self):
        super().__post_init__()
        self.brightness_level = ("=", DEFAULT_BRIGHTNESS)
        self.page_ref = None
        self.set_vars_conf = None
        self.duration_max = None
        self.duration_min = None

    @classmethod
    def save_raw_arg(cls, name, value, args):
        if "VAR_" in name:
            if "vars" not in args:
                args["vars"] = {}
            args["vars"][name] = value
        else:
            super().save_raw_arg(name, value, args)

    @classmethod
    def convert_args(cls, main, args):
        final_args = super().convert_args(main, args)

        if args.get("page"):
            if main["kind"] in ("start", "end"):
                raise InvalidArg(f'Changing page is not allowed for "{main["kind"].upper()}" events')
            final_args["mode"] = "page"
            final_args["page_ref"] = args["page"]
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

        if vars := args.get("vars"):
            final_args["set_vars"] = {}
            for name, var in vars.items():
                set_var_conf = final_args["set_vars"][name] = {
                    "name": var["name"],
                    "value": var.get("value") or "",
                    "dest": var.get("dest"),
                    "infile": bool(var.get("infile")),
                }
                if var.get("same_page"):
                    set_var_conf["dest_type"] = "page"
                    set_var_conf["page_ref"] = None
                elif var.get("deck"):
                    set_var_conf["dest_type"] = "deck"
                elif var.get("other_key"):
                    set_var_conf["dest_type"] = "key"
                    set_var_conf["page_ref"] = None
                    set_var_conf["key_ref"] = var["other_key"]
                elif var.get("other_page"):
                    set_var_conf["dest_type"] = "page"
                    set_var_conf["page_ref"] = var["other_page"]
                elif var.get("other_page_key_page"):
                    set_var_conf["dest_type"] = "key"
                    set_var_conf["page_ref"] = var["other_page_key_page"]
                    set_var_conf["key_ref"] = var["other_page_key_key"]
                else:
                    set_var_conf["dest_type"] = "key"
                    set_var_conf["page_ref"] = None
                    set_var_conf["key_ref"] = None

        return final_args

    @classmethod
    def create_from_args(cls, path, parent, identifier, args, path_modified_at):
        event = super().create_from_args(path, parent, identifier, args, path_modified_at)
        event.mode = args.get("mode")
        if event.mode == "brightness":
            event.brightness_level = args["brightness_level"]
        elif event.mode == "page":
            event.page_ref = args["page_ref"]
        if args.get("set_vars"):
            event.set_vars_conf = args["set_vars"]
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
            logger.debug(f"[{self}] ABORTED (pressed more than {self.duration_max}ms)")
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
        if self.set_vars_conf:
            for conf in self.set_vars_conf.values():
                self.set_var(conf)
        if self.mode == "brightness":
            self.deck.set_brightness(*self.brightness_level, quiet=self.quiet)
        elif self.mode == "page":
            self.deck.go_to_page(self.page_ref, quiet=self.quiet)
        else:
            return super()._run()

    def set_var(self, conf):
        name = conf["name"]
        value = conf["value"]

        if conf["dest_type"] == "deck":
            parent = self.deck
        elif conf["dest_type"] == "page":
            if conf["page_ref"]:
                parent = self.deck.find_page(conf["page_ref"])
            else:
                parent = self.page
        else:
            if conf["key_ref"]:
                if conf["page_ref"]:
                    page = self.deck.find_page(conf["page_ref"])
                else:
                    page = self.page
                parent = page.find_key(conf["key_ref"]) if page else None
            else:
                parent = self.key
        if not parent:
            logger.error(
                f"[{self}] Variable `VAR_{name}` cannot be set: unable to find a {conf['dest_type']} matching `{conf['dest']}`"
            )
            return

        if var := parent.find_var(name, allow_disabled=True):

            if conf["infile"]:
                if var.value is None and value == var.resolved_value:
                    logger.debug(f"[{self}] Variable `VAR_{name}` already had the correct value in `{var.path}`")
                    return
                filename = var.make_new_filename(update_args={}, remove_args={"value", "disabled"})

            else:
                if value == var.value:
                    logger.debug(
                        f"[{self}] Variable `VAR_{name}` already had the correct value configuration option in `{var.path}`"
                    )
                    return
                filename = var.make_new_filename(update_args={"value": conf["value"]}, remove_args={"disabled"})

            try:
                renamed, path = var.rename(new_filename=filename)
            except Exception:
                logger.error(
                    f"[{self}] Variable `VAR_{name}` cannot be set: error when renaming file `{var.path}` to `{parent.path / filename}`",
                    exc_info=logger.level <= logging.DEBUG,
                )
                return
            else:
                if conf["infile"]:
                    try:
                        path.write_text(value)
                    except Exception:
                        logger.error(
                            f"[{self}] Variable `VAR_{name}` cannot be set: error when renaming file `{var.path}` to `{parent.path / filename}`",
                            exc_info=logger.level <= logging.DEBUG,
                        )
                        return
                if renamed:
                    logger.debug(f"[{self}] Variable `VAR_{name}` updated (renamed from `{var.path}` to `{path}`)")
                elif conf["infile"]:
                    logger.debug(f"[{self}] Variable `VAR_{name}` updated (same path, `{path}`)")
                else:
                    logger.debug(f"[{self}] Variable `VAR_{name}` untouched (same path, `{path}`)")

        else:
            var = parent.var_class.create_basic(parent, {"name": name}, name)
            if conf["infile"]:
                path = var.path
            else:
                path = parent.path / var.make_new_filename({"value": conf["value"]}, set())
            try:
                if conf["infile"]:
                    path.write_text(value)
                else:
                    path.touch()
            except Exception:
                logger.error(
                    f"[{self}] Variable `VAR_{name}` cannot be set: error when creating file `{path}`",
                    exc_info=logger.level <= logging.DEBUG,
                )
                return
            else:
                logger.debug(f"[{self}] Variable `VAR_{name}` created (in `{path}`)")

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
