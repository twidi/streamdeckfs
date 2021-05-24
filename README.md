# SteamDeckify

SteamDeckify is a tool, written in Python (3.9+), to configure a StreamDeck ([by Elgato](https://www.elgato.com/fr/stream-deck)) for Linux.

It's not a graphical interface but if you can use a filesystem and create directories and files (no content needed!, see later), you'll have all the needed power.

It provides numerous features:

- page management, with overlays
- image composition, with layers, drawings, and texts, with, for all of them, a lot of configuration options
- advanced key management (on press, release, long press, repeat, delay...)
- references (explained later, but see this as a way to have template, repeat keys on pages...)


When starting, the program will look at the directory passed on the command line and will read all the configuration from directories and files.

And while running, it will catch any changes to them to update in real time the StreamDeck

# Pre-requisites

- Linux (may be compatible with some other OS)
- Python 3.9

# Installation

## System

**TODO**: check in the `streamdeck` lib info about system configuration

## StreamDeckify

There is no install script yet, so copy the file `streamdeckify`  where you want and install (in a virtual environment or not) these python libraries with pip (or your favorite tool):

- streamdeck
- pillow
- inotify-simple
- click
- click-log
- psutil

Complete command line with pip: `pip install streamdeck pillow inotify-simple click click-log psutil`

In addition you can install:

- python-prctl (to name the threads if want to see them in ps, top...)


**TODO**: Note about the fonts not yet in the repository

# Starting

## Knowing your StreamDeck(s)

First thing to do is to know about your StreamDecks.

For this, use the `inspect` command:

```bash
path/to/streamdeckify.py inspect
```

It will output some information about the connected decks (no other program must be connected the them as only one conection to the decks is possible).

The main useful thing is the serial number, as it will be the name of the directory containing its configuration (you'll place it where you want)

## Preparing the configuration directory

You can create the directories by hand (we'll explain how later) but we provide a command to create the tree for you

```bash
path/to/streamdeckify.py make-dirs SERIAL BASE_DIRECTORY
```

`SERIAL` is the serial number you got from the `inspect` command. Note that if you have only one connected StreamDeck this argument can be ignored as it will be automatically found for you.

`BASE_DIRECTORY` is the directory that will contain the configuration directory of this StreamDeck. So it will create (if it does not exist yet) a directory named `YOUR_SERIAL_NUMBER` in `BASE_DIRECTORY`

Before creating (or updating) the tree, you'll be asked to confirm (unless you pass `--yes` on the command line).

Only one page will be create, unless you pass `--page XX`, `XX` being the number of pages to create.

Once confirmed, the program will create all missing directories.

It will look like this, for example for a deck with 3 rows of 5 keys:

```
BASE_DIRECTORY/YOUR_SERIAL_NUMBER
├── PAGE_1
│   ├── KEY_ROW_1_COL_1
│   ├── KEY_ROW_1_COL_2
│   ├── KEY_ROW_1_COL_3
│   ├── KEY_ROW_1_COL_4
│   ├── KEY_ROW_1_COL_5
│   ├── KEY_ROW_2_COL_1
│   ├── KEY_ROW_2_COL_2
│   ├── KEY_ROW_2_COL_3
│   ├── KEY_ROW_2_COL_4
│   ├── KEY_ROW_2_COL_5
│   ├── KEY_ROW_3_COL_1
│   ├── KEY_ROW_3_COL_2
│   ├── KEY_ROW_3_COL_3
│   ├── KEY_ROW_3_COL_4
│   ├── KEY_ROW_3_COL_5
├── PAGE_2
...
```

Know you are ready to configure the keys. Here is the simplest configuration:

- copy an image file (or make a symbolic link) into a `KEY...` directory and name it `IMAGE`. It's the image that will be displayed for the key.
- copy a script/program (or make a symbolic link) into a `KEY...` directory and name it `ON_PRESS`. It's the script/program that will be executed when the key will be pressed.

That's it, you now know how to configure your StreamDeck in the simplest way possible. We'll see later the numerous configuration options.

## Running streamdeckify

Now that you have your configuration directory, run:

```bash
path/to/streamdeckify.py run SERIAL CONFIG_DIRECTORY
```

And voila!

Note that like for `make-dirs`, the `SERIAL` argument is optional if you have only one connected StreamDeck.

And `CONFIG_DIRECTORY` can be either the directory containing the one named after the serial number, or this last one. The program will automatically search for the final directory if the "parent" one is given.

Now that you have your StreamDeck running, try adding an image for another key... you'll see that the deck automatically updates. And maybe you're starting to see the infinite possibilities
