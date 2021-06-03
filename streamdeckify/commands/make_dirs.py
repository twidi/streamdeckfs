import click

from ..common import Manager
from ..entities import Key, Page
from .base import cli, common_options, validate_positive_integer


@cli.command()
@common_options['optional_deck']
@click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True))
@click.option('-p', '--pages', type=int, default=1, callback=validate_positive_integer, help="Number of pages to generate. Default to 1.")
@click.option('-y', '--yes', is_flag=True, help='Automatically answer yes to confirmation demand')
@common_options['verbosity']
def make_dirs(deck, directory, pages, yes):
    """Create keys directories for a Stream Deck.

    Arguments:

    DECK: Serial number of the Stream Deck to handle. Optional if only one Stream Deck.\n
    DIRECTORY: Path of the directory where to create pages and keys directories. If it does not ends with a subdirectory matching the SERIAL, it will be added.
    """
    deck = Manager.get_deck(deck)
    serial = deck.info['serial']
    directory = Manager.normalize_deck_directory(directory, serial)
    if directory.exists() and not directory.is_dir():
        return Manager.exit(1, f'"{directory}" exists but is not a directory.')

    if not yes:
        if not click.confirm(f'Create directories for Stream Deck "{serial}" in directory "{directory}" ({pages} page(s))?', default=True):
            click.echo('Aborting.')
            return

    def create_dir(directory, desc, relative_to='', print_prefix=''):
        directory_repr = directory.relative_to(relative_to) if relative_to else directory
        click.echo(f"{print_prefix}{directory_repr}   ({desc})... ", nl=False)
        if directory.exists():
            click.echo("Already exists.")
            return False
        try:
            pass
            directory.mkdir(parents=True)
        except Exception:
            return Manager.exit(1, f'"{directory}" could not be created', log_exception=True)
        click.echo("Created.")
        return True

    create_dir(directory, f'Main directory for Stream Deck "{serial}"')
    click.echo('Subdirectories:')

    for page in range(1, pages + 1):
        page_dir = directory / Page.Page.dir_template.format(page=page)
        create_dir(page_dir, f'Directory for content of page {page}', directory, "\t")
        for row in range(1, deck.info['rows'] + 1):
            for col in range(1, deck.info['cols'] + 1):
                key_dir = page_dir / Key.dir_template.format(row=row, col=col)
                create_dir(key_dir, f'Directory for key {col} on row {row} on page {page}', page_dir, "\t\t")
