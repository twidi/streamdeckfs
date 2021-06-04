#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of Streamdeckify
# (see https://github.com/twidi/streamdeckify).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import click
import click_log

from ..common import SUPPORTED_PLATFORMS, PLATFORM, Manager, logger


class NaturalOrderGroup(click.Group):
    def list_commands(self, ctx):
        return self.commands.keys()


CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(cls=NaturalOrderGroup, context_settings=CONTEXT_SETTINGS)
def cli():
    if not SUPPORTED_PLATFORMS.get(PLATFORM):
        return Manager.exit(1, f'{PLATFORM} is not supported yet')


common_options = {
    'optional_deck': click.argument('deck', nargs=-1, required=False),
    'verbosity': click_log.simple_verbosity_option(logger, help='Either CRITICAL, ERROR, WARNING, INFO or DEBUG', show_default=True),
}


def validate_positive_integer(ctx, param, value):
    if not param.required and value is None:
        return None
    if value <= 0:
        raise click.BadParameter(f"{value} is not a positive integer.")
    return value
