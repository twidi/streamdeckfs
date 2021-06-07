#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import json
import re
import shutil
from copy import deepcopy
from pathlib import Path
from random import randint

import click
import click_log

from ..common import logger, Manager
from ..entities import Deck, FILTER_DENY, PAGE_CODES
from .base import cli, validate_positive_integer

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

NoneType = type(None)


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
        'dry_run': click.option('--dry-run', is_flag=True, help='Only validate arguments and return what would have been returned by the command but without touching anything'),
        'disabled_flag': click.option('--with-disabled/--without-disabled', 'with_disabled', default=False, help='Include disabled ones or not'),
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
    def rename_entity(cls, entity, new_filename=None, new_path=None, dry_run=False):
        assert (new_filename or new_path) and not (new_filename and new_path)
        if new_filename:
            new_path = entity.path.parent / new_filename
        if new_path != entity.path:
            if not dry_run:
                entity.path = entity.path.replace(new_path)
            return True, new_path
        return False, new_path

    @classmethod
    def delete_entity(cls, entity, dry_run=False):
        if not dry_run and entity.path.exists():
            if entity.is_dir and not entity.path.is_symlink():
                shutil.rmtree(entity.path)
            else:
                entity.path.unlink()
        return entity.path

    @classmethod
    def check_new_path(cls, path, is_dir, msg_name_part):
        if path.exists():
            Manager.exit(1, f'[{msg_name_part}] Cannot create {"directory" if is_dir else "file"} "{path}" because it already exists')

    @classmethod
    def create_entity(cls, entity_class, parent, identifier, main_args, names_and_values, link, msg_name_part, dry_run=False):
        entity_filename = entity_class.compose_filename(main_args, {})
        entity = entity_class(**entity_class.get_create_base_args(parent.path / entity_filename, parent, identifier))
        args = cls.validate_names_and_values(entity, main_args, names_and_values, msg_name_part)[0]
        path = parent.path / entity.compose_filename(main_args, args)
        cls.check_new_path(path, entity_class.is_dir, msg_name_part)
        if not dry_run:
            if entity_class.is_dir:
                path.mkdir()
            elif link:
                path.symlink_to(link)
            else:
                path.touch()
        return path

    @classmethod
    def copy_entity(cls, entity, parent, main_override, names_and_values, msg_name_part, dry_run=False):
        main, args = cls.update_args(entity, names_and_values, msg_name_part)
        main |= main_override
        path = parent.path / cls.compose_filename(entity, main, args)
        cls.check_new_path(path, entity.is_dir, msg_name_part)
        if not dry_run:
            if entity.is_dir:
                shutil.copytree(entity.path, path, symlinks=True)
            else:
                shutil.copy2(entity.path, path, follow_symlinks=False)
        return path

    @classmethod
    def move_entity(cls, entity, parent, main_override, names_and_values, msg_name_part, dry_run=False):
        main, args = cls.update_args(entity, names_and_values, msg_name_part)
        main |= main_override
        path = parent.path / cls.compose_filename(entity, main, args)
        cls.check_new_path(path, entity.is_dir, msg_name_part)
        if not dry_run:
            cls.rename_entity(entity, new_path=path)
        return path

    @classmethod
    def validate_number_expression(cls, ctx, param, value):
        if not value:
            return 'first', None, None, value
        try:
            value = int(value)
            try:
                validate_positive_integer(ctx, param, value)
            except click.BadParameter:
                raise
            else:
                return 'exact', int(value), None, value
        except ValueError:
            pass
        for r in cls.validate_number_expression_regexs.values():
            if (match := r.match(value)):
                parts = match.groupdict()
                return (
                    'random' if 'random' in parts else 'first',
                    int(parts['low']) if 'low' in parts else None,
                    int(parts['high']) if 'high' in parts else None,
                    value
                )
        raise click.BadParameter(f'{value} is not a positive integer or one of these expression: "", "NUMBER+", "NUMBER+NUMBER", "?", "NUMBER?", "?NUMBER" or "NUMBER?NUMBER"')

    validate_number_expression_regexs = {
        'first_after': re.compile(r'^(?P<low>\d+)(?P<first>\+)$'),
        'first_between': re.compile(r'^(?P<low>\d+)(?P<first>\+)(?P<high>\d+)$'),
        'random': re.compile(r'^(?P<random>\?)$'),
        'ramdom_after': re.compile(r'^(?P<low>\d+)(?P<random>\?)$'),
        'random_between': re.compile(r'^(?P<low>\d+)(?P<random>\?)(?P<high>\d+)$'),
        'ramdom_before': re.compile(r'^(?P<random>\?)(?P<high>\d+)$'),
    }

    @staticmethod
    def get_one_number(used, mode, low, high, min_low, max_high):
        if low is None:
            low = min_low
        if high is None:
            high = max_high
        if mode == 'exact':
            # no constraints when using exact mode
            return low
        if low < min_low or high > max_high:
            return None
        if mode == 'first':
            for number in sorted(used):
                if number > low:
                    return number if number < high else None
        elif mode == 'random':
            inc_low, inc_high = low + 1, high - 1
            used = set(number for number in used if number > low and number < high)
            # ensure we have at least one possible before launching our while loop
            if len(used) < high - low - 1:
                while True:
                    if (number := randint(inc_low, inc_high)) not in used:
                        return number
        return None

    @classmethod
    def get_one_page(cls, deck, mode, low, high, original):
        if (number := cls.get_one_number((number for number, page in deck.pages.items() if page and not page.disabled), mode, low, high, 0, 100000)):
            return number
        Manager.exit(1, f'Cannot find an available page matching "{original}"')

    @classmethod
    def validate_key_expression(cls, ctx, param, value):
        if not value:
            return None
        if value in ('?', '+'):
            return value
        if cls.validate_key_regex.match(value):
            return tuple(map(int, value.split(',')))

        raise click.BadParameter(f'{value} is not in the format "row,col", or one of "+" or "?"')

    validate_key_regex = re.compile(r'^\d+,\d+$')

    @classmethod
    def get_one_key(cls, page, key):
        if not key:
            return None
        if isinstance(key, str):
            used_keys = set(row_col for row_col, page_key in page.keys.items() if page_key and not page_key.disabled)
            if len(used_keys) < page.deck.nb_cols * page.deck.nb_rows:
                if key == '+':  # get first available key
                    for row in range(1, page.deck.nb_rows + 1):
                        for col in range(1, page.deck.nb_cols + 1):
                            if (key := (row, col)) not in used_keys:
                                return key
                if key == '?':  # get random key
                    while True:
                        if (key := (randint(1, page.deck.nb_rows), randint(1, page.deck.nb_cols))) not in used_keys:
                            return key
        else:
            return key
        Manager.exit(1, f'Cannot find an available key matching "{key}"')

    @classmethod
    def create_page(cls, deck, number, names_and_values, dry_run=False):
        from ..entities import Page
        return cls.create_entity(Page, deck, number, {'page': number}, names_and_values, None, f'{deck}, NEW PAGE {number}', dry_run=dry_run)

    @classmethod
    def copy_page(cls, page, to_number, names_and_values, dry_run=False):
        return cls.copy_entity(page, page.deck, {'page': to_number}, names_and_values, f'{page.deck}, NEW PAGE {to_number}', dry_run=dry_run)

    @classmethod
    def move_page(cls, page, to_number, names_and_values, dry_run=False):
        return cls.move_entity(page, page.deck, {'page': to_number}, names_and_values, f'{page.deck}, NEW PAGE {to_number}', dry_run=dry_run)

    @classmethod
    def create_key(cls, page, to_row, to_col, names_and_values, dry_run=False):
        from ..entities import Key
        return cls.create_entity(Key, page, (to_row, to_col), {'row': to_row, 'col': to_col}, names_and_values, None, f'{page}, NEW KEY', dry_run=dry_run)

    @classmethod
    def copy_key(cls, key, to_page, to_row, to_col, names_and_values, dry_run=False):
        return cls.copy_entity(key, to_page, {'row': to_row, 'col': to_col}, names_and_values, f'{to_page}, NEW KEY {key}', dry_run=dry_run)

    @classmethod
    def move_key(cls, key, to_page, to_row, to_col, names_and_values, dry_run=False):
        return cls.move_entity(key, to_page, {'row': to_row, 'col': to_col}, names_and_values, f'{to_page}, NEW KEY {key}', dry_run=dry_run)

    @classmethod
    def create_layer(cls, key, names_and_values, link, dry_run=False):
        from ..entities import KeyImageLayer
        return cls.create_entity(KeyImageLayer, key, -1, {}, names_and_values, link, f'{key}, NEW LAYER', dry_run=dry_run)

    @classmethod
    def copy_layer(cls, layer, to_key, names_and_values, dry_run=False):
        return cls.copy_entity(layer, to_key, {}, names_and_values, f'{to_key}, NEW LAYER', dry_run=dry_run)

    @classmethod
    def move_layer(cls, layer, to_key, names_and_values, dry_run=False):
        return cls.move_entity(layer, to_key, {}, names_and_values, f'{to_key}, NEW LAYER', dry_run=dry_run)

    @classmethod
    def create_text_line(cls, key, names_and_values, link, dry_run=False):
        from ..entities import KeyTextLine
        return cls.create_entity(KeyTextLine, key, -1, {}, names_and_values, link, f'{key}, NEW TEXT LINE', dry_run=dry_run)

    @classmethod
    def copy_text_line(cls, text_line, to_key, names_and_values, dry_run=False):
        return cls.copy_entity(text_line, to_key, {}, names_and_values, f'{to_key}, NEW TEXT LINE', dry_run=dry_run)

    @classmethod
    def move_text_line(cls, text_line, to_key, names_and_values, dry_run=False):
        return cls.move_entity(text_line, to_key, {}, names_and_values, f'{to_key}, NEW TEXT LINE', dry_run=dry_run)

    @classmethod
    def create_event(cls, key, kind, names_and_values, link, dry_run=False):
        from ..entities import KeyEvent
        return cls.create_entity(KeyEvent, key, kind, {'kind': kind}, names_and_values, link, f'{key}, NEW EVENT {kind}', dry_run=dry_run)

    @classmethod
    def copy_event(cls, event, to_key, to_kind, names_and_values, dry_run=False):
        return cls.copy_entity(event, to_key, {'kind': to_kind}, names_and_values, f'{to_key}, NEW EVENT {to_kind}', dry_run=dry_run)

    @classmethod
    def move_event(cls, event, to_key, to_kind, names_and_values, dry_run=False):
        return cls.move_entity(event, to_key, {'kind': to_kind}, names_and_values, f'{to_key}, NEW EVENT {to_kind}', dry_run=dry_run)

    @classmethod
    def iter_content(cls, entity, content, with_disabled):
        for identifier, obj in sorted([(identifier, obj) for identifier, obj in content.items()]):
            if not obj and not with_disabled:
                continue
            if with_disabled:
                for version in obj.all_versions:
                    yield version
            else:
                yield obj

    @classmethod
    def list_content(cls, entity, content, with_disabled):
        for obj in cls.iter_content(entity, content, with_disabled):
            print(FC.get_args_as_json(obj))


