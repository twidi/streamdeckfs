#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import json
import threading
from dataclasses import dataclass
from queue import SimpleQueue

from StreamDeck.Devices.StreamDeck import StreamDeck

from ..common import DEFAULT_BRIGHTNESS, Manager, logger, file_flags
from .base import Entity, versions_dict_factory, FILTER_DENY


@dataclass(eq=False)
class Deck(Entity):
    is_dir = True
    current_page_file_name = '.current_page'
    set_current_page_file_name = '.set_current_page'

    device: StreamDeck
    scroll_activated: bool

    def __post_init__(self):
        super().__post_init__()
        if self.device:
            self.serial = self.device.info['serial']
            self.nb_cols = self.device.info['cols']
            self.nb_rows = self.device.info['rows']
            self.key_width = self.device.info['key_width']
            self.key_height = self.device.info['key_height']
        else:
            self.serial = None
            try:
                info = Manager.get_info_from_model_file(self.path)
                self.nb_rows = info['nb_rows']
                self.nb_cols = info['nb_cols']
                self.key_width = info['key_width']
                self.key_height = info['key_height']
            except Exception:
                from traceback import print_exc
                print_exc()
                Manager.exit(1, 'Cannot guess model, please run the "make-dirs" command.')
        self.brightness = DEFAULT_BRIGHTNESS
        self.pages = versions_dict_factory()
        self.current_page_number = None
        self.current_page_is_transparent = False
        self.waiting_images = {}
        self.render_images_thread = None
        self.render_images_queue = None
        self.filters = {}
        self.page_history = []
        self.visible_pages = []
        self.pressed_key = None
        self.is_running = False
        self.directory_removed = False
        self.current_page_state_file = self.path / self.current_page_file_name
        self.set_current_page_state_file = self.path / self.set_current_page_file_name

    @property
    def str(self):
        return f'DECK {self.serial or self.name}{", disabled" if self.disabled else ""}'

    def __str__(self):
        return self.str

    @property
    def deck(self):
        return self

    def key_to_index(self, row, col=None):
        if col is None:  # when key as (row, col) is passed instead of *key
            row, col = row
        return (row - 1) * self.nb_cols + (col - 1)

    def index_to_key(self, index):
        return index // self.nb_cols + 1, index % self.nb_cols + 1

    def on_create(self):
        Manager.add_watch(self.path, self)
        self.read_directory()

    def run(self):
        from .page import FIRST
        self.is_running = True
        self.device.set_key_callback(self.on_key_pressed)
        self.go_to_page(FIRST)  # always display the first page first even if we'll load another one in `set_page_from_file`
        self.set_page_from_file()

    def read_directory(self):
        if self.filters.get('page') != FILTER_DENY:
            from .page import Page
            for page_dir in sorted(self.path.glob(Page.path_glob)):
                self.on_file_change(self.path, page_dir.name, file_flags.CREATE | (file_flags.ISDIR if page_dir.is_dir() else 0))

    def on_file_change(self, directory, name, flags, modified_at=None, entity_class=None):
        if directory != self.path:
            return
        path = self.path / name

        if name == self.current_page_file_name:
            # ensure we are the sole owner of this file
            self.write_current_page_info()
            return None

        if name == self.set_current_page_file_name:
            self.set_page_from_file()
            return None

        if (page_filter := self.filters.get('page')) != FILTER_DENY:
            from .page import Page
            if not entity_class or entity_class is Page:
                ref_conf, ref, main, args = Page.parse_filename(name, self)
                if main:
                    if page_filter is not None and not Page.args_matching_filter(main, args, page_filter):
                        return None
                    return self.on_child_entity_change(path=path, flags=flags, entity_class=Page, data_identifier=main['page'], args=args, ref_conf=ref_conf, ref=ref, modified_at=modified_at)

    def on_directory_removed(self, directory):
        self.directory_removed = True

    def update_visible_pages_stack(self):
        # first page in `visible_pages` is the current one, then the one below, etc...
        # (the last one is the first one not being an overlay, so if the first one is not an overlay, it's the only
        # one in the stack)
        stack = []
        for page_number, transparent in reversed(self.page_history):
            stack.append(page_number)
            if not transparent:
                break
        self.visible_pages = stack
        if not self.visible_pages:
            self.current_page_number = None
            self.current_page_is_transparent = False

    def append_to_history(self, page, transparent=False):
        transparent = bool(transparent)
        page_key = (page.number, transparent)
        if not self.page_history or self.page_history[-1] != page_key:
            self.page_history.append(page_key)
        self.current_page_number = page.number
        self.current_page_is_transparent = transparent
        self.update_visible_pages_stack()

    def pop_from_history(self):
        page = None
        transparent = None
        while True:
            if not self.page_history:
                break
            page_num, transparent = self.page_history.pop()
            if (page_num, transparent) == (self.current_page_number, self.current_page_is_transparent):
                continue
            if (page := self.pages.get(page_num)):
                break
        return page, transparent

    def go_to_page(self, page_ref, transparent=False):
        try:
            self._go_to_page(page_ref, transparent)
        finally:
            self.write_current_page_info()

    def _go_to_page(self, page_ref, transparent=False):
        from .page import FIRST, PREVIOUS, BACK, NEXT
        transparent = bool(transparent)

        logger.debug(f'[{self}] Asking to go to page {page_ref} (current={self.current_page_number})')

        if page_ref is None:
            return

        current = (self.current_page_number, self.current_page_is_transparent)

        if isinstance(page_ref, int):
            if (page_ref, transparent) == current:
                return
            if not (page := self.pages.get(page_ref)):
                return

        elif page_ref == FIRST:
            if not (possible_pages := sorted([(number, page) for number, page in self.pages.items() if page])):
                return
            page = possible_pages[0][1]

        elif page_ref == BACK:
            if len(self.page_history) < 2:
                return

            page, transparent = self.pop_from_history()
            if not page:
                self.update_visible_pages_stack()  # because we may have updated the history
                return

        elif page_ref == PREVIOUS:
            if not self.current_page_number:
                return
            if not (page := self.pages.get(self.current_page_number - 1)):
                return

        elif page_ref == NEXT:
            if not self.current_page_number:
                return
            if not (page := self.pages.get(self.current_page_number + 1)):
                return

        elif not (page := self.find_page(page_ref, allow_disabled=False)):
            return

        if (page.number, transparent) == current:
            return

        if (current_page := self.current_page):
            if page_ref == BACK:
                if current[1]:
                    logger.info(f'[{self}] Closing overlay for page [{current_page.str}], going back to {"overlay " if transparent else ""}[{page.str}]')
                else:
                    logger.info(f'[{self}] Going back to [{page.str}] from [{current_page.str}]')
            elif transparent:
                logger.info(f'[{self}] Adding [{page.str}] as an overlay over [{current_page.str}]')
            else:
                logger.info(f'[{self}] Changing current page from [{current_page.str}] to [{page.str}]')
            if not transparent or page_ref == BACK:
                current_page.unrender()
        else:
            logger.info(f'[{self}] Setting current page to [{page.str}]')

        self.append_to_history(page, transparent)
        page.render(0, self.visible_pages[1:])

    def write_current_page_info(self):
        page = self.current_page if self.current_page_number else None
        page_info = {
            'number': self.current_page_number,
            'name': page.name if page and page.name != self.unnamed else None,
            'is_overlay': self.current_page_is_transparent if self.current_page_number else None,
        }
        if page_info != self.read_current_page_info():
            try:
                self.current_page_state_file.write_text(json.dumps(page_info))
            except Exception:
                pass

    def read_current_page_info(self):
        try:
            return json.loads(self.current_page_state_file.read_text().strip())
        except Exception:
            return None

    def set_page_from_file(self):
        if not self.set_current_page_state_file.exists():
            return
        try:
            page_info = json.loads(self.set_current_page_state_file.read_text().strip())
            if set(page_info.keys()) != {'page', 'is_overlay'}:
                raise ValueError
            if not isinstance(page_ref := page_info['page'], str) or not isinstance(transparent := page_info['is_overlay'], bool):
                raise ValueError
            self.go_to_page(page_ref, transparent)
        except Exception:
            pass
        try:
            self.set_current_page_state_file.unlink()
        except Exception:
            pass

    def is_page_visible(self, page):
        number = page.number
        return self.current_page_number == number or number in self.visible_pages

    def get_page_key(self, page_number, key_row_col):
        if not (page := self.pages.get(page_number)):
            return None
        return page.keys.get(key_row_col)

    def get_key_visibility(self, key):
        # returns visible, key level(if visible), key below(if visible)
        if not key.page.is_visible:
            return False, None, None

        visible_key_below = False
        key_level = None
        key_page_number = key.page.number
        key_row_col = key.key

        for level, page_number in enumerate(self.visible_pages):
            if page_number == key_page_number:
                key_level = level
            else:
                page_key = self.get_page_key(page_number, key_row_col)
                if not page_key:
                    continue
                if key_level is None:
                    # we are still above the key
                    if page_key.has_content():
                        # a key above ours is visible, so ours is not
                        return False, None, None
                else:
                    # we are below the key
                    if page_key.has_content():
                        visible_key_below = page_key
                        break

        return True, key_level, visible_key_below

    @property
    def current_page(self):
        return self.pages.get(self.current_page_number) or None

    def on_key_pressed(self, deck, index, pressed):
        row, col = row_col = self.index_to_key(index)

        if pressed:
            if self.pressed_key:
                logger.warning('Multiple press is not supported yet. Press ignored.')
                return

            if not (page := self.current_page):
                logger.debug(f'[{self}, KEY ({row}, {col})] PRESSED. IGNORED (no current page)')
                return

            if not (key := page.keys[row_col]):
                logger.debug(f'[{page}, KEY ({row}, {col})] PRESSED. IGNORED (key not configured)')
                return

            self.pressed_key = key
            key.pressed()

        else:
            if not self.pressed_key or self.pressed_key.key != row_col:
                return
            pressed_key, self.pressed_key = self.pressed_key, None
            pressed_key.released()

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
        for page_number in self.visible_pages:
            if (page := self.pages.get(page_number)):
                page.unrender()
        for file in (self.current_page_state_file, self.set_current_page_state_file):
            try:
                file.unlink()
            except Exception:
                pass
        if self.render_images_thread is not None:
            self.render_images_queue.put(None)
            self.render_images_thread.join(0.5)
            self.render_images_thread = self.render_images_queue = None

    def set_image(self, row, col, image):
        if self.render_images_thread is None:
            self.render_images_queue = SimpleQueue()
            self.render_images_thread = threading.Thread(name='ImgRenderer', target=Manager.render_deck_images, args=(self.device, self.render_images_queue))
            self.render_images_thread.start()

        self.render_images_queue.put((self.key_to_index(row, col), image))

    def remove_image(self, row, col):
        self.set_image(row, col, None)

    def find_page(self, page_filter, allow_disabled=False):
        from .page import Page
        return Page.find_by_identifier_or_name(self.pages, page_filter, int, allow_disabled=allow_disabled)
