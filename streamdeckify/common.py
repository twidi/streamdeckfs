#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of Streamdeckify
# (see https://github.com/twidi/streamdeckify).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
import os
import platform
import psutil
import signal
import threading
from pathlib import Path
from queue import Empty
from time import sleep, time

import click_log
from inotify_simple import flags as file_flags  # noqa: F401
from StreamDeck.DeviceManager import DeviceManager

from .threads import Repeater, set_thread_name


SUPPORTED_PLATFORMS = {
    'Linux': True,
    'Darwin': False,
    'Windows': False,
}
PLATFORM = platform.system()

logger = logging.getLogger('streamdeckify')
click_log.basic_config(logger)


ASSETS_PATH = Path.resolve(Path(__file__)).parent / 'assets'

DEFAULT_BRIGHTNESS = 30

RENDER_IMAGE_DELAY = 0.02


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
    def get_decks(cls, limit_to_serial=None):
        if not cls.decks:
            for deck in cls.get_manager().enumerate():
                try:
                    deck.open()
                except Exception:
                    logger.warning(f'Stream Deck "{deck.deck_type()}" (ID {deck.id()}) cannot be accessed. Maybe a program is already connected to it.')
                    continue
                deck.reset()
                serial = deck.get_serial_number()
                if limit_to_serial and limit_to_serial != serial:
                    deck.close()
                    continue
                deck.set_brightness(DEFAULT_BRIGHTNESS)
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
            Manager.exit(1, 'No available Stream Deck. Aborting.')
        return cls.decks

    @classmethod
    def get_deck(cls, serial):
        if len(serial) > 1:
            return cls.exit(1, f'Invalid serial "{" ".join(serial)}".')
        serial = serial[0] if serial else None
        decks = cls.get_decks(limit_to_serial=serial)
        if not serial:
            if len(decks) > 1:
                return cls.exit(1, f'{len(decks)} Stream Decks detected, you need to specify the serial. Use the "inspect" command to list all available decks.')
            return list(decks.values())[0]
        if serial not in decks:
            return cls.exit(1, f'No Stream Deck found with the serial "{serial}". Use the "inspect" command to list all available decks.')
        return decks[serial]

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

            sleep(0.01)  # needed to avoid error!!

        exit(status)

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
                logger.info(f'[PROCESS] `{process_info["command"]}`{" (launched in detached mode)" if process_info["detached"] else ""} ended [PID={pid}; ReturnCode={return_code}]')
                cls.processes.pop(pid, None)
                if (event := process_info.get('done_event')):
                    event.set()

    @classmethod
    def start_processes_checker(cls):
        if cls.processes_checker_thread:
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
    def start_process(cls, command, register_stop=False, detach=False, shell=False, done_event=None):
        if done_event is not None:
            done_event.clear()
        if not cls.processes_checker_thread:
            cls.start_processes_checker()

        base_str = f'[PROCESS] Launching `{command}`{" (in detached mode)" if detach else ""}'
        logger.info(f'{base_str}...')
        try:
            process = psutil.Popen(command, start_new_session=bool(detach), shell=bool(shell))
            cls.processes[process.pid] = {
                'pid': process.pid,
                'command': command,
                'process': process,
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
        base_str = f"[PROCESS {pid}] Terminating `{process_info['command']}`"
        logger.info(f'{base_str}...')
        gone, alive = cls.kill_proc_tree(pid, timeout=5)
        if alive:
            # TODO: handle the remaining processes
            logger.error(f'{base_str} [FAIL: still running: {" ".join([p.pid for p in alive])} ]')
        else:
            logger.info(f'{base_str} [done]')
