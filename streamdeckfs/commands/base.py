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

from ..common import PLATFORM, SERIAL_RE, SUPPORTED_PLATFORMS, Manager, logger


class NaturalOrderGroup(cloup.Group):
    def list_commands(self, ctx):
        return self.commands.keys()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@cloup.group(cls=NaturalOrderGroup, context_settings=CONTEXT_SETTINGS)
def cli():
    if not SUPPORTED_PLATFORMS.get(PLATFORM):
        return Manager.exit(1, f"{PLATFORM} is not supported yet")


def validate_serials(ctx, param, value):
    if value is not None:
        serials = value if isinstance(value, (tuple, list)) else (value,)
        for serial in serials:
            if not SERIAL_RE.match(serial):
                raise click.BadParameter(f"{serial} is not a valid serial number")
    return value


common_options = {
    "optional_serial": cloup.argument("serial", nargs=-1, required=False, callback=validate_serials),
    "optional_serials": cloup.argument("serials", nargs=-1, required=False, callback=validate_serials),
    "verbosity": click_log.simple_verbosity_option(
        logger, "--verbosity", help="Either CRITICAL, ERROR, WARNING, INFO or DEBUG", show_default=True
    ),
}
