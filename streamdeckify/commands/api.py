#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of Streamdeckify
# (see https://github.com/twidi/streamdeckify).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import json
import re
import shutil
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
        'to_page': click.option('-tp', '--to-page', 'to_page_filter', type=str, required=False, help='The optional destination page number or a name'),
        'key': click.option('-k', '--key', 'key_filter', type=str, required=True, help='A key as "row,col" or a name'),
        'to_key': click.option('-tk', '--to-key', 'to_key_filter', type=str, required=False, help='The optional destinations key as "row,col" or a name'),
        'layer': click.option('-l', '--layer', 'layer_filter', type=str, required=False, help='A layer number (do not pass it to use the default image) or name'),  # if not given we'll use ``-1``
        'text_line': click.option('-l', '--line', 'text_line_filter', type=str, required=False, help='A text line (do not pass it to use the default text) or name'),  # if not given we'll use ``-1``
        'event': click.option('-e', '--event', 'event_filter', type=str, required=True, help='An event kind (press/longpress/release/start) or name'),
        'names': click.option('-c', '--conf', 'names', type=str, multiple=True, required=False, help='Names to get the values from the configuration "-c name1 -c name2..."'),
        'names_and_values': click.option('-c', '--conf', 'names_and_values', type=(str, str), multiple=True, required=True, help='Pair of names and values to set for the configuration "-c name1 value1 -c name2 value2..."'),
        'optional_names_and_values': click.option('-c', '--conf', 'names_and_values', type=(str, str), multiple=True, required=False, help='Pair of names and values to set for the configuration "-c name1 value1 -c name2 value2..."'),
        'verbosity': click_log.simple_verbosity_option(logger, default='WARNING', help='Either CRITICAL, ERROR, WARNING, INFO or DEBUG', show_default=True),
        'link': click.option('--link', type=click.Path(file_okay=True, dir_okay=False, resolve_path=True, exists=True), help='Create a link to this file instead of an empty file'),
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

        cls.options['to_page_to_key'] = lambda func: cls.options['to_page'](cls.options['to_key'](func))

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
    def validate_names_and_values(cls, obj, main_args, names_and_values, msg_name_part):
        args = {}
        removed_args = set()
        base_filename = cls.compose_filename(obj, main_args, {})
        for (name, value) in names_and_values:
            if ';' in name:
                Manager.exit(1, f'[{msg_name_part}] Configuration name `{name}` is not valid')
            if ';' in value:
                Manager.exit(1, f'[{msg_name_part}] Configuration value for `{name}` is not valid')
            if name in main_args:
                Manager.exit(1, f'[{msg_name_part}] Configuration name `{name}` cannot be changed')

            if not value:
                removed_args.add(name)
            else:
                try:
                    args[name] = obj.raw_parse_filename(base_filename + f';{name}={value}', obj.path.parent)[1][name]
                except KeyError:
                    Manager.exit(1, f'[{msg_name_part}] Configuration `{name} {value}` is not valid')
        return args, removed_args

    @classmethod
    def update_args(cls, obj, names_and_values, msg_name_part=None):
        main, args = cls.get_args(obj, resolve=False)
        final_args = deepcopy(args)
        updated_args, removed_args = cls.validate_names_and_values(obj, main, names_and_values, obj if msg_name_part is None else msg_name_part)
        final_args |= updated_args
        for key in removed_args:
            final_args.pop(key, None)
        return main, final_args

    @classmethod
    def get_update_args_filename(cls, obj, names_and_values):
        main, args = cls.update_args(obj, names_and_values)
        return cls.compose_filename(obj, main, args)

    @classmethod
    def check_new_path(cls, path, is_dir, msg_name_part):
        if path.exists():
            Manager.exit(1, f'[{msg_name_part}] Cannot create {"directory" if is_dir else "file"} "{path}" because it already exists')

    @classmethod
    def create_entity(cls, entity_class, parent, identifier, main_args, names_and_values, link, msg_name_part):
        entity_filename = entity_class.compose_filename(main_args, {})
        entity = entity_class(**entity_class.get_create_base_args(parent.path / entity_filename, parent, identifier))
        args = cls.validate_names_and_values(entity, main_args, names_and_values, msg_name_part)[0]
        path = parent.path / entity.compose_filename(main_args, args)
        cls.check_new_path(path, entity_class.is_dir, msg_name_part)
        if entity_class.is_dir:
            path.mkdir()
        elif link:
            path.symlink_to(link)
        else:
            path.touch()
        return path

    @classmethod
    def copy_entity(cls, entity, parent, main_override, names_and_values, msg_name_part):
        main, args = cls.update_args(entity, names_and_values, msg_name_part)
        main |= main_override
        path = parent.path / cls.compose_filename(entity, main, args)
        cls.check_new_path(path, entity.is_dir, msg_name_part)
        if entity.is_dir:
            shutil.copytree(entity.path, path, symlinks=True)
        else:
            shutil.copy2(entity.path, path, follow_symlinks=False)
        return path

    @classmethod
    def move_entity(cls, entity, parent, main_override, names_and_values, msg_name_part):
        main, args = cls.update_args(entity, names_and_values, msg_name_part)
        main |= main_override
        path = parent.path / cls.compose_filename(entity, main, args)
        cls.check_new_path(path, entity.is_dir, msg_name_part)
        entity.rename(path)
        return path

    @classmethod
    def create_page(cls, deck, number, names_and_values):
        from ..entities import Page
        return cls.create_entity(Page, deck, number, {'page': number}, names_and_values, None, f'{deck}, NEW PAGE {number}')

    @classmethod
    def copy_page(cls, page, to_number, names_and_values):
        return cls.copy_entity(page, page.deck, {'page': to_number}, names_and_values, f'{page.deck}, NEW PAGE {to_number}')

    @classmethod
    def move_page(cls, page, to_number, names_and_values):
        return cls.move_entity(page, page.deck, {'page': to_number}, names_and_values, f'{page.deck}, NEW PAGE {to_number}')

    @classmethod
    def create_key(cls, page, key, names_and_values):
        from ..entities import Key
        row, col = map(int, key.split(','))
        return cls.create_entity(Key, page, key, {'row': row, 'col': col}, names_and_values, None, f'{page}, NEW KEY {key}')

    @classmethod
    def copy_key(cls, key, to_page, to_row, to_col, names_and_values):
        return cls.copy_entity(key, to_page, {'row': to_row, 'col': to_col}, names_and_values, f'{to_page}, NEW KEY {key}')

    @classmethod
    def move_key(cls, key, to_page, to_row, to_col, names_and_values):
        return cls.move_entity(key, to_page, {'row': to_row, 'col': to_col}, names_and_values, f'{to_page}, NEW KEY {key}')

    @classmethod
    def create_layer(cls, key, names_and_values, link):
        from ..entities import KeyImageLayer
        return cls.create_entity(KeyImageLayer, key, -1, {}, names_and_values, link, f'{key}, NEW LAYER')

    @classmethod
    def copy_layer(cls, layer, to_key, names_and_values):
        return cls.copy_entity(layer, to_key, {}, names_and_values, f'{to_key}, NEW LAYER')

    @classmethod
    def move_layer(cls, layer, to_key, names_and_values):
        return cls.move_entity(layer, to_key, {}, names_and_values, f'{to_key}, NEW LAYER')

    @classmethod
    def create_text_line(cls, key, names_and_values, link):
        from ..entities import KeyTextLine
        return cls.create_entity(KeyTextLine, key, -1, {}, names_and_values, link, f'{key}, NEW TEXT LINE')

    @classmethod
    def copy_text_line(cls, text_line, to_key, names_and_values):
        return cls.copy_entity(text_line, to_key, {}, names_and_values, f'{to_key}, NEW TEXT LINE')

    @classmethod
    def move_text_line(cls, text_line, to_key, names_and_values):
        return cls.move_entity(text_line, to_key, {}, names_and_values, f'{to_key}, NEW TEXT LINE')

    @classmethod
    def create_event(cls, key, kind, names_and_values, link):
        from ..entities import KeyEvent
        return cls.create_entity(KeyEvent, key, kind, {'kind': kind}, names_and_values, link, f'{key}, NEW EVENT {kind}')

    @classmethod
    def copy_event(cls, event, to_key, to_kind, names_and_values):
        return cls.copy_entity(event, to_key, {'kind': to_kind}, names_and_values, f'{to_key}, NEW EVENT {to_kind}')

    @classmethod
    def move_event(cls, event, to_key, to_kind, names_and_values):
        return cls.move_entity(event, to_key, {'kind': to_kind}, names_and_values, f'{to_key}, NEW EVENT {to_kind}')


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
@FC.options['directory']
@click.option('-p', '--page', type=int, required=True, help='The page number')
@FC.options['optional_names_and_values']
def create_page(directory, page, names_and_values):
    """Create a new image layer with configuration"""
    deck = FC.get_deck(directory, page_filter=FILTER_DENY, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    print(FC.create_page(deck, page, names_and_values))


@cli.command()
@FC.options['page_filter']
@click.option('-tp', '--to-page', 'to_page_number', type=int, required=True, help='The page number of the new page')
@FC.options['optional_names_and_values']
def copy_page(directory, page_filter, to_page_number, names_and_values):
    """Copy a page and all its content"""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.copy_page(page, to_page_number, names_and_values))


