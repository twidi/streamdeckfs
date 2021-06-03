import click
import click_log

from ..common import SUPPORTED_PLATFORMS, PLATFORM, Manager, logger


class NaturalOrderGroup(click.Group):
    def list_commands(self, ctx):
        return self.commands.keys()


@click.group(cls=NaturalOrderGroup)
def cli():
    if not SUPPORTED_PLATFORMS.get(PLATFORM):
        return Manager.exit(1, f'{PLATFORM} is not supported yet')


common_options = {
    'optional_deck': click.argument('deck', nargs=-1, required=False),
    'verbosity': click_log.simple_verbosity_option(logger, help='Either CRITICAL, ERROR, WARNING, INFO or DEBUG', show_default=True),
}


def validate_positive_integer(ctx, param, value):
    if value <= 0:
        raise click.BadParameter("Should be a positive integer")
    return value
