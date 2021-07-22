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
import re
import signal
import ssl
import sys
import threading
from pathlib import Path
from queue import Empty, SimpleQueue
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

SERIAL_RE_PART = r"[A-Z][A-Z0-9]{11}"
SERIAL_RE = re.compile(r"^" + SERIAL_RE_PART + "$")

MODEL_FILE_NAME = ".model"


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
    is_fake = True

    def __del__(self):
        pass

    def connected(self):
        return False

    def set_brightness(self, percent):
        pass

    def set_key_callback(self, callback):
        pass

    def set_key_image(self, key, image):
        pass


class FakeStreamDeckMini(FakeDevice, StreamDeckMini):
    pass


class FakeStreamDeckOriginal(FakeDevice, StreamDeckOriginal):
    pass


class FakeStreamDeckOriginalV2(FakeDevice, StreamDeckOriginalV2):
    pass


class FakeStreamDeckXL(FakeDevice, StreamDeckXL):
    pass


class FakeStreamDeckWeb(FakeStreamDeckXL):
    KEY_PIXEL_WIDTH = 100
    KEY_PIXEL_HEIGHT = 100

    def __init__(self, device, nb_rows, nb_cols):
        self.KEY_ROWS = nb_rows
        self.KEY_COLS = nb_cols
        super().__init__(device)

    @property
    def KEY_COUNT(self):
        return self.KEY_COLS * self.KEY_ROWS


WEB_QUEUE_ALL_IMAGES = "WEB_QUEUE_ALL_IMAGES"


DEVICE_CLASSES = {
    "Stream Deck Mini": StreamDeckMini,
    "Stream Deck Original": StreamDeckOriginal,
    "Stream Deck Original (V2)": StreamDeckOriginalV2,
    "Stream Deck XL": StreamDeckXL,
    "StreamDeckMini": StreamDeckMini,
    "StreamDeckOriginal": StreamDeckOriginal,
    "StreamDeckOriginalV2": StreamDeckOriginalV2,
    "StreamDeckXL": StreamDeckXL,
    "StreamDeckWeb": FakeStreamDeckWeb,
}

FAKE_DEVICE_CLASSES = {
    StreamDeckMini: FakeStreamDeckMini,
    StreamDeckOriginal: FakeStreamDeckOriginal,
    StreamDeckOriginalV2: FakeStreamDeckOriginalV2,
    StreamDeckXL: FakeStreamDeckXL,
    FakeStreamDeckWeb: FakeStreamDeckWeb,
}