@cli.command()
@FC.options['page_filter']
@click.option('-tp', '--to-page', 'to_page_number', type=int, required=True, help='The page number of the new page')
@FC.options['optional_names_and_values']
def move_page(directory, page_filter, to_page_number, names_and_values):
    """Move a page to a different number"""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.move_page(page, to_page_number, names_and_values))


@cli.command()
@FC.options['page_filter']
def delete_page(directory, page_filter):
    """Fully delete a page directory."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    page.delete_on_disk()


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


def validate_key(ctx, param, value):
    if not param.required and value is None:
        return value
    if validate_key.re.match(value):
        return value
    raise click.BadParameter('Should be in the format "row,col"')


validate_key.re = re.compile(r'^\d+,\d+$')


@cli.command()
@FC.options['page_filter']
@click.option('-k', '--key', type=str, required=True, help='The key position as "row,col"', callback=validate_key)
@FC.options['optional_names_and_values']
def create_key(directory, page_filter, key, names_and_values):
    """Create a new image layer with configuration"""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.create_key(page, key, names_and_values))


@cli.command()
@FC.options['key_filter']
@FC.options['to_page']
@click.option('-tk', '--to-key', 'to_key', type=str, required=False, help='The optional destination key position as "row,col"', callback=validate_key)
@FC.options['optional_names_and_values']
def copy_key(directory, page_filter, key_filter, to_page_filter, to_key, names_and_values):
    """Copy a key and all its content"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    key = FC.find_key(FC.find_page(deck, page_filter), key_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else key.page
    to_row, to_col = map(int, to_key.split(',')) if to_key else key.key
    print(FC.copy_key(key, to_page, to_row, to_col, names_and_values))


