#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import logging
import re
import signal
import threading
from pathlib import Path
from time import sleep

import click
import cloup
import cloup.constraints as cons

from ..common import SERIAL_RE, Manager, logger
from ..entities import Deck
from .base import cli, common_options

WEB_HOST_RE = re.compile(
    r"""^(?:
(?P<addr>
    (?P<ipv4>\d{1,3}(?:\.\d{1,3}){3}) |         # IPv4 address
    (?P<fqdn>[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*) # FQDN
):)?(?P<port>\d+)$""",
    re.VERBOSE,
)

DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = "1910"


def validate_web_host(value):
    if value is None:
        return {"host": DEFAULT_WEB_HOST, "port": DEFAULT_WEB_PORT}
    if not (match := WEB_HOST_RE.match(value)):
        raise click.BadParameter("Not a valid port, ip:port or fqdn:port", param_hint="'--web'")
    addr, ipv4, fqdn, port = match.groups()
    if not port.isdigit():
        raise click.BadParameter("%r is not a valid port number." % port, param_hint="'--web'")
    return {"host": addr or ipv4 or DEFAULT_WEB_HOST, "port": port or DEFAULT_WEB_PORT}


@cli.command()
@common_options["optional_serials"]
@click.argument("directory", type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=False))
@click.option("--scroll/--no-scroll", default=True, help="If scroll in keys is activated. Default to true.")
@click.option(
    "--web",
    type=str,
    help=f"Web server optional port number, or ip:port, or fqdn:port. Default to {DEFAULT_WEB_HOST}:{DEFAULT_WEB_PORT}",
)
@click.option("--no-web", is_flag=True, default=False, help="Deactivate web server.")
@click.option(
    "--web-password",
    is_flag=True,
    help="Will ask for a password for web access. No password by default",
)
@click.option(
    "--ssl-cert",
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    help="Path to SSL certificate file for the web server.",
)
@click.option(
    "--ssl-key",
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
    help="Path to SSL private key file for the web server.",
)
@common_options["verbosity"]
@cloup.constraint(cons.mutually_exclusive, ["web", "no_web"])
def run(serials, directory, scroll, web, no_web, web_password, ssl_cert, ssl_key):
    """Run, Forrest, Run!

    Arguments:

    SERIALS: Serial number(s) of the Stream Deck(s) to handle.
    Optional if only one Stream Deck, or if the given DIRECTORY is the configuration of a Stream Deck, or if all Stream Deck having a configuration directory inside DIRECTORY must be run. \n
    DIRECTORY: Path of the directory containing configuration directories for the Stream Decks to run, or the final configuration directory if only one to run.
    """

    if serials is None:
        serials = []

    directory = Path(directory)

    if not no_web:
        web = validate_web_host(web)
        if web_password:
            web_password = click.prompt("Password for web access", hide_input=True)

    current_decks = {}

    def start_deck(device, serial):
        deck_directory = Manager.normalize_deck_directory(directory, serial)
        if not deck_directory.exists() or not deck_directory.is_dir():
            return
        logger.info(
            f'[DECK {serial}] Ready to run{"" if device else " (for web only)"} in directory "{deck_directory}"'
        )
        deck = Deck(
            path=deck_directory,
            path_modified_at=deck_directory.lstat().st_ctime,
            name=serial,
            disabled=False,
            device=device,
            scroll_activated=scroll,
        )
        if device:
            Manager.write_deck_model(deck_directory, device.info)
        deck.on_create()
        deck.render()
        current_decks[serial] = deck

    def stop_deck(deck, close=True):
        deck.unrender()
        deck.on_delete()
        if close and not deck.device.is_fake:
            Manager.close_deck(deck.device)
        current_decks.pop(deck.serial)

    if no_web:

        # in non-web mode we'll only run decks that are really connected
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

    else:

        # in web mode, we run for all directories that hold a deck configuration if they are valid
        def check_decks_and_directories():
            # first we get the connected decks
            Manager.get_decks(limit_to_serials=serials or None, exit_if_none=False)

            # first check that decks have their directories else we stop them
            for deck in list(current_decks.values()):
                if deck.directory_removed:
                    if not deck.path.exists():
                        logger.critical(
                            f'[{deck}] Configuration directory "{deck.path}" was removed. Waiting for it...'
                        )
                    else:
                        logger.warning(f'[{deck}] Configuration directory "{deck.path}" changed. Reloading...')
                    stop_deck(deck, close=False)

            # if the connected state of a deck changes, we stop it (will be restarted with the correct state just after)
            for deck in list(current_decks.values()):
                is_connected = deck.serial in Manager.open_decks and Manager.open_decks[deck.serial].connected()
                if deck.device.is_fake and is_connected:
                    logger.info(f"[{deck}] Now connected")
                    stop_deck(deck, close=True)
                elif not deck.device.is_fake and not is_connected:
                    logger.info(f"[{deck}] Not connected anymore")
                    stop_deck(deck, close=True)

            # now go through directories to handle the ones that look likes a deck
            for child_dir in directory.iterdir():
                if not child_dir.is_dir():
                    continue
                serial = str(child_dir.name)
                if not SERIAL_RE.match(serial):
                    continue
                if serial in current_decks:
                    # we already handle it
                    continue
                if serials and serial not in serials:
                    # not in the serials we were asked to render
                    continue
                # first check if it's one of the opened decks
                if serial in Manager.open_decks:
                    # in this case we start the deck with the real device
                    start_deck(Manager.open_decks[serial], serial)
                    continue
                # else we try to load model information
                try:
                    Manager.get_info_from_model_file(child_dir)
                except Exception:
                    # not a valid directory
                    continue
                # we can start the deck with a fake device (created by the `Deck` class itself)
                start_deck(None, serial)

    if not no_web:
        Manager.start_web_server(web["host"], web["port"], ssl_cert, ssl_key, web_password)
    Manager.start_files_watcher()

    end_event = threading.Event()

    def end(signum, frame):
        if logger.level <= logging.DEBUG:
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

    if not no_web:
        Manager.end_web_server()
    Manager.end_files_watcher()
    Manager.end_processes_checker()

    for deck in list(current_decks.values()):
        stop_deck(deck)

    Manager.close_opened_decks()

    main_thread = threading.currentThread()
    for t in threading.enumerate():
        if t is not main_thread and t.is_alive():
            t.join(0.5)
