#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of Streamdeckify
# (see https://github.com/twidi/streamdeckify).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import click

from ..common import Manager
from .base import cli, common_options


def validate_brightness_level(ctx, param, value):
    if 0 <= value <= 100:
        return value
    raise click.BadParameter(f'{value} must be between 0 and 100 (inclusive)')


@cli.command()
@common_options['optional_deck']
@click.argument('level', type=int, callback=validate_brightness_level)
@common_options['verbosity']
def brightness(deck, level):
    """Set the brightness level of a Stream Deck.

    Arguments:

    LEVEL: Brightness level, from 0 (no light) to 100 (brightest)
    """
    deck = Manager.get_deck(deck)
    deck.set_brightness(level)
