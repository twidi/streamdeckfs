#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import click

from ..common import Manager
from .base import cli, common_options


@cli.command()
@common_options["verbosity"]
def inspect():
    """
    Get information about all connected Stream Decks.
    """
    decks = Manager.get_decks(need_open=False)

    click.echo(f"Found {len(decks)} Stream Deck(s):")

    for deck in decks.values():
        info = deck.info
        click.echo(f"* Deck {info['serial']}{'' if deck.info['connected'] else ' (already connected elsewhere)'}")
        click.echo(f"\t - Type: {info['type']}")
        click.echo(f"\t - ID: {info['id']}")
        click.echo(f"\t - Serial: {info['serial']}")
        click.echo(f"\t - Firmware Version: {info['firmware']}")
        click.echo(f"\t - Key Count: {info['nb_keys']} (in a {info['rows']}x{info['cols']} grid)")
        click.echo(f"\t - Key Images: {info['key_width']}x{info['key_height']} pixels, {info['format']} format")

        if deck.info["connected"]:
            Manager.close_deck(deck)
