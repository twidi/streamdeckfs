#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import json
import logging
import threading
from dataclasses import dataclass
from queue import SimpleQueue

from cached_property import cached_property
from StreamDeck.Devices.StreamDeck import StreamDeck

from ..common import DEFAULT_BRIGHTNESS, Manager, file_flags, logger
from .base import FILTER_DENY, NOT_HANDLED, Entity, EntityDir, versions_dict_factory


@dataclass(eq=False)
class Deck(EntityDir):
    current_page_file_name = ".current_page"
    set_current_page_file_name = ".set_current_page"
    current_brightness_file_name = ".current_brightness"

    device: StreamDeck
    scroll_activated: bool

    @cached_property
    def event_class(self):
        from . import DeckEvent

        return DeckEvent

    @cached_property
    def var_class(self):
        from . import DeckVar

        return DeckVar

    def __post_init__(self):
        super().__post_init__()
        if self.device:
            self.model = self.device.info["class"].__name__
            self.serial = self.device.info["serial"]
            self.nb_cols = self.device.info["cols"]
            self.nb_rows = self.device.info["rows"]
            self.key_width = self.device.info["key_width"]
            self.key_height = self.device.info["key_height"]
        else:
            self.serial = None
            try:
                info = Manager.get_info_from_model_file(self.path)
                self.model = info["model"]
                self.nb_rows = info["nb_rows"]
                self.nb_cols = info["nb_cols"]
                self.key_width = info["key_width"]
                self.key_height = info["key_height"]
            except Exception:
                from traceback import print_exc

                print_exc()
                Manager.exit(1, 'Cannot guess model, please run the "make-dirs" command.')
        self.nb_keys = self.nb_rows * self.nb_cols
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
        self.current_brightness_state_file = self.path / self.current_brightness_file_name

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

    def read_directory(self):
        super().read_directory()
        if self.filters.get("page") != FILTER_DENY:
            from .page import Page

            for page_dir in sorted(self.path.glob(Page.path_glob)):
                self.on_file_change(
                    self.path, page_dir.name, file_flags.CREATE | (file_flags.ISDIR if page_dir.is_dir() else 0)
                )

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

        if name == self.current_brightness_file_name:
            self.set_brightness_from_file()
            return None

        if (result := super().on_file_change(directory, name, flags, modified_at, entity_class)) is not NOT_HANDLED:
            return result

        if (page_filter := self.filters.get("page")) != FILTER_DENY:
            from .page import Page

            if not entity_class or entity_class is Page:
                if (parsed := Page.parse_filename(name, self)).main:
                    if page_filter is not None and not Page.args_matching_filter(
                        parsed.main, parsed.args, page_filter
                    ):
                        return None
                    return self.on_child_entity_change(
                        path=path,
                        flags=flags,
                        entity_class=Page,
                        data_identifier=parsed.main["page"],
                        args=parsed.args,
                        ref_conf=parsed.ref_conf,
                        ref=parsed.ref,
                        used_vars=parsed.used_vars,
                        modified_at=modified_at,
                    )

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
            if page := self.pages.get(page_num):
                break
        return page, transparent

    def go_to_page(self, page_ref, quiet=False):
        try:
            self._go_to_page(page_ref, quiet=quiet)
        finally:
            self.write_current_page_info()

    def _go_to_page(self, page_ref, quiet=False):
        from .page import BACK, FIRST, NEXT, PREVIOUS

        logger.debug(f"[{self}] Asking to go to page {page_ref} (current={self.current_page_number})")

        if page_ref is None:
            return

        current = (self.current_page_number, self.current_page_is_transparent)

        if isinstance(page_ref, int):
            if page_ref == current[0]:
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

        if page.number == current[0]:
            return
        transparent = page.overlay

        if page.number in self.visible_pages and page_ref != BACK:
            logger.error(f"[{self}] Page [{page.str}] is already opened")
            return

        if current_page := self.current_page:
            if page_ref == BACK:
                if not quiet:
                    if self.current_page_is_transparent:
                        logger.info(
                            f'[{self}] Closing overlay for page [{current_page.str}], going back to {"overlay " if transparent else ""}[{page.str}]'
                        )
                    else:
                        logger.info(
                            f'[{self}] Going back to {"overlay " if transparent else ""}[{page.str}] from [{current_page.str}]'
                        )
                current_page.unrender(
                    clear_images=False  # the render of the new page will clear keys needing to be cleared
                )
            elif transparent:
                if not quiet:
                    logger.info(f"[{self}] Adding [{page.str}] as an overlay over [{current_page.str}]")
            else:
                if not quiet:
                    logger.info(f"[{self}] Changing current page from [{current_page.str}] to [{page.str}]")
                for page_number in self.visible_pages:
                    if visible_page := self.pages.get(page_number):
                        visible_page.unrender(
                            clear_images=False  # the render of the new page will clear keys needing to be cleared
                        )
        else:
            if not quiet:
                logger.info(f"[{self}] Setting current page to [{page.str}]")

        self.append_to_history(page, transparent)
        page.render(render_above=False, render_below=True)

    def write_current_page_info(self):
        page = self.current_page if self.current_page_number else None
        page_info = {
            "number": self.current_page_number,
            "name": page.name if page and page.name != self.unnamed else None,
            "is_overlay": self.current_page_is_transparent if self.current_page_number else None,
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
            page_ref = self.set_current_page_state_file.read_text().strip()
            self.go_to_page(page_ref)
        except Exception:
            pass
        try:
            self.set_current_page_state_file.unlink()
        except Exception:
            pass

    def write_current_brightness_info(self):
        if self.brightness != self.read_current_brightness_info():
            try:
                self.current_brightness_state_file.write_text(str(self.brightness))
            except Exception:
                pass

    def read_current_brightness_info(self):
        try:
            return int(self.current_brightness_state_file.read_text().strip())
        except Exception:
            return None

    def set_brightness_from_file(self, minimum=0):
        try:
            if not self.current_brightness_state_file.exists():
                raise FileNotFoundError
            if (brightness := self.read_current_brightness_info()) is None:
                raise ValueError
            self.set_brightness("=", brightness)
        except Exception:
            self.write_current_brightness_info()
        if self.brightness < minimum:
            self.set_brightness("=", minimum)

    def is_page_visible(self, page_or_number):
        number = page_or_number if isinstance(page_or_number, int) else page_or_number.number
        return self.current_page_number == number or number in self.visible_pages

    def get_page_overlay_level(self, page_or_number):
        number = page_or_number if isinstance(page_or_number, int) else page_or_number.number
        if number < 1:
            return None, None
        for level, page_number in enumerate(self.visible_pages):
            if page_number == number:
                return level, level != len(self.visible_pages) - 1
        return None, None

    def get_page_below(self, page_or_number):
        level, transparent = self.get_page_overlay_level(page_or_number)
        if not transparent:
            return None
        nb_visible = len(self.visible_pages)
        page = None
        while level < nb_visible - 1 and not (page := self.pages.get(self.visible_pages[level + 1])):
            level += 1
        return page

    def get_page_above(self, page_or_number):
        level, transparent = self.get_page_overlay_level(page_or_number)
        page = None
        while level and not (page := self.pages.get(self.visible_pages[level - 1])):
            level -= 1
        return page

    def get_page_key(self, page_number, key_row_col):
        if not (page := self.pages.get(page_number)):
            return None
        return page.keys.get(key_row_col)

    def get_key_visibility(self, page_number, key):
        # Returns
        # 0. bool: if the key if visible
        # 1. int: key level (None if page not visible)
        # 2. key: key on page below (None if page not visible or key not visible)
        # 3. key: key on page above (None if page not visible or key visible)
        if not (page := self.pages.get(page_number)) or not page.is_visible:
            return False, None, None, None

        key_level = None

        for level, current_page_number in enumerate(self.visible_pages):
            if current_page_number == page_number:
                key_level = level
            else:
                page_key = self.get_page_key(current_page_number, key)
                if not page_key:
                    continue
                if key_level is None:
                    # we are still above the key
                    if page_key.has_content():
                        # a key above ours is visible, so ours is not
                        return False, None, None, page_key
                else:
                    # we are below the key
                    if page_key.has_content():
                        # we found a visible key below ours, we don't need to dig more
                        return True, key_level, page_key, None

        return True, key_level, None, None

    @property
    def current_page(self):
        return self.pages.get(self.current_page_number) or None

    def on_key_pressed(self, deck, index, pressed):
        row, col = row_col = self.index_to_key(index)

        if pressed:
            if self.pressed_key:
                logger.warning("Multiple press is not supported yet. Press ignored.")
                return

            if not (page := self.current_page):
                logger.debug(f"[{self}, KEY ({row}, {col})] PRESSED. IGNORED (no current page)")
                return

            if not (key := page.keys[row_col]):
                logger.debug(f"[{page}, KEY ({row}, {col})] PRESSED. IGNORED (key not configured)")
                return

            self.pressed_key = key
            key.pressed()

        else:
            if not self.pressed_key or self.pressed_key.key != row_col:
                return
            pressed_key, self.pressed_key = self.pressed_key, None
            pressed_key.released()

    def set_brightness(self, operation, level, quiet=False):
        old_brightness = self.brightness
        if operation == "=":
            self.brightness = level
        elif operation == "+":
            self.brightness = min(100, old_brightness + level)
        elif operation == "-":
            self.brightness = max(0, old_brightness - level)
        if self.brightness == old_brightness:
            return
        if not quiet:
            logger.info(f"[{self}] Changing brightness from {old_brightness} to {self.brightness}")
        self.device.set_brightness(self.brightness)
        self.write_current_brightness_info()

    def render(self):
        from .page import FIRST

        self.set_brightness_from_file(minimum=5)
        self.is_running = True
        self.device.set_key_callback(self.on_key_pressed)
        self.activate_events()
        self.go_to_page(
            FIRST  # always display the first page first even if we'll load another one in `set_page_from_file`
        )
        self.set_page_from_file()

    def unrender(self):
        for page_number in self.visible_pages:
            if page := self.pages.get(page_number):
                page.unrender()
        self.deactivate_events()
        for file in (self.current_page_state_file, self.set_current_page_state_file):
            try:
                file.unlink()
            except Exception:
                pass
        if self.render_images_thread is not None:
            self.render_images_queue.put(None)
            self.render_images_thread.join(0.5)
            self.render_images_thread = self.render_images_queue = None
        self.is_running = False

    def set_image(self, row, col, image):
        if self.render_images_thread is None:
            self.render_images_queue = SimpleQueue()
            self.render_images_thread = threading.Thread(
                name="ImgRenderer", target=Manager.render_deck_images, args=(self.device, self.render_images_queue)
            )
            self.render_images_thread.start()

        self.render_images_queue.put((self.key_to_index(row, col), image))

    def remove_image(self, row, col):
        self.set_image(row, col, None)

    def find_page(self, page_filter, allow_disabled=False):
        from .page import Page

        return Page.find_by_identifier_or_name(self.pages, page_filter, int, allow_disabled=allow_disabled)

    @cached_property
    def env_vars(self):
        return self.finalize_env_vars(
            {
                "executable": Manager.get_executable(),
                "device_type": self.model,
                "device_serial": self.serial,
                "device_directory": self.path,
                "device_nb_rows": self.nb_rows,
                "device_nb_cols": self.nb_cols,
                "device_key_width": self.key_width,
                "device_key_height": self.key_height,
                "device_brightness": self.brightness,
                "verbosity": logging.getLevelName(logger.level),
            }
        )

    def iterate_vars_holders(self):
        yield from super().iterate_vars_holders()
        for page in self.iter_all_children_versions(self.pages):
            yield page
            yield from page.iterate_vars_holders()


@dataclass(eq=False)
class DeckContent(Entity):
    parent_attr = "deck"
    deck: "Deck"
