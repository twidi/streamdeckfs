#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#

# the orders of the defined, ie imported, commands is the order they will be displayed
# so we want to handle this order manually here
# isort:skip_file

from ..common import Manager
from .inspect import inspect  # noqa: F401
from .make_dirs import make_dirs  # noqa: F401
from .run import run  # noqa: F401
from .api import *  # noqa: F401, F403

from .base import cli


def main():
    try:
        cli()
    except SystemExit as exc:
        Manager.exit(exc.code)
    except Exception:
        Manager.exit(1, "Oops...", log_exception=True)
    else:
        Manager.exit(0)
