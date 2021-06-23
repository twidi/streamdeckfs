#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
import signal
import threading
from time import sleep

import click

from ..common import Manager, logger
from ..entities import Deck
from .base import cli, common_options


@cli.command()
@common_options["optional_serials"]
@click.argument("directory", type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=False))
@click.option("--scroll/--no-scroll", default=True, help="If scroll in keys is activated. Default to true.")
@common_options["verbosity"]
def run(serials, directory, scroll):
    """Run, Forrest, Run!

    Arguments:

    SERIALS: Serial number(s) of the Stream Deck(s) to handle.
    Optional if only one Stream Deck, or if the given DIRECTORY is the configuration of a Stream Deck, or if all Stream Deck having a configuration directory inside DIRECTORY must be run. \n
    DIRECTORY: Path of the directory containing configuration directories for the Stream Decks to run, or the final configuration directory if only one to run.
    """

    if serials is None:
        serials = []

    current_decks = {}

    def start_deck(device, serial):
        deck_directory = Manager.normalize_deck_directory(directory, serial)
        if not deck_directory.exists() or not deck_directory.is_dir():
            return
        logger.info(f'[DECK {serial}] Ready to run in directory "{deck_directory}"')
        deck = Deck(
            path=deck_directory,
            path_modified_at=deck_directory.lstat().st_ctime,
            name=serial,
            disabled=False,
            device=device,
            scroll_activated=scroll,
        )
        Manager.write_deck_model(deck_directory, device.info["class"])
        deck.on_create()
        deck.render()
        current_decks[serial] = deck

    def stop_deck(deck, close=True):
        deck.unrender()
        if close:
            Manager.close_deck(deck.device)
        current_decks.pop(deck.serial)

    def check_decks_and_directories():
        # first check that decks are still connected and have their directories
        # else we stop them
        for deck in list(current_decks.values()):
            stop = close = False
            if deck.directory_removed:
                logger.critical(f'[{deck}] Configuration directory "{deck.path}" was removed. Waiting for it...')
                stop = True
            if not deck.device.connected():
                logger.critical(f"[{deck}] Unplugged. Waiting for it...")
                stop = close = True
            if stop:
                stop_deck(deck, close=close)

        # if we have wanted serials not running and not connected, check if they are now connected
        if serials and len(serials) != len(current_decks):
            if missing_serials := [serial for serial in serials if serial not in Manager.open_decks]:
                Manager.get_decks(limit_to_serials=missing_serials, exit_if_none=False)

        # if we didn't specify any serial, check if new decks are now connected
        elif not serials:
            Manager.get_decks(exit_if_none=False)

        # start decks that are not yet started
        for serial, device in Manager.open_decks.items():
            if serial not in current_decks:
                start_deck(device, serial)

    Manager.start_files_watcher()

    end_event = threading.Event()

    def end(signum, frame):
        if logger.level == logging.DEBUG:
            logger.info(f"Ending ({signal.strsignal(signum)})...")
        else:
            logger.info("Ending.")
        signal.signal(signal.SIGTERM, sigterm_handler)
        signal.signal(signal.SIGINT, sigint_handler)
        end_event.set()

    sigterm_handler = signal.getsignal(signal.SIGTERM)
    sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)

    check_decks_and_directories()
    if not len(current_decks):
        logger.warning("Waiting for some decks or directories to be ready...")

    while True:
        if end_event.is_set():
            break
        sleep(1)
        nb_decks = len(current_decks)
        check_decks_and_directories()
        if nb_decks and not len(current_decks):
            logger.warning("No more deck. Waiting for some to be ready...")

    Manager.end_files_watcher()
    Manager.end_processes_checker()

    for deck in list(current_decks.values()):
        stop_deck(deck)

    Manager.close_opened_decks()

    main_thread = threading.currentThread()
    for t in threading.enumerate():
        if t is not main_thread and t.is_alive():
            t.join(0.5)
