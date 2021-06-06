#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of Streamdeckify
# (see https://github.com/twidi/streamdeckify).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import signal
import threading

import click

from ..common import Manager, logger
from ..entities import Deck
from .base import cli, common_options


@cli.command()
@common_options['optional_serials']
@click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=True))
@click.option('--scroll/--no-scroll', default=True, help='If scroll in keys is activated. Default to true.')
@common_options['verbosity']
def run(serials, directory, scroll):
    """Run, Forrest, Run!

    Arguments:

    SERIALS: Serial number(s) of the Stream Deck(s) to handle.
    Optional if only one Stream Deck, or if the given DIRECTORY is the configuration of a Stream Deck, or if all Stream Deck having a configuration directory inside DIRECTORY must be run. \n
    DIRECTORY: Path of the directory containing configuration directories for the Stream Decks to run, or the final configuration directory if only one to run.
    """

    if not serials:
        decks = Manager.get_decks()
    else:
        decks = Manager.get_decks(limit_to_serials=serials, exit_if_none=False)
        for serial in serials:
            if serial not in decks:
                logger.warning(f'[DECK {serial}] No Stream Deck found with the serial "{serial}". Maybe a program is already connected to it.')
        if not decks:
            Manager.exit(1, 'No available Stream Deck found with the requested serials.')

    devices = []
    for serial, deck in decks.items():
        deck_directory = Manager.normalize_deck_directory(directory, serial)
        if not deck_directory.exists() or not deck_directory.is_dir():
            logger.warning(f"[DECK {serial}] {deck_directory} does not exist or is not a directory")
            Manager.close_deck(deck)
            continue
        devices.append((deck, serial, deck_directory))

    if not devices:
        Manager.exit(1, 'No Stream Deck found with configuration directory.')

    Manager.start_files_watcher()

    decks = []
    for device, serial, directory in devices:
        logger.info(f'[DECK {serial}] Running in directory "{directory}"')
        deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=serial, disabled=False, device=device, scroll_activated=scroll)
        Manager.write_deck_model(directory, device.info['class'])
        deck.on_create()
        deck.run()
        decks.append(deck)

    def end(signum, frame):
        logger.info(f'Ending ({signal.strsignal(signum)})...')
        signal.signal(signal.SIGTERM, sigterm_handler)
        signal.signal(signal.SIGINT, sigint_handler)
        for deck in decks:
            deck.end_event.set()

    sigterm_handler = signal.getsignal(signal.SIGTERM)
    sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)

    for deck in decks:
        deck.end_event.wait()

    exit_code = None
    if all(deck.directory_removed for deck in decks):
        exit_code = 1
        if len(decks) > 1:
            logger.critical('All configuration directories were removed. Ending.')

    Manager.end_files_watcher()
    Manager.end_processes_checker()

    for deck in decks:
        deck.unrender()
        Manager.close_deck(deck.device)

    main_thread = threading.currentThread()
    for t in threading.enumerate():
        if t is not main_thread and t.is_alive():
            t.join()

    if exit_code:
        exit(exit_code)