class Manager:
    open_decks = {}
    seen_ids = set()
    manager = None
    files_watcher = None
    files_watcher_thread = None
    processes = {}
    processes_checker_thread = None
    exited = False
    render_queues = {}
    started_decks = {}
    to_web_queue = None
    from_web_queue = None
    _stop_web_thread = None

    @classmethod
    def get_manager(cls):
        if not cls.manager:
            cls.manager = DeviceManager()
        return cls.manager

    @staticmethod
    def get_device_class(device_type):
        return DEVICE_CLASSES[device_type]

    @staticmethod
    def get_fake_device(device_class, **args):
        return FAKE_DEVICE_CLASSES[device_class](None, **args)

    @classmethod
    def get_decks(cls, limit_to_serials=None, need_open=True, exit_if_none=True):
        decks = {}
        for deck in cls.get_manager().enumerate():
            device_type = deck.deck_type()
            device_id = deck.id()
            already_seen = device_id in cls.seen_ids
            cls.seen_ids.add(device_id)
            try:
                cls.get_device_class(device_type)
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
            deck.info = cls.get_device_info(deck) | {
                "connected": connected,
                "serial": serial if connected else "Unknown",
                "id": device_id,
                "firmware": deck.get_firmware_version() if connected else "Unknown",
            }
            if connected:
                deck.reset()  # see https://github.com/abcminiuser/python-elgato-streamdeck/issues/38
        return decks

    @classmethod
    def get_device_info(cls, device):
        image_format = device.key_image_format()
        key_width, key_height = image_format["size"]
        flip_h, flip_v = image_format["flip"]
        return {
            "device": device,
            "class": device.__class__,
            "model": device.__class__.__name__.replace("Fake", ""),
            "nb_keys": device.key_count(),
            "nb_rows": (layout := device.key_layout())[0],
            "nb_cols": layout[1],
            "format": (image_format := device.key_image_format())["format"],
            "key_width": key_width,
            "key_height": key_height,
            "flip_horizontal": flip_h,
            "flip_vertical": flip_v,
            "rotation": image_format["rotation"],
        }

    @classmethod
    def open_deck(cls, device):
        try:
            device.open()
        except Exception:
            return
        device.is_fake = False
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
            serial = None
        else:
            if serial:
                cls.open_decks.pop(serial, None)
        logger.debug(f"[DECK {serial or deck.id() if hasattr(deck, 'id') else '???'}] Connection closed")
        sleep(0.05)  # https://github.com/abcminiuser/python-elgato-streamdeck/issues/68

    @classmethod
    def close_opened_decks(cls):
        if cls.open_decks:
            for serial, deck in list(cls.open_decks.items()):
                cls.close_deck(deck)

    @classmethod
    def get_deck(cls, serial):
        if isinstance(serial, (tuple, list)):
            if len(serial) > 1:
                return cls.exit(1, f'Invalid serial "{" ".join(serial)}".')
            serial = serial[0]
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
    def read_deck_model(cls, directory):
        return (Path(directory) / MODEL_FILE_NAME).read_text().split(":")

    @classmethod
    def write_deck_model(cls, directory, device_info):
        model_path = directory / MODEL_FILE_NAME
        device_class = device_info["class"]
        parts = [device_class.__name__]
        if device_class is FakeStreamDeckWeb:
            parts = ["StreamDeckWeb", str(device_info["nb_rows"]), str(device_info["nb_cols"])]
        else:
            parts = [device_class.__name__]
        try:
            if parts == cls.read_deck_model(directory):
                return
        except Exception:
            pass
        model_path.write_text(":".join(parts))

    @classmethod
    def get_info_from_model_file(cls, directory):
        parts = cls.read_deck_model(directory)
        device_class_name = parts[0]
        args = {}
        if device_class_name == "StreamDeckWeb":
            args["nb_rows"] = int(parts[1])
            args["nb_cols"] = int(parts[2])
        fake_device = cls.get_fake_device(cls.get_device_class(device_class_name), **args)
        fake_device.info = cls.get_device_info(fake_device)
        return fake_device.info

    @classmethod
    def start_render_thread(cls, deck):
        queue = SimpleQueue()
        thread = threading.Thread(
            name="ImgRenderer",
            target=Manager.render_deck_images,
            args=(deck.serial, deck.device, queue, cls.to_web_queue),
        )
        thread.start()
        cls.render_queues[deck.serial] = queue
        return queue, thread

    @classmethod
    def stop_render_thread(cls, deck):
        del cls.render_queues[deck.serial]
        deck.render_images_queue.put(None)
        deck.render_images_thread.join(0.5)

    @staticmethod
    def render_deck_images(serial, deck, queue, web_queue):
        set_thread_name("ImgRenderer")
        delay = RENDER_IMAGE_DELAY
        future_margin = RENDER_IMAGE_DELAY / 10
        timeout = None
        images = {}
        sent_images = {}

        def get_ordered():
            return sorted((ts, index) for index, (ts, key, image) in images.items())

        def extract_ready(ordered):
            if not ordered:
                return []
            limit = time() + future_margin
            return [(index, images.pop(index)) for ts, index in ordered if ts < limit]

        def web_queue_one(key, image, extra=None):
            if web_queue is None:
                return
            try:
                web_queue.sync_put(
                    {
                        "event": "deck.key.updated",
                        "serial": serial,
                        "key": key,
                        "image": image,
                    }
                    | (extra or {})
                )
            except Exception:
                pass

        def web_queue_all(extra):
            for index, (key, image) in sorted(sent_images.items()):
                web_queue_one(key, image, extra)

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
                    for index, (ts, key, image) in ready:
                        try:
                            deck.set_key_image(index, image)
                        except TransportError:
                            queue.put(None)
                            break
                        sent_images[index] = (key, image)
                        if web_queue is not None:
                            web_queue_one(key, image)
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
                if work[0] == WEB_QUEUE_ALL_IMAGES:
                    # we were asked to send all to the web queue
                    web_queue_all(work[1])
                    continue
                # we have some work: we received a new image to queue
                index, key, image = work
                images[index] = (time() + delay, key, image)
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
        if not process_info["quiet"]:
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
    def start_process(
        cls,
        command,
        register_stop=False,
        detach=False,
        shell=False,
        done_event=None,
        env=None,
        working_dir=None,
        quiet=False,
    ):
        if done_event is not None:
            done_event.clear()
        if not cls.processes_checker_thread:
            cls.start_processes_checker()

        if logger.level <= logging.DEBUG:
            quiet = False

        base_str = f'[PROCESS] Launching `{command}`{" (in detached mode)" if detach else ""}'
        logger.debug(f"{base_str}...")
        try:
            process = psutil.Popen(
                command,
                start_new_session=bool(detach),
                shell=bool(shell),
                env=(os.environ | env) if env else None,
                stderr=None if logger.level <= logging.DEBUG else DEVNULL,
                cwd=working_dir,
            )
            cls.processes[process.pid] = {
                "pid": process.pid,
                "command": command,
                "process": process,
                "to_stop": bool(register_stop),
                "detached": detach,
                "done_event": done_event,
                "quiet": quiet,
            }
            if not quiet:
                logger.info(f"{base_str} [ok PID={process.pid}]")
            return None if detach else process.pid
        except Exception:
            logger.error(f"{base_str} [failed]", exc_info=logger.level <= logging.DEBUG)
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
            if not process_info["quiet"]:
                logger.info(f"{base_str} [done]")
        cls.check_process_running(pid, process_info)  # to update the `done_event`

    @classmethod
    def get_executable(cls):
        executable = sys.argv[0]
        if executable.endswith(f"{LIBRARY_NAME}/__main__.py"):
            executable = f"{sys.executable} -m {LIBRARY_NAME}"
        return executable

    @classmethod
    def start_web_server(cls, host, port, ssl_cert, ssl_key, password):
        from .web import start_web_thread

        ssl_context = None
        if ssl_cert:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            ssl_context.load_cert_chain(ssl_cert, ssl_key)
        cls.to_web_queue, cls.from_web_queue, cls._stop_web_thread = start_web_thread(
            host, port, ssl_context, password
        )

    @classmethod
    def end_web_server(cls):
        cls._stop_web_thread()
        cls.to_web_queue = cls.from_web_queue = None

    @classmethod
    def on_deck_started(cls, deck, client_id=None):
        cls.started_decks[deck.serial] = deck
        cls.on_deack_ready(deck.serial)

    @classmethod
    def on_deack_ready(cls, serial, client_id=None):
        if cls.to_web_queue is None:
            return
        deck = cls.started_decks[serial]
        cls.to_web_queue.sync_put(
            {
                "event": "deck.started",
                "serial": deck.serial,
                "client_id": client_id,
                "deck": {
                    "model": deck.model,
                    "model_human": (parts := deck.model.split("Deck"))[0] + "Deck " + parts[1],
                    "plugged": deck.plugged,
                    "serial": deck.serial,
                    "nb_cols": deck.nb_cols,
                    "nb_rows": deck.nb_rows,
                    "image_format": deck.image_format,
                    "key_width": deck.key_width,
                    "key_height": deck.key_height,
                    "flip_horizontal": deck.flip_horizontal,
                    "flip_vertical": deck.flip_vertical,
                    "rotation": deck.rotation,
                },
            }
        )

    @classmethod
    def on_deck_stopped(cls, serial, client_id=None):
        if cls.to_web_queue is None:
            return
        cls.to_web_queue.sync_put(
            {
                "event": "deck.stopped",
                "serial": serial,
                "client_id": client_id,
            }
        )
        cls.started_decks.pop(serial, None)

    @classmethod
    def on_key_pressed(cls, key):
        if cls.to_web_queue is None:
            return
        cls.to_web_queue.sync_put(
            {
                "event": "deck.key.pressed",
                "serial": key.deck.serial,
                "key": key.key,
            }
        )

    @classmethod
    def on_key_released(cls, key):
        if cls.to_web_queue is None:
            return
        cls.to_web_queue.sync_put(
            {
                "event": "deck.key.released",
                "serial": key.deck.serial,
                "key": key.key,
            }
        )

    @classmethod
    def on_web_ready(cls, client_id, serial):
        if serial:
            if not (render_queue := cls.render_queues.get(serial)):
                return
            render_queue.put((WEB_QUEUE_ALL_IMAGES, {"client_id": client_id}))
        else:
            for deck in cls.started_decks.values():
                cls.on_deack_ready(deck.serial, client_id)

    @classmethod
    def on_web_key_pressed(cls, serial, key):
        if not (deck := cls.started_decks.get(serial)):
            return
        deck.on_key_pressed(None, deck.key_to_index(key), True)

    @classmethod
    def on_web_key_released(cls, serial, key):
        if not (deck := cls.started_decks.get(serial)):
            return
        deck.on_key_pressed(None, deck.key_to_index(key), False)
