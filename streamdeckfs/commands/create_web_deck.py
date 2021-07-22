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

from ..common import FakeStreamDeckWeb, Manager
from .base import cli, validate_serials


def validate_web_serial(ctx, param, value):
    if value is not None:
        if not value.startswith("W"):
            value = "W" + value
        value = validate_serials(ctx, param, (value,))[0]
    return value


@cli.command()
@click.argument(
    "directory",
    type=click.Path(
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        exists=True,
    ),
)
@click.option(
    "-s",
    "--serial",
    type=str,
    required=True,
    help="Serial number for the web deck. 12 uppercase characters (A-Z, 0-9), must start with a 'W' or one will be added.",
    callback=validate_web_serial,
)
@click.option("-r", "--rows", type=click.IntRange(min=1, max=8), required=True, help="Number of rows (from 1 to 8)")
@click.option(
    "-c", "--cols", type=click.IntRange(min=1, max=12), required=True, help="Number of columns (from 1 to 12)"
)
def create_web_deck(directory, serial, rows, cols):
    """Create the configuration directory for a web-only deck.

    Arguments:

    DIRECTORY: Path of the directory where to create the new configuration directory.
    """
    directory = Path(directory)
    deck_directory = directory / serial
    is_new = False
    if not deck_directory.exists():
        deck_directory.mkdir()
        is_new = True

    device_info = {
        "class": FakeStreamDeckWeb,
        "nb_rows": rows,
        "nb_cols": cols,
    }

    Manager.write_deck_model(deck_directory, device_info)
    click.echo(f"Successfully {'created' if is_new else 'updated'} web deck in {deck_directory}")
