#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of Streamdeckify
# (see https://github.com/twidi/streamdeckify).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import json
from copy import deepcopy

import click
import click_log

from ..common import logger, Manager
from ..entities import Deck, FILTER_DENY
from .base import cli

__all__ = [
    'get_page_path',
    'get_page_conf',
    'set_page_conf',
    'get_key_path',
    'get_key_conf',
    'set_key_conf',
    'get_image_path',
    'get_image_conf',
    'set_image_conf',
    'get_text_path',
    'get_text_conf',
    'set_text_conf',
    'get_event_path',
    'get_event_conf',
    'set_event_conf',
]


class FilterCommands:
    options = {
        'directory': click.argument('directory', type=click.Path(file_okay=False, dir_okay=True, resolve_path=True, exists=True)),
        'page': click.option('-p', '--page', 'page_filter', type=str, required=True, help='A page number or a name'),
        'key': click.option('-k', '--key', 'key_filter', type=str, required=True, help='A key as `(row,col)` or a name'),
        'layer': click.option('-l', '--layer', 'layer_filter', type=str, required=False, help='A layer number (do not pass it to use the default image)'),  # if not given we'll use ``-1``
        'text_line': click.option('-l', '--line', 'text_line_filter', type=str, required=False, help='A text line (do not pass it to use the default text)'),  # if not given we'll use ``-1``
        'event': click.option('-e', '--event', 'event_filter', type=str, required=True, help='An event name (press/longpress/release/start)'),
        'names': click.option('-c', '--conf', 'names', type=str, multiple=True, required=False, help='Names to get the values from the configuration "---conf name1 --conf name2..."'),
        'names_and_values': click.option('-c', '--conf', 'names_and_values', type=(str, str), multiple=True, required=True, help='Pair of names and values to set for the configuration "---conf name1 value1 --conf name2 value2..."'),
        'verbosity': click_log.simple_verbosity_option(logger, default='WARNING', help='Either CRITICAL, ERROR, WARNING, INFO or DEBUG', show_default=True),
    }

    @classmethod
    def combine_options(cls):
        options = {}
        options['page_filter'] = lambda func: cls.options['directory'](cls.options['page'](func))
        options['key_filter'] = lambda func: options['page_filter'](cls.options['key'](func))
        options['layer_filter'] = lambda func: options['key_filter'](cls.options['layer'](func))
        options['text_line_filter'] = lambda func: options['key_filter'](cls.options['text_line'](func))
        options['event_filter'] = lambda func: options['key_filter'](cls.options['event'](func))

        def add_verbosity(option):
            return lambda func: option(cls.options['verbosity'](func))

        def set_command(name, option):
            cls.options[name] = add_verbosity(option)
            arg_key = f'{name}_arg'
            options[arg_key] = lambda func: option(cls.options['name'](func))
            cls.options[arg_key] = add_verbosity(options[arg_key])

            key = f'{name}_with_names'
            options[key] = lambda func: option(cls.options['names'](func))
            cls.options[key] = add_verbosity(options[key])

            key = f'{name}_with_names_and_values'
            options[key] = lambda func: option(cls.options['names_and_values'](func))
            cls.options[key] = add_verbosity(options[key])

        for name, option in list(options.items()):
            set_command(name, option)

    @staticmethod
    def get_deck(directory, page_filter=None, key_filter=None, event_filter=None, layer_filter=None, text_line_filter=None):
        directory = Manager.normalize_deck_directory(directory, None)
        deck = Deck(path=directory, path_modified_at=directory.lstat().st_ctime, name=directory.name, disabled=False, device=None, scroll_activated=False)
        if page_filter is not None:
            deck.filters['page'] = page_filter
        if key_filter is not None:
            deck.filters['key'] = key_filter
        if event_filter is not None:
            deck.filters['event'] = event_filter
        if layer_filter is not None:
            deck.filters['layer'] = layer_filter
        if text_line_filter is not None:
            deck.filters['text_line'] = text_line_filter
        deck.on_create()
        return deck

    @staticmethod
    def find_page(deck, page_filter):
        if not (page := deck.find_page(page_filter, allow_disabled=True)):
            Manager.exit(1, f'[{deck}] Page `{page_filter}` not found')
        return page

    @staticmethod
    def find_key(page, key_filter):
        if not (key := page.find_key(key_filter, allow_disabled=True)):
            Manager.exit(1, f'[{page}] Key `{key_filter}` not found')
        return key

    def find_layer(key, layer_filter):
        if not (layer := key.find_layer(layer_filter or -1, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Layer `{layer_filter}` not found')
        return layer

    def find_text_line(key, text_line_filter):
        if not (text_line := key.find_text_line(text_line_filter or -1, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Text line `{text_line_filter}` not found')
        return text_line

    @staticmethod
    def find_event(key, event_filter):
        if not (event := key.find_event(event_filter, allow_disabled=True)):
            Manager.exit(1, f'[{key}] Event `{event_filter}` not found')
        return event

    @classmethod
    def get_args(cls, obj, resolve=True):
        return obj.get_resovled_raw_args() if resolve else obj.get_raw_args()

    @classmethod
    def get_args_as_json(cls, obj, only_names=None):
        main, args = cls.get_args(obj)
        data = main | args
        if only_names is not None:
            data = {k: v for k, v in data.items() if k in only_names}
        return json.dumps(data)

    @staticmethod
    def compose_filename(obj, main, args):
        return obj.compose_filename(main, args)

    @classmethod
    def get_update_args_filename(cls, obj, names_and_values):
        main, args = cls.get_args(obj, resolve=False)
        final_args = deepcopy(args)
        for (name, value) in names_and_values:
            if ';' in name:
                Manager.exit(1, f'[{obj}] Configuration name `{name}` is not valid')
            if ';' in value:
                Manager.exit(1, f'[{obj}] Configuration value for `{name}` is not valid')
            if name in main:
                Manager.exit(1, f'[{obj}] Configuration name `{name}` cannot be changed')

            if not value:
                final_args.pop(name, None)
            else:
                try:
                    final_args[name] = obj.raw_parse_filename(cls.compose_filename(obj, main, {}) + f';{name}={value}', obj.path.parent)[1][name]
                except KeyError:
                    Manager.exit(1, f'[{obj}] Configuration `{name} {value}` is not valid')

        return cls.compose_filename(obj, main, final_args)


FC = FilterCommands
FC.combine_options()


@cli.command()
@FC.options['page_filter']
def get_page_path(directory, page_filter):
    """Get the path of a page."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(page.path)


@cli.command()
@FC.options['page_filter_with_names']
def get_page_conf(directory, page_filter, names):
    """Get the configuration of a page, in json."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.get_args_as_json(page, names or None))


@cli.command()
@FC.options['page_filter_with_names_and_values']
def set_page_conf(directory, page_filter, names_and_values):
    """Set the value of some entries of a page configuration."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    page.rename(FC.get_update_args_filename(page, names_and_values))


@cli.command()
@FC.options['key_filter']
def get_key_path(directory, page_filter, key_filter):
    """Get the path of a key."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(key.path)


@cli.command()
@FC.options['key_filter_with_names']
def get_key_conf(directory, page_filter, key_filter, names):
    """Get the configuration of a key, in json."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.get_args_as_json(key, names or None))


@cli.command()
@FC.options['key_filter_with_names_and_values']
def set_key_conf(directory, page_filter, key_filter, names_and_values):
    """Set the value of some entries of a key configuration."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    key.rename(FC.get_update_args_filename(key, names_and_values))


@cli.command()
@FC.options['layer_filter']
def get_image_path(directory, page_filter, key_filter, layer_filter):
    """Get the path of an image/layer."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(layer.path)


@cli.command()
@FC.options['layer_filter_with_names']
def get_image_conf(directory, page_filter, key_filter, layer_filter, names):
    """Get the configuration of an image/layer, in json."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(FC.get_args_as_json(layer, names or None))


@cli.command()
@FC.options['layer_filter_with_names_and_values']
def set_image_conf(directory, page_filter, key_filter, layer_filter, names_and_values):
    """Set the value of some entries of an image configuration."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    layer.rename(FC.get_update_args_filename(layer, names_and_values))


@cli.command()
@FC.options['text_line_filter']
def get_text_path(directory, page_filter, key_filter, text_line_filter):
    """Get the path of an image/layer."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(text_line.path)


@cli.command()
@FC.options['text_line_filter_with_names']
def get_text_conf(directory, page_filter, key_filter, text_line_filter, names):
    """Get the configuration of an image/layer, in json."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(FC.get_args_as_json(text_line, names or None))


@cli.command()
@FC.options['text_line_filter_with_names_and_values']
def set_text_conf(directory, page_filter, key_filter, text_line_filter, names_and_values):
    """Set the value of some entries of an image configuration."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    text_line.rename(FC.get_update_args_filename(text_line, names_and_values))


@cli.command()
@FC.options['event_filter']
def get_event_path(directory, page_filter, key_filter, event_filter):
    """Get the path of an event."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    print(event.path)


@cli.command()
@FC.options['event_filter_with_names']
def get_event_conf(directory, page_filter, key_filter, event_filter, names):
    """Get the configuration of an event, in json."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    print(FC.get_args_as_json(event, names or None))


@cli.command()
@FC.options['event_filter_with_names_and_values']
def set_event_conf(directory, page_filter, key_filter, event_filter, names_and_values):
    """Set the value of some entries of an event configuration."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    event.rename(FC.get_update_args_filename(event, names_and_values))
