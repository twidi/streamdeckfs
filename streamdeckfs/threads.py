#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import platform
import threading
from time import time

try:
    if platform.system() == "Linux":
        from prctl import set_name as set_thread_name
    else:
        raise ImportError
except ImportError:
    set_thread_name = lambda name: None  # noqa: E731


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
            self.runs_count += 1
            additional_time = 0
            if self.max_runs is not None and self.runs_count >= self.max_runs:
                break
        if self.end_callback:
            self.end_callback(thread=self)
