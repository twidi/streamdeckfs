# Changelog


## Release `1.8` - *IN PROGRESS*

- [WIP] web server


## Release `1.7.1` - *2021-07-14*

- Handle semicolon and slash replacements in texts
- Add the `enabled` configuration option, reverse of `disabled`


## Release `1.7` - *2021-07-13*

- Allow setting many vars in key events, independently from actions
- Set current directory to the deck/page/key triggering a command
- Add `quiet` configuration option for events
- Add floor divistion (via `||`, so we removed `concat`) and string conversion (via `str()`) to replace `concat` to expressions
- Add formating (via special `format()` function) to expressions
- Allow disabling emojis in texts


## Release `1.6.1` - *2021-07-11*

- Small tweaks and fixes regarding variables
- Add sdfs env vars available as variables


## Release `1.6` - *2021-07-05*

- Expressions evaluation


## Release `1.5.3` - *2021-06-30*

- Conditionals when defining variables
- Lines indexing on multi-lines variable files
- Adding/removing numbers to a variable (for example to increment a counter, or get the last line index of a file)


## Release `1.5.2` - *2021-06-25*

- Read variables recursively and in equality values


## Release `1.5.1` - *2021-06-25*

- Fix some variables that cannot be set via api
- New action on key events to set a variable


## Release `1.5` - *2021-06-23*

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