@cli.command()
@FC.options['key_filter']
@FC.options['to_page']
@click.option('-tk', '--to-key', 'to_key', type=str, required=False, help='The optional destination key position as "row,col"', callback=validate_key)
@FC.options['optional_names_and_values']
def move_key(directory, page_filter, key_filter, to_page_filter, to_key, names_and_values):
    """Move a key to another page and/or a different position"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    key = FC.find_key(FC.find_page(deck, page_filter), key_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else key.page
    to_row, to_col = map(int, to_key.split(',')) if to_key else key.key
    print(FC.move_key(key, to_page, to_row, to_col, names_and_values))


@cli.command()
@FC.options['key_filter']
def delete_key(directory, page_filter, key_filter):
    """Fully delete of a key directory."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    key.delete_on_disk()


@cli.command()
@FC.options['layer_filter']
def get_image_path(directory, page_filter, key_filter, layer_filter):
    """Get the path of an image layer."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(layer.path)


@cli.command()
@FC.options['layer_filter_with_names']
def get_image_conf(directory, page_filter, key_filter, layer_filter, names):
    """Get the configuration of an image layer, in json."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(FC.get_args_as_json(layer, names or None))


@cli.command()
@FC.options['layer_filter_with_names_and_values']
def set_image_conf(directory, page_filter, key_filter, layer_filter, names_and_values):
    """Set the value of some entries of an image configuration."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    layer.rename(FC.get_update_args_filename(layer, names_and_values))


@cli.command()
@FC.options['key_filter']
@FC.options['optional_names_and_values']
@FC.options['link']
def create_image(directory, page_filter, key_filter, names_and_values, link):
    """Create a new image layer with configuration"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.create_layer(key, names_and_values, link))


