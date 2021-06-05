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
@common_options['optional_deck']
@click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=True))
@click.option('-p', '--page', type=str, help="Page (number or name) to open first. Default to the first available number.")
@click.option('--scroll/--no-scroll', default=True, help='If scroll in keys is activated. Default to true.')
@common_options['verbosity']
def run(deck, directory, page, scroll):
    """Run, Forrest, Run!"""

    device = Manager.get_deck(deck)
    serial = device.info['serial']
    directory = Manager.normalize_deck_directory(directory, serial)
    if not directory.exists() or not directory.is_dir():
        return Manager.exit(1, f"{directory} does not exist or is not a directory")
    logger.info(f'[DECK {serial}] Running in directory "{directory}"')

    Manager.start_files_watcher()

    deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=serial, disabled=False, device=device, scroll_activated=scroll)
    Manager.write_deck_model(directory, device.info['class'])
    deck.on_create()
    deck.run(page)
    if page and not deck.current_page_number:
        return Manager.exit(1, f'Unable to find page "{page}"')

    def end(signum, frame):
        logger.info(f'Ending ({signal.strsignal(signum)})...')
        signal.signal(signal.SIGTERM, sigterm_handler)
        signal.signal(signal.SIGINT, sigint_handler)
        deck.end_event.set()

    sigterm_handler = signal.getsignal(signal.SIGTERM)
    sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)

    deck.end_event.wait()

    Manager.end_files_watcher()
    Manager.end_processes_checker()

    deck.unrender()

    Manager.close_deck(deck)

    main_thread = threading.currentThread()
    for t in threading.enumerate():
        if t is not main_thread and t.is_alive():
            t.join()

    if deck.end_reason:
        Manager.exit(deck.end_reason[0], deck.end_reason[1])