FC = FilterCommands
FC.combine_options()


@cli.command()
@FC.options['directory']
def get_deck_info(directory):
    """Get some information about the deck"""
    try:
        print(Manager.get_info_from_model_file(directory))
    except Exception:
        Manager.exit(1, f'Unable to read information from directory "{directory}')

@cli.command()
@FC.options['directory']
def get_current_page(directory):
    """Get the current page"""
    try:
        page_info = json.loads((Path(directory) / Deck.current_page_file_name).read_text().strip())
        if set(page_info.keys()) != {'number', 'name', 'is_overlay'}:
            raise ValueError
        if (number := page_info['number']) is None:
            if page_info['name'] is not None or page_info['is_overlay'] is not None:
                raise ValueError
        else:
            if not isinstance(number, int) or not isinstance(page_info['name'], (str, NoneType)) or not isinstance(page_info['is_overlay'], bool):
                raise ValueError
        print(json.dumps(page_info))
    except Exception:
        Manager.exit(1, f'Unable to read current page information from directory "{directory}"')


@cli.command()
@FC.options['directory']
@click.option('-p', '--page', 'page_filter', type=str, required=True, help='A page number or a name, or one of ' + ', '.join(f'"{page_code}"' for page_code in PAGE_CODES))
@click.option('--overlay/--no-overlay', 'overlay', default=False, help='Set page as an overlay or not')
def set_current_page(directory, page_filter, overlay):
    """Set the current page"""
    if page_filter not in PAGE_CODES:
        FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    page_info = {
        'page': page_filter,
        'is_overlay': overlay
    }
    path = Path(directory) / Deck.set_current_page_file_name
    try:
        if path.exists():
            path.unlink()
        path.write_text(json.dumps(page_info))
    except Exception:
        Manager.exit(1, f'Unable to write current page information into directory "{directory}"')


