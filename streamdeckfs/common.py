#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
import os
import platform
import signal
import sys
import threading
from pathlib import Path
from queue import Empty
from subprocess import DEVNULL
from time import sleep, time

import click_log
import psutil
from inotify_simple import flags as file_flags  # noqa: F401
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeckMini import StreamDeckMini
from StreamDeck.Devices.StreamDeckOriginal import StreamDeckOriginal
from StreamDeck.Devices.StreamDeckOriginalV2 import StreamDeckOriginalV2
from StreamDeck.Devices.StreamDeckXL import StreamDeckXL
from StreamDeck.Transport.Transport import TransportError

from .threads import Repeater, set_thread_name

SUPPORTED_PLATFORMS = {
    "Linux": True,
    "Darwin": False,
    "Windows": False,
}
PLATFORM = platform.system()

LIBRARY_NAME = "streamdeckfs"


class ColorFormatter(click_log.ColorFormatter):
    # NOT thanks to click-log that does not format when there is an exception info
    # https://github.com/click-contrib/click-log/blob/0d72a212ae7a45ab890d6e88a690679f8b946937/click_log/core.py#L35
    def formatMessage(self, record):
        try:
            exc_info, record.exc_info = record.exc_info, None
            return click_log.ColorFormatter.format(self, record)
        finally:
            record.exc_info = exc_info

    def format(self, record):
        return logging.Formatter.format(self, record)


click_log.core._default_handler.formatter = ColorFormatter()


logger = logging.getLogger(LIBRARY_NAME)
click_log.basic_config(logger)


ASSETS_PATH = Path.resolve(Path(__file__)).parent / "assets"

DEFAULT_BRIGHTNESS = 30

RENDER_IMAGE_DELAY = 0.01


class FakeDevice:
    def __del__(self):
        pass


class FakeStreamDeckMini(FakeDevice, StreamDeckMini):
    pass


class FakeStreamDeckOriginal(FakeDevice, StreamDeckOriginal):
    pass


class FakeStreamDeckOriginalV2(FakeDevice, StreamDeckOriginalV2):
    pass


class FakeStreamDeckXL(FakeDevice, StreamDeckXL):
    pass


