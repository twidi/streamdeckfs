#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
from pathlib import Path

import click

from ..common import SERIAL_RE, Manager
from ..entities import Key, Page
from .base import cli, common_options


@cli.command()
@common_options["optional_serial"]
@click.argument("directory", type=click.Path(file_okay=False, dir_okay=True, resolve_path=True))
@click.option(
    "-p",
    "--pages",
    type=click.IntRange(min=0),
    default=0,
    help="Number of pages to generate. Default to 0 to only create the main directory.",
)
@click.option("-y", "--yes", is_flag=True, help="Automatically answer yes to confirmation demand")
@click.option(
    "--dry-run", is_flag=True, help="Only show the directories that would have been created without creating them"
)
@common_options["verbosity"]
def make_dirs(serial, directory, pages, yes, dry_run):
    """Create pages and keys directories for a Stream Deck.

    Arguments:

    SERIAL: Serial number of the Stream Deck to handle. Optional if only one Stream Deck connected.\n
    DIRECTORY: Path of the directory where to create pages and keys directories. If it does not ends with a subdirectory matching the SERIAL, it will be added.
    """
    if serial:
        if len(serial) > 1:
            raise click.BadParameter("Only one serial accepted", param_hint="SERIAL")
        serial = serial[0]
    else:
        serial = None

    directory = Path(directory)

    deck = None
    if not serial or not serial.startswith("W"):
        try:
            deck = Manager.get_deck(serial)
        except IndexError:
            pass
        else:
            serial = deck.info["serial"]
            directory = Manager.normalize_deck_directory(directory, serial)

    if not deck:
        if SERIAL_RE.match(final_serial := (serial or directory.name)) and final_serial.startswith("w"):
            serial = final_serial
            directory = Manager.normalize_deck_directory(directory, serial)
            try:
                deck = Manager.get_info_from_model_file(directory)["device"]
            except FileNotFoundError:
                return Manager.exit(
                    1,
                    f'No web Stream Deck found with the serial "{serial}". Use the "create-web-deck" command to create the wanted web deck.',
                )
        else:
            return Manager.exit(
                1,
                f'No Stream Deck found with the serial "{serial}". Use the "inspect" command to list all available decks.',
            )

    if directory.exists() and not directory.is_dir():
        return Manager.exit(1, f'"{directory}" exists but is not a directory.')

    if not yes:
        if not click.confirm(
            f'Create {"(not really, dry-run mode is active) " if dry_run else ""}directories for Stream Deck "{serial}" in directory "{directory}" ({pages} page(s))?',
            default=True,
        ):
            click.echo("Aborting.")
            return

    def create_dir(directory, desc, relative_to="", print_prefix=""):
        directory_repr = directory.relative_to(relative_to) if relative_to else directory
        click.echo(f"{print_prefix}{directory_repr}   ({desc})... ", nl=False)
        if directory.exists():
            click.echo("Already exists.")
            return False, directory
        try:
            real_directory = next(directory.parent.glob(f"{directory.name};*"))
        except StopIteration:
            pass
        else:
            click.echo(f"Already exists (as {real_directory.name})")
            return False, real_directory
        try:
            if not dry_run:
                directory.mkdir(parents=True)
        except Exception:
            return Manager.exit(1, f'"{directory}" could not be created', log_exception=True)
        click.echo("Would have been created." if dry_run else "Created.")
        return True, directory

    directory = create_dir(directory, f'Main directory for Stream Deck "{serial}"')[1]
    if not dry_run:
        Manager.write_deck_model(directory, deck.info)

    if pages:
        click.echo("Subdirectories:")

        for page in range(1, pages + 1):
            page_dir = directory / Page.compose_main_part({"page": page})
            page_dir = create_dir(page_dir, f"Directory for content of page {page}", directory, "\t")[1]
            for row in range(1, deck.info["nb_rows"] + 1):
                for col in range(1, deck.info["nb_cols"] + 1):
                    key_dir = page_dir / Key.compose_main_part({"row": row, "col": col})
                    create_dir(key_dir, f"Directory for key {col} on row {row} on page {page}", page_dir, "\t\t")