@cli.command()
@FC.options['layer_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
def copy_image(directory, page_filter, key_filter, layer_filter, to_page_filter, to_key_filter, names_and_values):
    """Copy an image layer"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    layer = FC.find_layer(FC.find_key(FC.find_page(deck, page_filter), key_filter), layer_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else layer.page
    if to_key_filter:
        to_key = FC.find_key(to_page, to_key_filter)
    elif to_page_filter:
        to_key = FC.find_key(to_page, '%s,%s' % layer.key.key)
    else:
        to_key = layer.key
    print(FC.copy_layer(layer, to_key, names_and_values))


@cli.command()
@FC.options['layer_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
def move_image(directory, page_filter, key_filter, layer_filter, to_page_filter, to_key_filter, names_and_values):
    """Move a layer to another key"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    layer = FC.find_layer(FC.find_key(FC.find_page(deck, page_filter), key_filter), layer_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else layer.page
    if to_key_filter:
        to_key = FC.find_key(to_page, to_key_filter)
    elif to_page_filter:
        to_key = FC.find_key(to_page, '%s,%s' % layer.key.key)
    else:
        to_key = layer.key
    print(FC.move_layer(layer, to_key, names_and_values))


@cli.command()
@FC.options['layer_filter']
def delete_image(directory, page_filter, key_filter, layer_filter):
    """Delete an image layer."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    layer.delete_on_disk()


@cli.command()
@FC.options['text_line_filter']
def get_text_path(directory, page_filter, key_filter, text_line_filter):
    """Get the path of an image layer."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(text_line.path)


@cli.command()
@FC.options['text_line_filter_with_names']
def get_text_conf(directory, page_filter, key_filter, text_line_filter, names):
    """Get the configuration of an image layer, in json."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(FC.get_args_as_json(text_line, names or None))


@cli.command()
@FC.options['text_line_filter_with_names_and_values']
def set_text_conf(directory, page_filter, key_filter, text_line_filter, names_and_values):
    """Set the value of some entries of an image configuration."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    text_line.rename(FC.get_update_args_filename(text_line, names_and_values))


@cli.command()
@FC.options['key_filter']
@FC.options['optional_names_and_values']
@FC.options['link']
def create_text(directory, page_filter, key_filter, names_and_values, link):
    """Create a new text line with configuration"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.create_text_line(key, names_and_values, link))


@cli.command()
@FC.options['text_line_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
def copy_text(directory, page_filter, key_filter, text_line_filter, to_page_filter, to_key_filter, names_and_values):
    """Copy a text line"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY)
    text_line = FC.find_text_line(FC.find_key(FC.find_page(deck, page_filter), key_filter), text_line_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else text_line.page
    if to_key_filter:
        to_key = FC.find_key(to_page, to_key_filter)
    elif to_page_filter:
        to_key = FC.find_key(to_page, '%s,%s' % text_line.key.key)
    else:
        to_key = text_line.key
    print(FC.copy_text_line(text_line, to_key, names_and_values))


@cli.command()
@FC.options['text_line_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
def move_text(directory, page_filter, key_filter, text_line_filter, to_page_filter, to_key_filter, names_and_values):
    """Move a text line to another key"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY)
    text_line = FC.find_text_line(FC.find_key(FC.find_page(deck, page_filter), key_filter), text_line_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else text_line.page
    if to_key_filter:
        to_key = FC.find_key(to_page, to_key_filter)
    elif to_page_filter:
        to_key = FC.find_key(to_page, '%s,%s' % text_line.key.key)
    else:
        to_key = text_line.key
    print(FC.move_text_line(text_line, to_key, names_and_values))


@cli.command()
@FC.options['text_line_filter']
def delete_text(directory, page_filter, key_filter, text_line_filter):
    """Delete a text line."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    text_line.delete_on_disk()


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


@cli.command()
@FC.options['key_filter']
@click.option('-e', '--event', 'event_kind', type=click.Choice(['press', 'longpress', 'release', 'start']), required=True, help='The kind of event to create')
@FC.options['optional_names_and_values']
@FC.options['link']
def create_event(directory, page_filter, key_filter, event_kind, names_and_values, link):
    """Create a new event with configuration"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.create_event(key, event_kind, names_and_values, link))


@cli.command()
@FC.options['event_filter']
@FC.options['to_page_to_key']
@click.option('-te', '--to-event', 'to_kind', type=click.Choice(['press', 'longpress', 'release', 'start']), required=False, help='The optional kind of the new event')
@FC.options['optional_names_and_values']
def copy_event(directory, page_filter, key_filter, event_filter, to_page_filter, to_key_filter, to_kind, names_and_values):
    """Copy an event"""
    deck = FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    event = FC.find_event(FC.find_key(FC.find_page(deck, page_filter), key_filter), event_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else event.page
    if to_key_filter:
        to_key = FC.find_key(to_page, to_key_filter)
    elif to_page_filter:
        to_key = FC.find_key(to_page, '%s,%s' % event.key.key)
    else:
        to_key = event.key
    if not to_kind:
        to_kind = event.kind
    print(FC.copy_event(event, to_key, to_kind, names_and_values))


@cli.command()
@FC.options['event_filter']
@FC.options['to_page_to_key']
@click.option('-te', '--to-event', 'to_kind', type=click.Choice(['press', 'longpress', 'release', 'start']), required=False, help='The optional kind of the new event')
@FC.options['optional_names_and_values']
def move_event(directory, page_filter, key_filter, event_filter, to_page_filter, to_key_filter, to_kind, names_and_values):
    """Move an event to another key"""
    deck = FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    event = FC.find_event(FC.find_key(FC.find_page(deck, page_filter), key_filter), event_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else event.page
    if to_key_filter:
        to_key = FC.find_key(to_page, to_key_filter)
    elif to_page_filter:
        to_key = FC.find_key(to_page, '%s,%s' % event.key.key)
    else:
        to_key = event.key
    if not to_kind:
        to_kind = event.kind
    print(FC.move_event(event, to_key, to_kind, names_and_values))


@cli.command()
@FC.options['event_filter']
def delete_event(directory, page_filter, key_filter, event_filter):
    """Delete an event."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    event.delete_on_disk()