class Manager:
    open_decks = {}
    seen_ids = set()
    manager = None
    files_watcher = None
    files_watcher_thread = None
    processes = {}
    processes_checker_thread = None
    exited = False

    @classmethod
    def get_manager(cls):
        if not cls.manager:
            cls.manager = DeviceManager()
        return cls.manager

    @staticmethod
    def get_device_class(device_type):
        return {
            "Stream Deck Mini": StreamDeckMini,
            "Stream Deck Original": StreamDeckOriginal,
            "Stream Deck Original (V2)": StreamDeckOriginalV2,
            "Stream Deck XL": StreamDeckXL,
            "StreamDeckMini": StreamDeckMini,
            "StreamDeckOriginal": StreamDeckOriginal,
            "StreamDeckOriginalV2": StreamDeckOriginalV2,
            "StreamDeckXL": StreamDeckXL,
        }[device_type]

    @staticmethod
    def get_fake_device(device_class):
        return {
            StreamDeckMini: FakeStreamDeckMini,
            StreamDeckOriginal: FakeStreamDeckOriginal,
            StreamDeckOriginalV2: FakeStreamDeckOriginalV2,
            StreamDeckXL: FakeStreamDeckXL,
        }[device_class](None)

    @classmethod
    def get_decks(cls, limit_to_serials=None, need_open=True, exit_if_none=True):
        decks = {}
        for deck in cls.get_manager().enumerate():
            device_type = deck.deck_type()
            device_id = deck.id()
            already_seen = device_id in cls.seen_ids
            cls.seen_ids.add(device_id)
            try:
                device_class = cls.get_device_class(device_type)
            except KeyError:
                if not already_seen:
                    logger.warning(f'Stream Deck "{device_type}" (ID {device_id}) is not a type we can manage.')
                continue
            serial = cls.open_deck(deck)
            if not serial:
                if not already_seen:
                    logger.warning(
                        f'Stream Deck "{device_type}" (ID {device_id}) cannot be accessed. Maybe a program is already connected to it.'
                    )
                if need_open:
                    continue
                connected = False
                serial = f"UNKNOW-ID={device_id}"
            else:
                connected = True
                deck.reset()
                if limit_to_serials and serial not in limit_to_serials:
                    deck.info = {"serial": serial}
                    cls.close_deck(deck)
                    continue
                deck.set_brightness(DEFAULT_BRIGHTNESS)
            decks[serial] = deck
            deck.info = {
                "connected": connected,
                "serial": serial if connected else "Unknown",
                "id": device_id,
                "type": device_type,
                "class": device_class,
                "firmware": deck.get_firmware_version() if connected else "Unknown",
                "nb_keys": deck.key_count(),
                "rows": (layout := deck.key_layout())[0],
                "cols": layout[1],
                "format": (image_format := deck.key_image_format())["format"],
                "key_width": image_format["size"][0],
                "key_height": image_format["size"][1],
            }
            if connected:
                deck.reset()  # see https://github.com/abcminiuser/python-elgato-streamdeck/issues/38
        return decks

    @classmethod
    def open_deck(cls, device):
        try:
            device.open()
        except Exception:
            return
        serial = device.get_serial_number()
        logger.debug(f"[DECK {serial}] Connection opened")
        cls.open_decks[serial] = device
        return serial

    @classmethod
    def close_deck(cls, deck):
        if deck.connected():
            try:
                deck.reset()
            except Exception:
                pass
            try:
                deck.close()
            except Exception:
                pass
        try:
            serial = deck.info["serial"]
        except AttributeError:
            pass
        else:
            if serial:
                cls.open_decks.pop(serial, None)
        logger.debug(f"[DECK {serial or deck.id()}] Connection closed")
        sleep(0.05)  # https://github.com/abcminiuser/python-elgato-streamdeck/issues/68

    @classmethod
    def close_opened_decks(cls):
        if cls.open_decks:
            for serial, deck in list(cls.open_decks.items()):
                cls.close_deck(deck)

    @classmethod
    def get_deck(cls, serial):
        if len(serial) > 1:
            return cls.exit(1, f'Invalid serial "{" ".join(serial)}".')
        serial = serial[0] if serial else None
        decks = cls.get_decks(limit_to_serials=[serial] if serial else None)
        if not serial:
            if len(decks) > 1:
                return cls.exit(
                    1,
                    f'{len(decks)} Stream Decks detected, you need to specify the serial. Use the "inspect" command to list all available decks.',
                )
            return list(decks.values())[0]
        if serial not in decks:
            return cls.exit(
                1,
                f'No Stream Deck found with the serial "{serial}". Use the "inspect" command to list all available decks.',
            )
        return decks[serial]

    @classmethod
    def write_deck_model(cls, directory, device_class):
        model_path = directory / ".model"
        model_path.write_text(device_class.__name__)

    @classmethod
    def get_info_from_model_file(cls, directory):
        device_class = (Path(directory) / ".model").read_text().split(":")[0]
        fake_device = cls.get_fake_device(cls.get_device_class(device_class))
        nb_rows, nb_cols = fake_device.key_layout()
        key_width, key_height = fake_device.key_image_format()["size"]
        return {
            "model": device_class,
            "nb_rows": nb_rows,
            "nb_cols": nb_cols,
            "key_width": key_width,
            "key_height": key_height,
        }

    @staticmethod
    def render_deck_images(deck, queue):
        set_thread_name("ImgRenderer")
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
                        try:
                            deck.set_key_image(index, image)
                        except TransportError:
                            queue.put(None)
                            break
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

    @classmethod
    def add_watch(cls, directory, owner):
        if not cls.files_watcher:
            return
        cls.files_watcher.WatchedDirectory.add(cls.files_watcher, directory, owner)

    @classmethod
    def remove_watch(cls, directory, owner):
        if not cls.files_watcher:
            return
        cls.files_watcher.WatchedDirectory.remove(directory, owner)

    @classmethod
    def get_files_watcher_class(cls):
        from .watchers.inotify import InotifyFilesWatcher

        return InotifyFilesWatcher

    @classmethod
    def start_files_watcher(cls):
        if cls.files_watcher:
            return
        cls.files_watcher = cls.get_files_watcher_class()()
        cls.files_watcher_thread = threading.Thread(name=cls.files_watcher.thread_name, target=cls.files_watcher.run)
        cls.files_watcher_thread.start()

    @classmethod
    def end_files_watcher(cls):
        if not cls.files_watcher:
            return
        cls.files_watcher.stop()
        cls.files_watcher_thread.join(0.5)
        cls.files_watcher = cls.files_watcher_thread = None

    @classmethod
    def exit(cls, status=0, msg=None, msg_level=None, log_exception=False):
        if cls.exited:
            return
        if msg is not None:
            if msg_level is None:
                msg_level = "info" if status == 0 else "critical"
            getattr(logger, msg_level)(msg, exc_info=log_exception)

        cls.end_files_watcher()
        cls.end_processes_checker()
        cls.close_opened_decks()

        cls.exited = True
        exit(status)

    @staticmethod
    def normalize_deck_directory(directory, serial):
        if not isinstance(directory, Path):
            directory = Path(directory)
        if serial and directory.name != serial:
            directory /= serial
        return directory

    @classmethod
    def check_process_running(cls, pid, process_info):
        if (return_code := process_info["process"].poll()) is None:
            return True
        logger.info(
            f'[PROCESS {pid}] `{process_info["command"]}`{" (launched in detached mode)" if process_info["detached"] else ""} ended [ReturnCode={return_code}]'
        )
        cls.processes.pop(pid, None)
        if event := process_info.get("done_event"):
            event.set()
        return False

    @classmethod
    def check_running_processes(cls):
        for pid, process_info in list(cls.processes.items()):
            cls.check_process_running(pid, process_info)

    @classmethod
    def start_processes_checker(cls):
        if cls.processes_checker_thread:
            return
        cls.processes_checker_thread = Repeater(cls.check_running_processes, 0.1, name="ProcessChecker")
        cls.processes_checker_thread.start()

    @classmethod
    def end_processes_checker(cls):
        if not cls.processes_checker_thread:
            return
        cls.processes_checker_thread.stop()
        cls.processes_checker_thread.join(0.5)
        cls.processes_checker_thread = None

    @classmethod
    def start_process(cls, command, register_stop=False, detach=False, shell=False, done_event=None, env=None):
        if done_event is not None:
            done_event.clear()
        if not cls.processes_checker_thread:
            cls.start_processes_checker()

        base_str = f'[PROCESS] Launching `{command}`{" (in detached mode)" if detach else ""}'
        logger.debug(f"{base_str}...")
        try:
            process = psutil.Popen(
                command,
                start_new_session=bool(detach),
                shell=bool(shell),
                env=(os.environ | env) if env else None,
                stderr=None if logger.level == logging.DEBUG else DEVNULL,
            )
            cls.processes[process.pid] = {
                "pid": process.pid,
                "command": command,
                "process": process,
                "to_stop": bool(register_stop),
                "detached": detach,
                "done_event": done_event,
            }
            logger.info(f"{base_str} [ok PID={process.pid}]")
            return None if detach else process.pid
        except Exception:
            logger.error(f"{base_str} [failed]", exc_info=logger.level == logging.DEBUG)
            return None

    @classmethod
    def kill_proc_tree(cls, pid, sig=signal.SIGTERM, include_parent=True, timeout=None, on_terminate=None):
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
        gone, alive = psutil.wait_procs(children, timeout=timeout, callback=on_terminate)
        return (gone, alive)

    @classmethod
    def terminate_process(cls, pid):
        if not (process_info := cls.processes.get(pid)):
            return
        if not cls.check_process_running(pid, process_info):
            return
        base_str = f"[PROCESS {pid}] Terminating `{process_info['command']}`"
        logger.debug(f"{base_str}...")
        gone, alive = cls.kill_proc_tree(pid, timeout=5)
        if alive:
            # TODO: handle the remaining processes
            logger.error(f'{base_str} [FAIL: still running: {" ".join([p.pid for p in alive])} ]')
        else:
            logger.info(f"{base_str} [done]")
        cls.check_process_running(pid, process_info)  # to update the `done_event`

    @classmethod
    def get_executable(cls):
        executable = sys.argv[0]
        if executable.endswith(f"{LIBRARY_NAME}/__main__.py"):
            executable = f"{sys.executable} -m {LIBRARY_NAME}"
        return executable
