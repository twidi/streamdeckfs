import click

from ..common import Manager
from .base import cli, common_options


@cli.command()
@common_options['verbosity']
def inspect():
    """
Get information about all connected Stream Decks.
    """
    decks = Manager.get_decks()

    click.echo(f"Found {len(decks)} Stream Deck(s):")

    for deck in decks.values():
        info = deck.info
        click.echo(f"* Deck {info['serial']}")
        click.echo(f"\t - Type: {info['type']}")
        click.echo(f"\t - ID: {info['id']}")
        click.echo(f"\t - Serial: {info['serial']}")
        click.echo(f"\t - Firmware Version: '{info['firmware']}'")
        click.echo(f"\t - Key Count: {info['nb_keys']} (in a {info['rows']}x{info['cols']} grid)")
        click.echo(f"\t - Key Images: {info['key_width']}x{info['key_height']} pixels, {info['format']} format")

        deck.close()
