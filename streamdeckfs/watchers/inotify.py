#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
from inotify_simple import INotify
from inotify_simple import flags as f

from ..common import logger
from .base import BaseFilesWatcher


class InotifyFilesWatcher(BaseFilesWatcher):
    def __init__(self):
        super().__init__()
        self.inotify = INotify()
        self.mapping = {}  # only used to display directories in debug mode

    flag_groups = {
        "self_delete": f.DELETE_SELF | f.MOVE_SELF | f.UNMOUNT,
        "all": f.CREATE | f.DELETE | f.MODIFY | f.MOVED_FROM | f.MOVED_TO | f.DELETE_SELF | f.MOVE_SELF | f.UNMOUNT,
        "added": f.CREATE | f.MOVED_TO,
        "removed": f.DELETE | f.MOVED_FROM,
        "changed": f.MODIFY,
    }

    def _set_watch(self, directory, watch_mode):
        watch_id = self.inotify.add_watch(directory, self.flag_groups[watch_mode])
        self.mapping[watch_id] = directory
        return watch_id

    def _remove_watch(self, watch_id):
        try:
            self.inotify.rm_watch(watch_id)
        except OSError:
            # the watch is already removed from the kernel, maybe because the directory was deleted
            pass
        self.mapping.pop(watch_id, None)

    def stop(self):
        super().stop()
        if self.inotify:
            self.inotify.close()

    def stopped(self):
        return super().stopped() or self.inotify.closed

    def iter_events(self):
        try:
            for event in self.inotify.read(timeout=500):
                directory = self.mapping.get(event.wd)
                logger.debug(
                    f'{event} ; {directory}/{event.name} ; FLAGS: {", ".join(str(flag) for flag in f.from_mask(event.mask))}'
                )
                if event.mask & f.IGNORED:
                    self.remove_watch(event.wd)
                    continue
                yield event
        except ValueError:
            # happen if read while closed
            pass

    def get_event_watch_id(self, event):
        return event.wd

    def get_event_watch_name(self, event):
        return event.name

    def is_directory_event(self, event):
        return event.mask & f.ISDIR

    def is_event_self_removed(self, event):
        return event.mask & self.flag_groups["self_delete"]

    def is_event_directory_added(self, event):
        return self.is_directory_event(event) and (event.mask & self.flag_groups["added"])

    def is_event_directory_removed(self, event):
        return self.is_directory_event(event) and (event.mask & self.flag_groups["removed"])

    def is_file_added(self, event):
        return not self.is_directory_event(event) and (event.mask & self.flag_groups["added"])

    def is_file_removed(self, event):
        return not self.is_directory_event(event) and (event.mask & self.flag_groups["removed"])

    def is_file_changed(self, event):
        return not self.is_directory_event(event) and (event.mask & self.flag_groups["changed"])
