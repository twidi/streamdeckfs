#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
from pathlib import Path
from time import time

from ..common import file_flags, logger
from ..threads import set_thread_name


class WatchedDirectory:

    by_directories = {}
    by_watch_ids = {}

    @staticmethod
    def normalize_directory(directory):
        if not isinstance(directory, Path):
            directory = Path(directory)
        return directory

    @classmethod
    def add(cls, files_watcher, directory, watcher=None):
        if not (watched := cls.get_by_directory(directory)):
            watched = cls(files_watcher, directory)
            cls.by_directories[watched.directory] = watched
            if (parent_directory := watched.directory.parent) != directory:
                watched.parent = watched.add(files_watcher, parent_directory)
                watched.parent.children.append(watched)
            else:
                watched.parent = None
        if watcher:
            watched.add_watcher(watcher)
        else:
            watched.update_watch()
        return watched

    @classmethod
    def remove(cls, directory, watcher):
        if not (watched := cls.get_by_directory(directory)):
            return
        watched.remove_watcher(watcher)
        watched.update_watch()

    @classmethod
    def get_by_directory(cls, directory):
        return cls.by_directories.get(cls.normalize_directory(directory))

    @classmethod
    def get_by_watch_id(cls, watch_id):
        return cls.by_watch_ids.get(watch_id)

    def __init__(self, files_watcher, directory):
        self.files_watcher = files_watcher
        self.directory = self.normalize_directory(directory)
        self.name = self.directory.name
        self.parent = None
        self.children = []
        self.watchers = []
        self.watch_mode = None
        self.watch_id = None
        self.exists = self.directory_exists()

    def __str__(self):
        return f"Watched directory: {self.directory} ; Exists={self.directory_exists()} ({self.exists}) ; Mode={self.get_mode()} ({self.watch_mode} ; WatchId={self.watch_id})"

    def __repr__(self):
        return f'<WatchedDirectory "{self.directory}">'

    def directory_exists(self):
        return self.directory.exists() and self.directory.is_dir()

    @property
    def waiting(self):
        return not self.exists

    def add_watcher(self, watcher):
        if watcher not in self.watchers:
            self.watchers.append(watcher)
            self.update_watch()

    def remove_watcher(self, watcher):
        try:
            self.watchers.remove(watcher)
        except ValueError:
            pass
        else:
            self.update_watch()

    def has_waiting_children(self):
        return any(child.waiting for child in self.children)

    def has_descendant_watchers(self):
        return any(bool(child.watchers) or child.has_descendant_watchers() for child in self.children)

    def get_mode(self):
        if not self.watchers and not self.has_descendant_watchers():
            # if their is noone waiting for us we can stop watching the directory
            return None
        if self.waiting:
            # if the directory does not exist, we are in waiting mode, ie not watched but the parent will be
            # in "all" watch mode to know when the directory is created
            return "waiting"
        if self.watchers:
            # if we have direct watchers, we watch for content and self-deletion
            return "all"
        if self.has_waiting_children():
            # if we have no direct watchers but have direct children in waiting mode (ie their directory does not exist)
            # we watch for content and self-deletion
            return "all"
        # if we have no direct watchers and no direct children in waiting mode, we only watch for self-deletion
        return "self_delete"

    def update_watch(self):
        self.exists = self.directory_exists()
        if (watch_mode := self.get_mode()) == self.watch_mode:
            return
        self.watch_mode = watch_mode
        if self.watch_mode in ("waiting", None):
            if self.files_watcher and self.watch_id:
                self.files_watcher.remove_watch(self.watch_id)
        else:
            if self.files_watcher:
                self.watch_id = self.files_watcher.set_watch(self.directory, self.watch_mode)

        if self.parent:
            self.parent.update_watch()

    @classmethod
    def on_watch_set(cls, directory, watch_id):
        if not (watched := cls.get_by_directory(directory)):
            return
        watched.watch_id = watch_id
        cls.by_watch_ids[watch_id] = watched

    @classmethod
    def on_watch_removed(cls, watch_id):
        if not (watched := cls.get_by_watch_id(watch_id)):
            return
        cls.by_watch_ids.pop(watched.watch_id, None)
        watched.watch_id = None

    def on_file_added(self, name):
        if not self.watchers:
            return
        for watcher in self.watchers:
            watcher.on_file_change(self.directory, name, file_flags.CREATE, time())

    def on_file_removed(self, name):
        if not self.watchers:
            return
        for watcher in self.watchers:
            watcher.on_file_change(self.directory, name, file_flags.DELETE, time())

    def on_file_changed(self, name):
        if not self.watchers:
            return
        for watcher in self.watchers:
            watcher.on_file_change(self.directory, name, file_flags.MODIFY, time())

    def get_child(self, name):
        try:
            return next(child for child in self.children if child.name == name)
        except StopIteration:
            return None

    def on_directory_added(self, name):
        if child := self.get_child(name):
            child.update_watch()
            for grand_child in child.iter_all_children():
                grand_child.update_watch()
        for watcher in self.watchers:
            watcher.on_file_change(self.directory, name, file_flags.CREATE | file_flags.ISDIR, time())

    def on_directory_removed(self, name):
        if child := self.get_child(name):
            child.update_watch()
            for grand_child in child.iter_all_children():
                grand_child.update_watch()
        for watcher in self.watchers:
            watcher.on_file_change(self.directory, name, file_flags.DELETE | file_flags.ISDIR, time())

    def iter_all_children(self):
        """Yields all children from the current object and its children, starting by the most far in the tree"""
        for child in self.children:
            yield from child.iter_all_children()
            yield child

    def iter_all_watchers(self):
        """Yields all (object, watchers) from the current object and its children, starting by the most far in the tree"""
        for child in self.iter_all_children():
            for watcher in child.watchers:
                yield child, watcher
        for watcher in self.watchers:
            yield self, watcher

    def on_self_directory_removed(self, directory=None):
        if not directory:
            directory = self.directory
        for children in self.iter_all_children():
            children.on_self_directory_removed(directory)
        self.update_watch()
        for watched, watcher in self.iter_all_watchers():
            watcher.on_directory_removed(directory)


