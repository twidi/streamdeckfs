#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import click
import click_log
import cloup

from ..common import PLATFORM, SUPPORTED_PLATFORMS, Manager, logger


class NaturalOrderGroup(cloup.Group):
    def list_commands(self, ctx):
        return self.commands.keys()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@cloup.group(cls=NaturalOrderGroup, context_settings=CONTEXT_SETTINGS)
def cli():
    if not SUPPORTED_PLATFORMS.get(PLATFORM):
        return Manager.exit(1, f"{PLATFORM} is not supported yet")


common_options = {
    "optional_serial": cloup.argument("serial", nargs=-1, required=False),
    "optional_serials": cloup.argument("serials", nargs=-1, required=False),
    "verbosity": click_log.simple_verbosity_option(
        logger, "--verbosity", help="Either CRITICAL, ERROR, WARNING, INFO or DEBUG", show_default=True
    ),
}


def validate_positive_integer(ctx, param, value):
    if not param.required and value is None:
        return None
    if value <= 0:
        raise click.BadParameter(f"{value} is not a positive integer.")
    return value


def validate_positive_integer_or_zero(ctx, param, value):
    if not param.required and value is None:
        return None
    if value < 0:
        raise click.BadParameter(f"{value} is not 0 or a positive integer.")
    return value