@cli.command()
@FC.options['directory']
@FC.options['disabled_flag']
def list_pages(directory, with_disabled):
    """List the page of the deck"""
    deck = FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    FC.list_content(deck, deck.pages, with_disabled=with_disabled)


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
@FC.options['dry_run']
def set_page_conf(directory, page_filter, names_and_values, dry_run):
    """Set the value of some entries of a page configuration."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.rename_entity(page, FC.get_update_args_filename(page, names_and_values), dry_run=dry_run)[1])


PAGE_NUMBER_EXPRESSION_HELP = """\
Expression can be an empty string (same as not passing this option) to use the next available page in order,
or "NUMBER+" for the first page available after this number,
or "NUMBER+NUMBER" for the first page available between the two numbers (exclusive),
or "?" for a random available page,
or "NUMBER? for a random available page after this number,
or "?NUMBER" for a random available page before this number,
or "NUMBER?NUMBER" for a random available page between the two numbers (exclusive).
If no available page match the request, a error will be raised."""


@cli.command()
@FC.options['directory']
@click.option('-p', '--page', type=str, required=False, help="The page number or an expression to find one available." + PAGE_NUMBER_EXPRESSION_HELP, callback=FC.validate_number_expression)
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def create_page(directory, page, names_and_values, dry_run):
    """Create a new image layer with configuration"""
    deck = FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    print(FC.create_page(deck, FC.get_one_page(deck, *page), names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['page_filter']
@click.option('-tp', '--to-page', 'to_page_number', type=str, required=False, help="The page number of the new page or an expression to find one available." + PAGE_NUMBER_EXPRESSION_HELP, callback=FC.validate_number_expression)
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def copy_page(directory, page_filter, to_page_number, names_and_values, dry_run):
    """Copy a page and all its content"""
    deck = FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    page = FC.find_page(deck, page_filter)
    print(FC.copy_page(page, FC.get_one_page(deck, *to_page_number), names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['page_filter']
@click.option('-tp', '--to-page', 'to_page_number', type=str, required=False, help="The page number of the new page or an expression to find one available." + PAGE_NUMBER_EXPRESSION_HELP, callback=FC.validate_number_expression)
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def move_page(directory, page_filter, to_page_number, names_and_values, dry_run):
    """Move a page to a different number"""
    deck = FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    page = FC.find_page(deck, page_filter)
    print(FC.move_page(page, FC.get_one_page(deck, *to_page_number), names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['page_filter']
@FC.options['dry_run']
def delete_page(directory, page_filter, dry_run):
    """Fully delete a page directory."""
    page = FC.find_page(FC.get_deck(directory, key_filter=FILTER_DENY, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    print(FC.delete_entity(page, dry_run=dry_run))


@cli.command()
@FC.options['page_filter']
@FC.options['disabled_flag']
def list_keys(directory, page_filter, with_disabled):
    """List the keys of a page"""
    page = FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    FC.list_content(page, page.keys, with_disabled=with_disabled)


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
@FC.options['dry_run']
def set_key_conf(directory, page_filter, key_filter, names_and_values, dry_run):
    """Set the value of some entries of a key configuration."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.rename_entity(key, FC.get_update_args_filename(key, names_and_values), dry_run=dry_run)[1])