class BaseFilesWatcher:
    WatchedDirectory = WatchedDirectory
    thread_name = "FilesWatcher"

    def __init__(self):
        self.running = False

    def set_watch(self, directory, watch_mode):
        try:
            watch_id = self._set_watch(directory, watch_mode)
        except Exception as exc:
            if logger.level <= logging.DEBUG:
                logger.exception(
                    f'[{self.thread_name}] Could not watch directory "{directory}" in mode "{watch_mode}": {exc}'
                )
        else:
            self.WatchedDirectory.on_watch_set(directory, watch_id)

    def _set_watch(self, directory, watch_mode):
        # must return a watch id
        raise NotImplementedError

    def remove_watch(self, watch_id):
        try:
            self._remove_watch(watch_id)
        except Exception as exc:
            if logger.level <= logging.DEBUG:
                logger.exception(f'[{self.thread_name}] Could not remove watch "{watch_id}": {exc}')
        else:
            self.WatchedDirectory.on_watch_removed(watch_id)

    def _remove_watch(self, watch_id):
        raise NotImplementedError

    def stop(self):
        self.running = False

    def stopped(self):
        return not self.running

    def run(self):
        set_thread_name(self.thread_name)
        self.running = True
        while True:
            if self.stopped():
                break
            for event in self.iter_events():
                if self.stopped():
                    break
                watch_id = self.get_event_watch_id(event)
                if not (watched := self.WatchedDirectory.get_by_watch_id(watch_id)):
                    continue
                name = self.get_event_watch_name(event)
                if self.is_event_self_removed(event):
                    watched.on_self_directory_removed()
                elif self.is_event_directory_added(event):
                    watched.on_directory_added(name)
                elif self.is_event_directory_removed(event):
                    watched.on_directory_removed(name)
                elif self.is_file_added(event):
                    watched.on_file_added(name)
                elif self.is_file_removed(event):
                    watched.on_file_removed(name)
                elif self.is_file_changed(event):
                    watched.on_file_changed(name)

    def iter_events(self):
        raise NotImplementedError

    def get_event_watch_id(self, event):
        raise NotImplementedError

    def get_event_watch_name(self, event):
        raise NotImplementedError

    def is_event_self_removed(sellf, event):
        raise NotImplementedError

    def is_event_directory_added(self, event):
        raise NotImplementedError

    def is_event_directory_removed(self, event):
        raise NotImplementedError

    def is_file_added(self, event):
        raise NotImplementedError

    def is_file_removed(self, event):
        raise NotImplementedError

    def is_file_changed(self, event):
        raise NotImplementedError
