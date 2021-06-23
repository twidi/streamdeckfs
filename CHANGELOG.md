# Changelog


## Release `1.5` - *IN PROGRESS*

- Add variables as files in decks, pages and keys directories, usable in configuration values
- API to read/change brightness
- Change the way pages are opened as overlay (configured from the page itself, not the event)


## Release `1.4` - *2021-06-19*

- Add `ON_START` and `ON_END` events for pages
- Add `ON_START` and `ON_END` events for the deck
- Add `SDFS_DEVICE_TYPE` env var


## Release `1.3` - *2021-06-18*

- Fix some overlays issues
- Pass environment variables to commands
- `ON_START` events have `unique=true` by default
- Add `ON_END` events


## Release `1.2` - *2021-06-10*

- API to get/set the current displayed page
- Fix missing keys when copying a large page directory
- Support for emojis
- New `fit` configuration option for texts


## Release `1.1` - *2021-06-06*

- Complete API: get deck info + list/create/delete/move/duplicate pages/keys/images/texts/events (in addition to get/update) 
- Run multiple StreamDeck at the same time
- Wait for StreamDecks and directories to be available at start and if made unavailable
- Add `__inside__` possible value for `file` and `command` configuration option to read from the file content instead of the file name
- New project name: `StreamDeckFS` instead of `StreamDeckify` (used by a Spotify plugin for the official app)


## Release `1.0` - *2021-06-03*

- First public version