KEY_EXPRESSION_HELP = """\
Expression can be '+' to use the next available key in order (row by row), or "?" for a random available key.
If no available key match the request, a error will be raised."""


@cli.command()
@FC.options['page_filter']
@click.option('-k', '--key', type=str, required=True, help='The key position as "row,col" or an expression to find an available key.' + KEY_EXPRESSION_HELP, callback=FC.validate_key_expression)
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def create_key(directory, page_filter, key, names_and_values, dry_run):
    """Create a new image layer with configuration"""
    page = FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter)
    to_row, to_col = FC.get_one_key(page, key or '+')
    print(FC.create_key(page, to_row, to_col, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['key_filter']
@FC.options['to_page']
@click.option('-tk', '--to-key', 'to_key', type=str, required=False, help='The optional destination key position as "row,col" or an expression to find an available key.' + KEY_EXPRESSION_HELP, callback=FC.validate_key_expression)
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def copy_key(directory, page_filter, key_filter, to_page_filter, to_key, names_and_values, dry_run):
    """Copy a key and all its content"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    key = FC.find_key(FC.find_page(deck, page_filter), key_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else key.page
    to_row, to_col = FC.get_one_key(to_page, to_key) or key.key
    print(FC.copy_key(key, to_page, to_row, to_col, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['key_filter']
@FC.options['to_page']
@click.option('-tk', '--to-key', 'to_key', type=str, required=False, help='The optional destination key position as "row,col" or an expression to find an available key.' + KEY_EXPRESSION_HELP, callback=FC.validate_key_expression)
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def move_key(directory, page_filter, key_filter, to_page_filter, to_key, names_and_values, dry_run):
    """Move a key to another page and/or a different position"""
    deck = FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY)
    key = FC.find_key(FC.find_page(deck, page_filter), key_filter)
    to_page = FC.find_page(deck, to_page_filter) if to_page_filter else key.page
    to_row, to_col = FC.get_one_key(to_page, to_key) or key.key
    print(FC.move_key(key, to_page, to_row, to_col, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['key_filter']
@FC.options['dry_run']
def delete_key(directory, page_filter, key_filter, dry_run):
    """Fully delete of a key directory."""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.delete_entity(key, dry_run=dry_run))


@cli.command()
@FC.options['key_filter']
@FC.options['disabled_flag']
def list_images(directory, page_filter, key_filter, with_disabled):
    """List the image layers of a key"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    FC.list_content(key, key.layers, with_disabled=with_disabled)


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
@FC.options['dry_run']
def set_image_conf(directory, page_filter, key_filter, layer_filter, names_and_values, dry_run):
    """Set the value of some entries of an image configuration."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(FC.rename_entity(layer, FC.get_update_args_filename(layer, names_and_values), dry_run=dry_run)[1])


@cli.command()
@FC.options['key_filter']
@FC.options['optional_names_and_values']
@FC.options['link']
@FC.options['dry_run']
def create_image(directory, page_filter, key_filter, names_and_values, link, dry_run):
    """Create a new image layer with configuration"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.create_layer(key, names_and_values, link, dry_run=dry_run))


@cli.command()
@FC.options['layer_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def copy_image(directory, page_filter, key_filter, layer_filter, to_page_filter, to_key_filter, names_and_values, dry_run):
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
    print(FC.copy_layer(layer, to_key, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['layer_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def move_image(directory, page_filter, key_filter, layer_filter, to_page_filter, to_key_filter, names_and_values, dry_run):
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
    print(FC.move_layer(layer, to_key, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['layer_filter']
@FC.options['dry_run']
def delete_image(directory, page_filter, key_filter, layer_filter, dry_run):
    """Delete an image layer."""
    layer = FC.find_layer(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), layer_filter)
    print(FC.delete_entity(layer, dry_run=dry_run))


@cli.command()
@FC.options['key_filter']
@FC.options['disabled_flag']
def list_texts(directory, page_filter, key_filter, with_disabled):
    """List the text lines of a key"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter)
    FC.list_content(key, key.text_lines, with_disabled=with_disabled)


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
@FC.options['dry_run']
def set_text_conf(directory, page_filter, key_filter, text_line_filter, names_and_values, dry_run):
    """Set the value of some entries of an image configuration."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(FC.rename_entity(text_line, FC.get_update_args_filename(text_line, names_and_values), dry_run=dry_run)[1])


@cli.command()
@FC.options['key_filter']
@FC.options['optional_names_and_values']
@FC.options['link']
@FC.options['dry_run']
def create_text(directory, page_filter, key_filter, names_and_values, link, dry_run):
    """Create a new text line with configuration"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.create_text_line(key, names_and_values, link, dry_run=dry_run))


@cli.command()
@FC.options['text_line_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def copy_text(directory, page_filter, key_filter, text_line_filter, to_page_filter, to_key_filter, names_and_values, dry_run):
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
    print(FC.copy_text_line(text_line, to_key, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['text_line_filter']
@FC.options['to_page_to_key']
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def move_text(directory, page_filter, key_filter, text_line_filter, to_page_filter, to_key_filter, names_and_values, dry_run):
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
    print(FC.move_text_line(text_line, to_key, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['text_line_filter']
@FC.options['dry_run']
def delete_text(directory, page_filter, key_filter, text_line_filter, dry_run):
    """Delete a text line."""
    text_line = FC.find_text_line(FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY), page_filter), key_filter), text_line_filter)
    print(FC.delete_entity(text_line, dry_run=dry_run))


@cli.command()
@FC.options['key_filter']
@FC.options['disabled_flag']
def list_events(directory, page_filter, key_filter, with_disabled):
    """List the events of a key"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    FC.list_content(key, key.events, with_disabled=with_disabled)


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
@FC.options['dry_run']
def set_event_conf(directory, page_filter, key_filter, event_filter, names_and_values, dry_run):
    """Set the value of some entries of an event configuration."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    print(FC.rename_entity(event, FC.get_update_args_filename(event, names_and_values), dry_run=dry_run)[1])


@cli.command()
@FC.options['key_filter']
@click.option('-e', '--event', 'event_kind', type=click.Choice(['press', 'longpress', 'release', 'start']), required=True, help='The kind of event to create')
@FC.options['optional_names_and_values']
@FC.options['link']
@FC.options['dry_run']
def create_event(directory, page_filter, key_filter, event_kind, names_and_values, link, dry_run):
    """Create a new event with configuration"""
    key = FC.find_key(FC.find_page(FC.get_deck(directory, event_filter=FILTER_DENY, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter)
    print(FC.create_event(key, event_kind, names_and_values, link, dry_run=dry_run))


@cli.command()
@FC.options['event_filter']
@FC.options['to_page_to_key']
@click.option('-te', '--to-event', 'to_kind', type=click.Choice(['press', 'longpress', 'release', 'start']), required=False, help='The optional kind of the new event')
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def copy_event(directory, page_filter, key_filter, event_filter, to_page_filter, to_key_filter, to_kind, names_and_values, dry_run):
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
    print(FC.copy_event(event, to_key, to_kind, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['event_filter']
@FC.options['to_page_to_key']
@click.option('-te', '--to-event', 'to_kind', type=click.Choice(['press', 'longpress', 'release', 'start']), required=False, help='The optional kind of the new event')
@FC.options['optional_names_and_values']
@FC.options['dry_run']
def move_event(directory, page_filter, key_filter, event_filter, to_page_filter, to_key_filter, to_kind, names_and_values, dry_run):
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
    print(FC.move_event(event, to_key, to_kind, names_and_values, dry_run=dry_run))


@cli.command()
@FC.options['event_filter']
@FC.options['dry_run']
def delete_event(directory, page_filter, key_filter, event_filter, dry_run):
    """Delete an event."""
    event = FC.find_event(FC.find_key(FC.find_page(FC.get_deck(directory, layer_filter=FILTER_DENY, text_line_filter=FILTER_DENY), page_filter), key_filter), event_filter)
    print(FC.delete_entity(event, dry_run=dry_run))
