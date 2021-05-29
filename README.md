# SteamDeckify

SteamDeckify is a tool, written in Python (3.9+), to configure a StreamDeck ([by Elgato](https://www.elgato.com/fr/stream-deck)) for Linux.

It's not a graphical interface, but if you can use a file system and create directories and files (no content needed, see later), you'll have all the necessary power.

It provides numerous features:

- page management, with overlays
- image composition, with layers, drawings, and texts, with, for all of them, many configuration options
- advanced key management (on press, release, long press, repeat, delay, and more)
- references (explained later, but see this as a way to have templates, or to repeat keys on pages, or have many times the same key with a few differences)


The program will look at the directory passed on the command line and read all the configuration from directories and files.

And while running, it will catch any changes to them to update in real-time the StreamDeck

# Prerequisites

- Linux (may be compatible with some other OS)
- Python 3.9

# Installation

## System

**TODO**: check in the `streamdeck` lib info about system configuration

## StreamDeckify

There is no install script yet, so copy the file `streamdeckify.py`  where you want and install (in a virtual environment or not) these python libraries with pip (or your favorite tool):

- `streamdeck`
- `pillow`
- `inotify-simple`
- `click`
- `click-log`
- `psutil`

Complete command-line with pip: `pip install streamdeck pillow inotify-simple click click-log psutil`

In addition, you can install:

- `python-prctl` (to name the threads if you want to see them in ps, top...)


**TODO**: Note about the fonts not yet in the repository

# Starting

## Knowing your StreamDeck(s)

The first thing to do is to discover your StreamDecks.

For this, use the `inspect` command:

```bash
path/to/streamdeckify.py inspect
```

It will output some information about the connected decks (no other program must be connected to them as only one connection to the decks is possible).

The main useful thing is the serial number, as it will be the name of the directory containing its configuration (you'll place it where you want)

## Preparing the configuration directory

You can create the directories by hand (we'll explain how later), but we provide a command to create the tree for you, `make-dirs`:

```bash
path/to/streamdeckify.py make-dirs SERIAL BASE_DIRECTORY
```

`SERIAL` is the serial number you got from the `inspect` command. Note that if you have only one connected StreamDeck, you can ignore this argument as the program will automatically find it for you.

`BASE_DIRECTORY` is the directory that will contain the configuration directory of this StreamDeck. So it will create (if it does not exist yet) a directory named `YOUR_SERIAL_NUMBER` in `BASE_DIRECTORY`.

Before creating (or updating) the tree, you'll be asked to confirm (unless you pass `--yes` on the command line).

Only one page will be created unless you pass `--page XX`, `XX` being the number of pages to create.

Once confirmed, the program will create all missing directories.

The resulting tree will look like this, for example for a deck with 3 rows of 5 keys:

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

Now you are ready to configure the keys. Here is the most straightforward configuration:

- copy an image file (or make a symbolic link) into a `KEY...` directory (in the `PAGE_1` directory, as the first displayed page is the one with the lowest number) and name it `IMAGE`. It's the image that will be displayed for the key.
- copy a script/program (or make a symbolic link) into the same `KEY...` directory and name it `ON_PRESS`. It's the script/program that will be executed when the key will be pressed.

That's it; you now know how to configure your StreamDeck in the simplest way possible. We'll see later the numerous configuration options.

PS: you can have `IMAGE` without `ON_PRESS` or `ON_PRESS` without `IMAGE`.

## Running streamdeckify

Now that you have your configuration directory, run:

```bash
path/to/streamdeckify.py run SERIAL CONFIG_DIRECTORY
```

And voila!

Note that, like for `make-dirs`, the `SERIAL` argument is optional if you have only one connected StreamDeck.

And `CONFIG_DIRECTORY` can be either the exact directory, i.e., `BASE_DIRECTORY/YOUR_SERIAL_NUMBER`, or the directory used in `make-dirs` i.e., `BASE_DIRECTORY` (the program will then complete it. It's helpful if you have only one deck connected and don't want to have to remember the serial number, so in this case, the command can be only `path/to/streamdeckify.py run BASE_DIRECTORY`)

Now that you have your StreamDeck running, try adding an image for another key (on the first page). You'll see that the deck automatically updates. And maybe you're starting to see the infinite possibilities.


# Configuration

There are two things you can configure on your StreamDeck: "appearance", i.e., what is displayed on the keys, and "events" that happen when you "act" with them.

But first, let's talk a bit about the way configuration works.

## Configuration format

Everything is done in the name of the file. Say you want to configure the `IMAGE` with the option `foo` taking the value `bar`, then you have to rename the file from `IMAGE` to `IMAGE;foo=bar`.

And if you want to add a name (explained later) of `my-key`, the file's name will be `IMAGE;foo=bar;name=my-key`.

So the three things to know are:

- if the image (or event, or text) has some configuration options, the "normal" name (like `IMAGE`, `ON_PRESS`...) is separated from the configuration with a semi-colon: `IMAGE;here=is;the=config`
- key/value configuration pairs are grouped with a `=` between the key and the value: `key=value`
- different configuration pairs are separated by a semi-colon: `key1=value1;key2=value2`

Note that the order of the configuration options is not essential.

About the limitations, there are a few things to consider:

- some characters are not allowed in a file name (only `/` on Linux)
- the length of a file name is limited (generally 256 characters on Linux)

## Common configuration options

Two configuration options are common to every directory or file:

### `name`

It's the name of the page, key, image, event, or text. It can be whatever you want and can be used to "reference" other pages, keys, images, events, and texts. For example, if you have a page dedicated to displaying keys for Spotify, you can have the page directory named `PAGE_50;name=spotify`, so when you'll want to have a key to go to this page, you'll be able to use the page number or the name. Using the name allows reordering the pages as you want. We'll see later how to make keys display a different page when pressed.

### `disabled`

To make the program ignore a page, image, text, event..., you can use the `disabled` option, which is a flag, meaning that simply adding `;disabled` is enough to have it disabled, but you can also set a boolean value `;disabled=true` or `;disabled=false`.

## Configuring appearance (images, drawings, texts)

First, let's talk about the image formats that are supported. You can use every [image format supported by the `pillow` python library](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html). For example, jpeg, png, gif (we don't handle animated gif yet). The transparency level ("alpha layer") is respected.

The image will be resized to fit in the key: a 72 or 96 pixel square, depending on which StreamDeck you have (the `inspect` command gives us this information). The ratio of the original image is kept, with transparent bands of the top/bottom or left/right depending on the ratio.

### Images layers

The simplest thing to do to display something on a key is to drop (or link) an image on the corresponding `KEY...` directory and name it `IMAGE`.

But you can make an advanced composition with layers, for example a background, an icon, and an overlay.

#### Option "layer"

To make an image a layer, set the `layer` configuration option to a number: `IMAGE;layer=1`, `IMAGE;layer=2`... Layers will be drawn on top of each other in numerical order. Also, note that when there is at least one image with the layer number defined, the default image (i.e., `IMAGE` without the `layer` configuration) is ignored.

If two images have the same layer number, the most recent one will be displayed, the other(s) will be ignored.

So ok, we can have many layers that will be rendered on top of each other. But in this case, we'll only be able to see the last layer, right? Not exactly. There are many configuration options that you can apply on each layer, as you can see below. 

The list below presents the configuration options in the same order as they are applied on a layer (the order in the file name is not essential).

Note that the following options are also valid when you have a single image without a defined layer.

#### Option "crop"

The `crop` option allows displaying only a specific part of the image. Coordinates can be defined in pixels from the source image or in percents.

It must be defined like this: `crop=LEFT,TOP,RIGHT,BOTTOM`, representing the two points (x,y) of the image to be used to "cut" a rectangle, based on the (0,0) coordinate being in the top left corner of the image: the top-left corner and the bottom-right corner. Values are:

- `LEFT`: the distance, in pixels or percents (of the original width) from the left
- `TOP`: the distance, in pixels or percents (of the original height) from the top
- `RIGHT`: the distance, in pixels or percents (of the original width) from the left
- `BOTTOM`: the distance, in pixels or percents (of the original height) from the top

Examples:

- `IMAGE;crop=10,10,90,90` applied on a 100x100 pixels image, will remove a border of 10 pixels on all sides
- `IMAGE;crop=0,0,33.33%,33.33%` will only keep the top left third of the image. It can be used to display an image on a 9-keys square, each key containing the same image but with a different crop configuration.

Once cropped, the part that is kept will be the source image for the other configuration options.

#### Option "rotate"

The `rotate` option takes the source image (or the one already updated by previous options) and rotates it the number given of degrees clockwise.

It must be defined like this: `rotate=ANGLE`, with:

- `ANGLE`: the angle, in degrees, from 0 to 360, or in percents (100%=360 degrees) (it can be negative or more than 360/100%, in which case it will work as you expect)

Examples:

- `IMAGE;rotate=180` will make the image upside down
- `IMAGE;rotate=50%` same but expressed in percents

Once rotated, the updated image will be the source image for the other configuration options.

#### Option "margin"

The `margin` option allows placing the source image (or the one already updated by the previous options) in a key area by defining margin on all sides. The way it works is to remove the margins from the original key size and fit the image in the remaining area, keeping the aspect ratio.

Values can be defined in pixels from the key or in percents (Using percents allow to have the same rendering whatever the key size is)

It must be defined like this: `margin=TOP,RIGHT,BOTTOM,LEFT`, with:

- `TOP`: the height of the top margin, in pixels or percents (of the key height)
- `RIGHT`: the width of the right margin, in pixels or percents (of the key width)
- `BOTTOM`: the height of the bottom margin, in pixels or percents (of the key height)
- `LEFT`: the width of the left margin, in pixels or percents (of the key width)

Examples:

- `IMAGE;margin=10,10,10,10` makes a margin of 10 pixels on all size
- `IMAGE;margin=0,33.33%,0,33.33%` will fit the image if the middle third of the key (margin of 33.33% on left and right, so 33.33% are available in the middle)


#### Option "colorize"

The `colorize` option takes a color and will replace every color of the image with this one, keeping the alpha layer (opacity) intact. Useful, for example, if you want to display an icon, given in black with transparency, in a specific color.

It must be defined like this: `colorize=COLOR` with:

- `COLOR`: the color to use. It can be a common HTML color name (red, green, blue, etc.) or a hexadecimal RGB value, like `#ff0000` (here, pure red). Color names or hexadecimal values are case-insensitive.

Examples:

- `IMAGE;colorize=red` colorizes the image in red
- `IMAGE;colorize=#00FFFF` colorizes the image in cyan


#### Option "opacity"

The `opacity` option allows defining how transparent the image will be, i.e., how the layers below will be visible.

It must be defined like this: `opacity=NUMBER` with:

- `NUMBER`: the level of opacity, from 0 to 100, 0 being the less opaque (fully transparent, the layer won't be visible at all), and 100 being the most opaque (not transparent at all, except the parts already transparent)

For parts of the image already partially transparent, they will become more transparent with the opacity decreasing.

Examples:

- `IMAGE;opacity=100` does not change the transparency at all
- `IMAGE;opacity=50` makes the image 50% transparent


### Drawings

In addition to image files, layers can be simple drawings: points, lines, rectangles/polygons, circles/ellipses, arch, chords, and pie slices.

Drawings can be used to add a progress bar (horizontal, circular), a separation line between a text title and a text content, etc.

A drawing layer is named like an image (i.e., with the name starting with `IMAGE;`) but can be an empty file (or any file you want, the content will be ignored) and is defined with `draw=KIND` and other configuration options depending on the drawing kind.

As said above, drawings are image layers, defined like it, so all configuration options described above for images also apply for drawing. The drawing is like the source of the image, so all configuration options for images seen above can be used.

Common things to know about drawings configuration options:

- coordinates are based on the size of the key, based on the (0,0) coordinate being in the top left corner of the image
- coordinates can be given in pixels or percents. If percents they will be from the width or the height of the key size
- coordinates can be negative
- coordinates are given as a suite of `X,Y` pairs, separated by commas, like `X1,Y1,X2,Y2` for two points
- default line ("outline") color if not defined is `white`
- width if the width of the line, in pixels, and if it is more than 1, it will spread equally around the "center" of the line
- default line width, if not defined, is 1
- default "fill" is not set, i.e., by default, when not defining any color/width, you'll have a thin white line
- colors ("outline" and "fill") can be set as a name, a simple hexadecimal value (#RRGGBB), or a hexadecimal value with opacity (#RRGGBBAA)
- angles can be expressed in degrees (from 0 to 360, but can be negative or more than 360, 0 is at midnight) or in percents (100%=360 degrees)

#### Kind "points"

Many points (pixels) can be drawn at once with the same color.

It must be defined like this: `draw=points;coords=COORDS;outline=COLOR`, with:

- `COORDS`: a suite of `X,Y` pairs, each one representing a point to draw
- `COLOR`: the color of the points. Optional

It is impossible to define the "size" of a point: it's always a single pixel.

Examples:

- `IMAGE;draw=points;coords=50%,50%` will draw a single white point at the center of the key
- `IMAGE;draw=points;coords=10,10,20,20,30,30;outline=red` will draw 3 red points, at coordinates (10,10), (20,20), and (30,30)

#### Kind "line"

The line is drawn between the different given points (at least two).

It must be defined like this: `draw=line;coords=COORDS;outline=COLOR;width=WIDTH`, with:

- `COORDS`: a suite of `X,Y` pairs, each one representing the start of a line, and, if not the first, the end of the previous one
- `COLOR`: the color of the line. Optional
- `WIDTH`: the width of the line. Optional

Examples:

- `IMAGE;draw=line;coords=0,0,100%,100%` will draw a white diagonal from the top left corner to the bottom right corner
- `IMAGE;draw=line;coords=10,10,20,10,10,20,20,20;color=red;width=2` will draw a red "Z" with a thickness of 2 pixels near the top left corner

#### Kind "rectangle"

The rectangle is represented by two points: the top-left and bottom-right corners.

It must be defined like this: `draw=rectangle;coords=X1,Y1,X2,Y2;outline=LINE_COLOR;width=WIDTH;fill=FILL_COLOR`, with:

- `X1,Y1`: the coordinates of the top left corner
- `X2,Y2`: the coordinates of the bottom right corner
- `LINE_COLOR`: the color of the line. Optional
- `WIDTH`: the width of the line. Optional
- `FILL_COLOR`: the color to fill the inside of the rectangle. Optional

Examples:

- `IMAGE;draw=rectangle;coords=0,0,100%,100%;fill=red;width=0` will fill the whole key area with red
- `IMAGE;draw=rectangle;coords=10,10,40,40;outline=blue;width=5;fill=#0000FF80` will draw a thick blue rectangle in the top left area, with the inner filled in semi (via the ending `80`) transparent blue

#### Kind "polygon"

The polygon is like the `line`, plus a line between the last and first points, and can be filled with a color. There is one drawback: due to a limitation of the used library, it's not possible to define the width of the line (for this, it's possible to add a `draw=line` layer with the same `coords` but with adding the first `X,Y` at the end to close the line)

It must be defined like this: `draw=polygon;coords=COORDS;outline=LINE_COLOR;fill=FILL_COLOR`, with:

- `COORDS`: a suite of `X,Y` pairs, each one "corner" of the polygon
- `LINE_COLOR`: the color of the line. Optional
- `FILL_COLOR`: the color to fill the inside of the polygon. Optional

Example:

- `IMAGE;draw=polygon;coords=50%,0,100%,50%,50%,100%,0,100%;color=yellow` will draw a yellow diamond touching the middle of the four sides

#### Kind "ellipse"

The ellipse is defined by its bounding box, represented by two points: the top-left and bottom-right corners of the box. If the width and height of the bounding box are equal, we have a circle.

It must be defined like this: `draw=ellipse;coords=X1,Y1,X2,Y2;outline=LINE_COLOR;width=WIDTH;fill=FILL_COLOR`, with:

- `X1,Y1`: the coordinates of the top left corner of the bounding box
- `X2,Y2`: the coordinates of the bottom right corner of the bounding box
- `LINE_COLOR`: the color of the line. Optional
- `WIDTH`: the width of the line. Optional
- `FILL_COLOR`: the color to fill the inside of the ellipse. Optional

Examples:

- `IMAGE;draw=ellipse;coords=0,0,100%,100%` will draw a circle touching the middle of the four sides
- `IMAGE;draw=ellipse;coords=10,10,50,20;outline=blue;width=5;fill=#0000FF80` will draw a thick flat blue ellipse in the top area, with the inner filled in semi (via the ending `80`) transparent blue

#### Kind "arc"

An arc is a portion of an ellipse outline. It is defined by the bounding box of the ellipse (see `ellipse` above) and two angles (start and end)

It must be defined like this: `draw=arch;coords=X1,Y1,X2,Y2;angles=START,END;outline=LINE_COLOR;width=WIDTH`, with:

- `X1,Y1`: the coordinates of the top left corner of the bounding box
- `X2,Y2`: the coordinates of the bottom right corner of the bounding box
- `START`: the start angle of the arc.
- `END`: the end angle of the arc. The arc is drawn clockwise.
- `LINE_COLOR`: the color of the line. Optional
- `WIDTH`: the width of the line. Optional

Examples:

- `IMAGE;draw=arc;coords=10%,10%,90%,90%;angles=0,270;width=5;outline=red` will draw a thick red arc representing a circular progress bar of 75% starting at midnight and ending a 9 o'clock
- `IMAGE;draw=arc;coords=10%,10%,90%,90%;angles=0,75%;width=5;outline=red` same but end angle expressed as percents

#### Kind "chord"

A chord is like an arc, plus a line between its two ends, and can be filled with a color.

It must be defined like this: `draw=chord;coords=X1,Y1,X2,Y2;angles=START,END;outline=LINE_COLOR;width=WIDTH;fill=FILL_COLOR`, with:

- `X1,Y1`: the coordinates of the top left corner of the bounding box
- `X2,Y2`: the coordinates of the bottom right corner of the bounding box
- `START`: the start angle of the arc.
- `END`: the end angle of the arc. The arc is drawn clockwise.
- `LINE_COLOR`: the color of the line. Optional
- `WIDTH`: the width of the line. Optional
- `FILL_COLOR`: the color to fill the inside of the chord. Optional

Examples:

- `IMAGE;draw=chord;coords=20%,20%,80%,80%;angles=270,90` will draw a closed semi circle on the top half
- `IMAGE;draw=chord;coords=20%,20%,80%,80%;angles=-25%,25%` same but angles expressed as percents


#### Kind "pieslice"

A chord is like an arc, plus a line between each end and the center of the bounding box. It can be filled with a color.

It must be defined like this: `draw=pieslice;coords=X1,Y1,X2,Y2;angles=START,END;outline=LINE_COLOR;width=WIDTH;fill=FILL_COLOR`, with:

- `X1,Y1`: the coordinates of the top left corner of the bounding box
- `X2,Y2`: the coordinates of the bottom right corner of the bounding box
- `START`: the start angle of the arc.
- `END`: the end angle of the arc. The arc is drawn clockwise.
- `LINE_COLOR`: the color of the line. Optional
- `WIDTH`: the width of the line. Optional
- `FILL_COLOR`: the color to fill the inside of the pie slice. Optional

Examples:

- `IMAGE;draw=pieslice;coords=20%,20%,80%,80%;angles=0,90` will draw a quarter circle on the top right quarter "pointing" towards the middle
- `IMAGE;draw=pieslice;coords=-50%,-50%,50%,50%;angles=90,180` will draw a quarter circle on the top left quarter "pointing" towards the top left corner


### Texts

Images are great to have keys with meaning, but having a way to display text can be helpful, to add a visible title on the key or present some information.

Texts are not defined like image layers and drawings but on their own. The most basic text is `TEXT;text=foo`. It will write "foo" above all image layers (if any) in white color in the top left corner.

But like layers, you can either have one text line or many, using the `line=XX` configuration option (same as the `layer=XX` configuration option of images/drawings).

"Lines" of texts can have different configuration options and will be written on top of each other in their numerical order. Note that all `IMAGE` layers will be drawn BEFORE the text lines.

All text will be written in the same font (Roboto), which has many "styles" (combination of weight and italic).

Text is not wrapped by default and will be truncated to fit on a line. See the `wrap` and `scroll` options below to change this behavior.

The configuration options for the texts are:

#### Option "line"

It's the number of the line to write. It is only needed if many lines are defined. And like layers, if many lines are present, if one has no `line=` configuration option, it will be ignored.

#### Option "text"

It's the text to write. New lines are replaced by spaces (except when wrapping is enabled).

When setting the text, don't forget to consider the rules regarding the file names limitation: no `/` and length not longer than the max authorized on the operating system (256 characters on Linux). Plus, a last rule: the text cannot contain semi-colon as it is interpreted as the end of the text (because it's the configuration options separator)

If you need to bypass these rules, see the `file` configuration option below.

The text must be defined like this: `text=foo with space | or whatever (really)`.

#### Option "size"

The size of the font. 

It must be defined like this: `size=SIZE` with:

- `SIZE`: the size in pixels of the text to write, or in percents of the key height (will be converted to pixels). The default is `20%`.

Examples:

- `TEXT;text=foobar;size=5` will draw a very small text
- `TEXT;text=foobar;size=40` will draw a very big text

#### Option "weight"

It's the font-weight to use.

It must be defined like this: `weight=WEIGHT` with:

- `WEIGHT`: one of the available weights: `thin`, `light`, `regular`, `medium`, `bold`, `black` (here in the thinner to the largest order)

Default is `medium`.

Examples:

- `TEXT;text=foobar;weight=thin` will draw a very thin text
- `TEXT;text=foobar;weight=black` will draw a very thick text

#### Option "italic"

A flag to tell if the text must be written in italic.

It must be defined like this:

- `italic` or `italic=true` to use italic
- `italic=false` to not use italic (it's the same as not defining the italic option at all)

Examples:

- `TEXT;text=foobar;italic` or `TEXT;text=foobar;italic=true` will draw a text in italic
- `TEXT;text=foobar;italic=false` or `TEXT;text=foobar` will draw a regular text (not in italic)

#### Option "align"

Horizontal alignment of the text. 

It must be defined like this: `align=ALIGN` with:

- `ALIGN`: the horizontal alignment to use between `left`, `center`, and `right`. Default if not set is `left`.

Example:

- `TEXT;text=foobar;align=center` will center the text horizontally in the key


#### Option "valign"

Vertical alignment of the text. 

It must be defined like this: `valign=ALIGN` with:

- `ALIGN`: the horizontal alignment to use between `top`, `middle`, and `bottom`. Default if not set is `top`.

Example:

- `TEXT;text=foobar;valign=middle` will center the text vertically in the key


#### Option "color"

The color of the text to write.

It must be defined like this: `color=COLOR` with:

- `COLOR`: the color to use. It can be a common HTML color name (red, green, blue, etc...) or a hexadecimal RGB value, like `#ff0000` (here, pure red). Color names or hexadecimal values are case-insensitive.

Example:

- `TEXT;color=red` will write text in red

#### Option "opacity"

The `opacity` option allows defining how transparent the text will be, i.e., how the layers below will be visible.

It must be defined like this: `opacity=NUMBER` with:

- `NUMBER`: the level of opacity, from 0 to 100, 0 being the less opaque (fully transparent, the text won't be visible at all), and 100 being the most opaque (not transparent at all)

Examples:

- `TEXT;opacity=100` does not change the transparency at all
- `TEXT;opacity=50` makes the text 50% transparent

#### Option "wrap"

It's a flag defining if the text must be wrapped if it does not fit in one line. If not set (the default), the text will be truncated to stay on one line.

In wrap mode, the text will be split on words, and if a word is too long to fit in one line, it will be divided into at least two parts.

It must be defined like this:

- `wrap` or `wrap=true` to wrap the text on my lines
- `wrap=false` to not wrap the text (it's the same as not defining the wrap option at all)

Examples:

- `TEXT;text=foobar;wrap` or `TEXT;text=foobar;wrap=true` will wrap the text if too long
- `TEXT;text=foobar;wrap=false` or `TEXT;text=foobar` will not wrap the text

#### Option "margin"

The `margin` option allows placing the text in a key area by defining margin on all sides. The way it works is to remove the margins from the original key size and fit the text in the remaining area.

The `align` and `valign` options will then be applied in the area limited by the margins (same for the `wrap` option: text will be displayed on many lines but only in this area). 

Values can be defined in pixels from the key or in percents (Using percents allow to have the same rendering whatever the key size is)

It must be defined like this: `margin=TOP,RIGHT,BOTTOM,LEFT`, with:

- `TOP`: the height of the top margin, in pixels or percents (of the key height)
- `RIGHT`: the width of the right margin, in pixels or percents (of the key width)
- `BOTTOM`: the height of the bottom margin, in pixels or percents (of the key height)
- `LEFT`: the width of the left margin, in pixels or percents (of the key width)

Examples:

- `TEXT;margin=0,0,80%,0` will display the text only in the top 20%. For example to display a "title"

#### Option "scroll"

The `scroll` option is useful when the text is not fully visible. It allows scrolling text horizontally if the wrap option is not set or vertically if not.

It must be defined like this: `scroll=SIZE`, with:

- `SIZE`: the number of pixels to scroll per second

There will be no scroll if the text is small enough to fit in its defined area (the whole key or the area left inside the margins).

About the alignment, if the text needs to scroll because it doesn't fit, if `wrap` is not activated, the `align` option will be ignored, and the text will be left-aligned (and will move to the left). And if `wrap` is activated, the `valid` option will be ignored, and the text will be aligned to the top (and will move to the top)

Examples:

- `TEXT;text=this is a long text for a single line;wrap=false;scroll=20` will keep the text on one line but will scroll at a speed of 20 pixels per second
- `TEXT;text=this is a very long text that even when wrapped, will not fit;wrap;scroll=20` will wrap the text and scroll it at a speed of 20 pixels per second

#### Option "file"

If the text is too long to fit in the file name or contains some forbidden characters, it can be placed in the file itself. 

You can specify this by NOT setting the `text` option but setting the `file` option to `__self__`: `file=__self__`

Example:

- `TEXT;file=__self__` will read the text from the content of the file

In the future, it will be possible to specify another file.


## Configuring events (press, long-press, release, start)

`streamdeckify` handles four different events from your StreamDeck that are listed below. But first, let see how events are defined.

An event for a key is a file in a `KEY...` directory that starts with `ON_`, followed by the event's name uppercased: `ON_PRESS`, `ON_RELEASE`, `ON_LONGPRESS`, `ON_START`

And it is configured the same way as images, texts, with configurations options, like this: `ON_PRESS;conf1=value1;conf2=value2`

An event is an action that is triggered when the key is pressed, released, etc., and, like images, it will use the file itself as the script the run. So to run a script/program for a specific action, copy, or make a link, the program in the `KEY...` directory and rename it `ON__...`. It can be any executable the OS knows to execute or a script with the correct shebang.

There can be only one of each event for each key. If the same `ON_XXX` is defined many times, the most recent will be used and the others ignored.

If you want many actions to be done when a key is, for example, pressed, the file can be a bash script with many commands, periods of sleep, etc.

Two other kinds of actions can be triggered on an event instead of running a script/program: changing page (see later the `page` configuration option) or adjusting the brightness of the StreamDeck (see later the `brightness` configuration option)

Now let see the different events, then how they can be configured:

### The available events

#### Event "ON_START"

When a key is displayed, the `ON_START` command is executed. And if it still runs when the key stops to be displayed (at the end of the `streamdeckify` program or when you change page), the command will be terminated. It can be used, for example, to start a script that will periodically fetch some information and update a key, like the temperature of your CPU, the title of the current Spotify song, etc.

If the program must still run when the key stops to be displayed, it can be "detached" (and in this case, it will not even be stopped when the `streamdeckify` program ends)

#### Event "ON_PRESS"

When a key is pressed (note that we have a different event for "press" and "release"), the `ON_PRESS` command is executed. Among the configuration options, it's possible to run the command only if the key is pressed more, or less, than a specific period; it can be repeated if pressed long enough, etc.

Same as for the `ON_START` event, the program, if it still runs when the key stops being displayed, will be terminated, except if the `detach` option is set.

#### Event "ON_RELEASE"

The `ON_RELEASE` event works like `ON_PRESS`, but the action is triggered when the key was pressed but is now released.

#### Event "ON_LONGPRESS"

The `ON_LONGPRESS` event is an event that is triggered when the key is pressed for more than a specific period, which is, by default, 300 milliseconds (0.3 seconds) but can be configured.

It's possible to have both the `ON_PRESS` and `ON_LONGPRESS` events defined on the same key, for example to have a specific action when pressed, and to display a page with more options when long-pressed (play/pause Spotify on simple press and display a page specific to Spotify on long press).

It's best to define the `duration-max` configuration option of the `ON_PRESS` event to be at max the `duration-min` of the `ON_LONGPRESS`  event if you don't want the `ON_PRESS` event triggered when you do a long press.

### The events configuration options

Except when said so, all options are available for all events.

Common things to know about events configuration options:

- all durations are expressed in milliseconds. For example, `300` (it's 0.3 seconds)

### Option "wait"

The `wait` option is the delay between the event happening and the action executed. For the `ON_PRESS` event, it will happen even if the key is released.

It must be defined like this: `delay=DURATION` with:

- `DURATION`: duration in milliseconds

Examples:

- `ON_START;delay=5000` starts the action defined by the `ON_START` file 5 seconds after the key is displayed
- `ON_PRESS;delay=300` starts the action defined by the `ON_PRESS` file 0.3 seconds after the key being pressed

### Option "every"

The `every` option allows repeating the action every XXX milliseconds.

Only works for `ON_PRESS` and `ON_START` events. For `ON_PRESS`, the repeat stops when the key is released. For `ON_START`, it's when the key stops being displayed.

It must be defined like this: `every=DURATION` with:

- `DURATION`: delay between two executions of the action, in milliseconds

Examples:

- `ON_START;every=5000` will run the action defined by the `ON_START` file every 5 seconds as long as the key is displayed
- `ON_PRESS;every=500` will run the action defined by the `ON_PRESS` file every 0.5 seconds as long as the key is not released

To limit the number of times the action is repeated, see the `max-runs` option.

### Option "max-runs"

The `max-runs` option works with the `every` option and allows setting a maximum of times the action must be repeated.

It must be defined like this: `max-runs=COUNT` with:

- `COUNT`: maximum number of times the action can be repeated

Examples:

- `ON_START;every=5000;max-runs=3` will run the action every 5 seconds until the key is released, but a maximum of 3 times
- `ON_PRESS;every=500;max-runs=10` will run the action every 0.5 seconds until the key is released, but a maximum of 10 times

### Option "duration-min"

The `duration-min` option specifies the minimum duration for which the key must have been pressed before running the `ON_RELEASE` or `ON_LONGPRESS` action, the only events for which this option is available.

If must be defined like this: `duration-min=DURATION` with:

- `DURATION`: the minimum duration, in milliseconds, for the key to having been pressed to trigger the event. For the `ON_RELEASE` event, there is no default, so no minimum duration by default, but for the `ON_LONGPRESS` event, there is a default value of 300 (milliseconds).

The main difference between `ON_RELEASE` and `ON_LONGPRESS` here is that for `ON_LONGPRESS`, the action will be executed as soon as the duration is reached, but for `ON_RELEASE`, when releasing the key only if the duration was reached, but it may be much more than this delay.

Examples:

- `ON_RELEASE;duration-min=2000`: run the action when releasing the key only if the key was pressed at least 2 seconds
- `ON_LONGPRESS;duration-min=1000`: run after 1 second of the key being pressed, if the key is not yet released, but does not wait for the key to be released

### Option "duration-max"

The `duration-max` option is only for the `ON_PRESS` event and is created specifically to handle the event `ON_PRESS` OR `ON_LONGPRESS` to be executed.

If not set on the `ON_PRESS` event, the `ON_PRESS` event will be executed even if the `ON_LONGPRESS` is configured. To avoid that, you must configure the `duration-max` of the `ON_PRESS` key to be at max the `duration-min` (which is 300 by default) of the `ON_LONGPRESS` key.

It must be defined like this: `duration-max=DURATION` with:

- `DURATION`: the maximum duration, in milliseconds, for the key to having been pressed to trigger the event.

When using this configuration option, the action will not be triggered directly when the key is pressed but after this delay (or when the key is released if it is released before this delay).

Example:

- `ON_PRESS;duration-max=300` will only run the action if the key is pressed at max 300 milliseconds. After this delay, if the key is not released, the action won't be triggered.

### Option "detach"

By default, all scripts/programs executed by a StreamDeck event are "tied" to the `streamdeckify` process. And they are stopped, if still running, when the key stops being displayed for `ON_START` events, or, for others, when `streamdeckify` ends.

It's common to want to run an external program that should stay open even if the `streamdeckify` ends. The `detach` flag is here for that.

It must be defined like this:

- `detach` or `detach=true` to detach the program from the `streamdeckify` process
- `detach=false` to not detach the program (when key stops being displayed or when `streamdeckify` ends)

Examples:

- `ON_PRESS;detach` or `ON_PRESS;detach=true` will detach the program
- `ON_PRESS;detach=false` or `ON_PRESS` will not detach the program

### Option "unique"

The `unique` flag avoids running a program when its previous execution (from the same event) is not yet finished. It's useful with the `every` option to wait for the previous iteration to be done before running the next one. Or for multiple presses.

It must be defined like this:

- `unique` or `unique=true` to deny the execution of a program if it's still running from the same event
- `unique=false` won't check if the program is already running, and it's the default

Examples:

- `ON_PRESS;every=100;detach` or `ON_PRESS;every=100;detach=true` will run the program every 100 milliseconds but will skip an iteration if the execution from the previous one is not finished yet, so if the program takes 140ms, it will run at 0ms, 200ms, 400ms... instead of 0ms, 100ms, 200ms...
- `ON_PRESS;every=100;detach=false` or `ON_PRESS;every=100` will not detach the program, so it will run at 0ms, 100ms, 200ms, and many occurrences of the same program may be running at the same time

### Option "program"

By default, the action executed by an event is the program of the file itself (or the one it links to), but it may not be convenient. Imagine if you want to run the gnome calculator on a press, you'd have to find the path of the gnome-calculator binary and link it to your `ON_PRESS` file, or make your `ON_PRESS` file a bash script that would call `gnome-calculator`.

And another example could be to open a specific page in your default browser. As there is an argument (the page to open), you cannot make a link and need to make this bash script (it's not complicated, but maybe you want to stick to the whole configuration in file names)

The `program` configuration option allows you to define the full command to execute, and it will be run as-is. You still have to respect the known limitations of the file name (max length and no slash `/`). For the `slash`, which is common if you have a path in the command, you can replace it with any suite of characters defined in the `slash` option.

The `program` configuration option can include `|`, `>`, etc., as you would do in a console.

Note that the file can be empty when this option is set, as its content will be ignored.

It must be defined like this: `program=COMMAND` with:

- `COMMAND`: the command to execute

Examples:

- `ON_PRESS;program=gnome-calculator` will run the gnome calculator
- `ON_PRESS;program=browse http:||elgato.com;slash=|` will open your default browser on the `http://elgato.com` web page. Note that the `/` in `http://` are replaced by `|` as defined by the `slash` configuration option.

### Option "slash"

When using the `program` option, it's impossible to use slashes in the filename, so you can replace it with any character or suite of characters you defined with the `slash` option.

It must be defined like this: `slash=REPLACEMENT` with:

- `REPLACEMENT`: a character or suite of characters to use as a replacement in the `program` configuration option for the `/` character

Examples:

- `ON_PRESS;program=@path@to@myscript | grep foobar > @path@to@log;slash=@` will run the command `/path/to/myscript | grep foobar > /path/to/log`
- `ON_PRESS;program=XXXpathXXXtoXXXmyscript;slash=XXX` will run the command `/path/to/myscript`


### Option "brightness"

Another possible action when pressing a key is, instead of running a program, simply change the brightness of the connected `StreamDeck`.

It must be defined like this: `brightness=BRIGHTNESS` with:

- `BRIGHTNESS`: A value between 0 and 100 (both included), or a delta, like `+10` or `-20`... The final value will be capped to fit in the accepted range.

- `ON_PRESS;brightness=+10` Increase the brightness from the actual value to the actual value plus 10
- `ON_PRESS;brightness=100` Set the brightness at the maximum

### Option "page"

Another possible action when pressing a key is to go to a different page. See the `Pages` section below to have more details about this feature.

It must be defined like this: `page=PAGE` with:

- `PAGE`: the number or name (it will use the name in the page directory name: `PAGE_NUMBER;name=NAME`) of the page to display or one of these specific values:

	- `__first__` will go to the first page number available
	- `__previous__` will go to the previous page number (i.e., the actual page number minus 1)
	- `__next__` will go to the following page number (i.e., the actual page number plus 1)
	- `__back__` will go to the previous page that was displayed before the current one


See the `Pages` section below to know more about how pages work.

### Option "overlay"

The `overlay` flag goes with the `page` configuration option and allows opening the wanted page as an overlay over the current one. The keys defined on the new page will be displayed, and for the others, the keys from the current page will be displayed with a black overlay and all events deactivated. It's like a "modal" on a website. 

It must be defined like this:

- `overlay` or `overlay=true` to have the page displayed as an overlay
- `overlay=false` to have the new page hiding the current one, including non defined keys that will then be black

Examples:

- `ON_PRESS;page=50;overlay` or `ON_PRESS;overlay=true` will open the page number 50 as an overlay
- `ON_PRESS;page=50;overlay=false` or `ON_PRESS;page=50` will open the page number 50 without any key of the current page being visible


# Pages

Pages are a way to extend the number of keys available on your StreamDeck and regroup some actions together. 

Some examples: 

- a key on your first page with a simple press that will toggle your microphone, and, on a long press, display an overlay with keys to decrease/increase the microphone sensitivity (and a key to close the overlay)

- a key on your first page that will open a page dedicated to Spotify controls

Each page is a directory with at least a page number: `PAGE_NUMBER` with `NUMBER` being a positive number. If two pages have the same number, the most recent directory will be used.

A page can also have a name: `PAGE_NUMBER;name=NAME`, and then this name is available for the `page` configuration of key events. So if you have a page directory named `PAGE_50;name=spotify`, you can say "go to page 50" or "go to page spotify".

In a page directory, you only need to define the key you need, not all, in the format `KEY_ROW_XX_COL_YY`. If a key directory exists but has no images/texts/events (or only disabled ones), it will be ignored.

Page navigation history is kept so you can easily go back to the previous page seen. It's helpful, for example, for overlays. Let's take the first example about the "microphone overlay". Let's say you have a directory `PAGE_60;name=microphone`; you would have a key with the long press event to display this overlay defined like this: `ON_LONGPRESS;page=microphone;overlay`, and in this `PAGE_60;name=microphone` directory, you would have a key to close this overlay (i.e., go back to the previous page), like this: `ON_PRESS;page=__back__`. Until you press this key, as this page is opened as an overlay, you would see the keys of this new page as regular keys and the others from the page below, still visible but darker and without any effect when pressing them.

Pages are numbered, but it's not at all mandatory to have consecutive numbers unless you want to, for example to use the `page=__next__` and `page=__previous__` configuration options for your key event because they only work for consecutive pages.

For example, say you have three pages of classic actions and want to navigate between them easily, you can number them 1, 2, and 3. But you can also have pages triggered by some keys that should not be accessed this way, so the number can be higher. For example `PAGE_50;name=spotify` and `PAGE_60;name=microphone`.

Using names is very useful when you configure your page actions: having `page=spotify` is a lot more meaningful than `page=50` (and it allows reorganizing your pages as you want)


# References

References are a way for a key, event, image, and text to inherit from another.

Say you have a page dedicated to Spotify, and some of your keys are Spotify controls and should have the same background.

You can, on each key, have a file named `IMAGE;layer=0;name=background;draw=rectangle;coords=0,0,100%,100%;width=0;fill=#8cc63f`

OR, you can have it only defined on the first key using it, say it's `KEY_ROW_1_COL_1;name=toggle` and in other keys, add a file named `IMAGE;ref=:toggle:background`. This will take the image named "background" in the key named "toggle" of the current page (nothing before the first `:` in the `ref` configuration option means "current page")

So you can easily change how this background should look in one place, affecting all keys referencing this background. All configuration options are inherited. In this example the image defined by `IMAGE;ref=:toggle:background` will inherit the `name`, `layer`, `draw`, `coords`, `width` and `fill`. But these can be overridden. If you want to change the color but still have a rectangle, you can use `IMAGE;ref=toggle:background;fill=red`, and you'll have a red rectangle as the background.

Last important thing about references: you can have references of references (of references, etc.). Just be careful to avoid cyclic references, as it's not checked, and `streamdeckify` may crash.

## References configuration

References can be defined for image layers, text lines, events, and keys. They are defined like this:

### Images

An image layer can reference another image layer like this: `ref=PAGE:KEY:LAYER`, with:

- `PAGE` is the name or number of the page where the reference image is, and not setting the page (`ref=:KEY:LAYER`) means looking on the same page as the image defining the `ref`
- `KEY` is the name or coordinates (`ROW,COL`) of the key where the reference image is
- `LAYER` is the name or layer number of the reference image, and not setting the layer (`ref=PAGE:KEY:`) means referencing the text on the `KEY` that has no layer defined

As with all configuration options, `name` and `layer` are inherited too (if defined on the reference) if not specified on the image having the `ref` option.

### Texts

A text line can reference another text line like this: `ref=PAGE:KEY:LINE`, with:

- `PAGE` is the name or number of the page where the reference text is, and not setting a page (`ref=:KEY:LAYER`) means looking on the same page as the image defining the `ref`
- `KEY` is the name or coordinates (`ROW,COL`) of the key where the reference text is
- `LINE` is the name or line number of the reference text, and not setting the line (`ref=PAGE:KEY:`) means referencing the text on the `KEY` that has no line defined

As with all configuration options, `name` and `line` are inherited too (if defined on the reference) if not specified on the text having the `ref` option.

### Events

An event can reference another event like this: `ref=PAGE:KEY:EVENT`, with:

- `PAGE` is the name or number of the page where the reference event is, and not setting a page (`ref=:KEY:LAYER`) means looking on the same page as the event defining the `ref`
- `KEY` is the name or coordinates (`ROW,COL`) of the key where the reference event is
- `EVENT` is the name or kind (`press`, `longpress`, `release`, `start`) of the reference event, and not setting the event (`ref=PAGE:KEY:`) means referencing the event for the `KEY` with the same kind (`ON_PRESS;ref=PAGE:KEY:` = looking for a `press` event in the key `KEY` of the page `PAGE`)

### Keys

A key can reference another key like this: `ref=PAGE:KEY`, with:

- `PAGE` is the name or number of the page where the reference key is, and not setting a page (`ref=:KEY:LAYER`) means looking on the same page as the event defining the `ref`
- `KEY` is the name or coordinates (`ROW,COL`) of the reference key, and not setting the key (`ref=PAGE:`) means referencing the key on the `PAGE` with the same coordinates

Keys references are particular because a key can contain text, images, events, etc. The way it works is simple: by default, everything that is available in the reference key is "imported" in the key referencing it, but in the directory key referencing it, you can add texts, images, events... that will "replace" the ones in the reference key. So you can add layers, texts, and if you want to change one configuration of, say, an image, you can reference it and only add the configuration to update. See below when using the "close" reference.

## Usage example: references page

Among many things that are possible with references, one way of using them is to have a "references" page where you put things that are common among your configuration.

Here is an example of such a page:

```
└── PAGE_999;name=ref
    ├── KEY_ROW_1_COL_1;name=img
    │   └── IMAGE;layer=999;name=overlay -> /home/twidi/dev/streamdeck-scripts/assets/overlay.png
    ├── KEY_ROW_1_COL_2;name=draw
    │   ├── IMAGE;layer=0;name=background;draw=rectangle;coords=0,0,100%,100%;width=0;fill=
    │   └── IMAGE;name=progress;draw=line;coords=0%,92,32%,92;outline=white;width=7
    ├── KEY_ROW_2_COL_1;name=close
    │   ├── IMAGE;layer=1;colorize=white;margin=20,10,10,10;name=icon -> /home/twidi/dev/streamdeck-scripts/assets/close.png
    │   ├── IMAGE;ref=:images:overlay
    │   └── ON_PRESS;page=__back__
    └── KEY_ROW_2_COL_2;name=titled-text
        ├── IMAGE;layer=1;name=separator;draw=line;coords=0,25,100%,25;outline=
        ├── TEXT;line=1;name=title;text=Title;weight=bold;align=center;valign=top;color=
        └── TEXT;line=2;name=content;text=Some content that will scroll if too long;size=18;align=center;valign=middle;margin=28,0,0,0;scroll=20;wrap
```

Here you can see a page, number 999 (could have been any number) with the name `ref`.

On this page, many keys are defined:

- `KEY_ROW_1_COL_1;name=img`

This key contains an image with a layer number of 999, named "overlay". The goal is to have an overlay over each defined key with a specific style (like a "glass" rendering). As the layer number is 999, it's almost certain that it will be the top layer. To use it, add an empty file in your `KEY...` directories named `IMAGE;ref=ref:img:overlay`.

- `KEY_ROW_2_COL_2;name=draw`

This key contains two drawings:

One named `background` (where only the fill color is missing), which can be referenced like this: `IMAGE;ref=ref:draw:background;fill=ref`

One named `progress` which draws a progress bar on the bottom of the key (where the Y2 coordinate must be updated to the proper progress value (here at 32%)), which can be referenced like this: `IMAGE;layer=3;ref=ref:draw:progress;coords=0%,92,50%,92%` (here we change the progress to 50% and set the layer number to `3` as the reference does not have it defined because each key using it may want to place the `progress` at a different layer)

- `KEY_ROW_2_COL_1;name=close`

This key represents a complete "close" key that can be used to close an overlay on press. To use it, add in your page a directory named `KEY_ROW_X_COL_Y;ref=ref:close` (set your row and col according to your needs): and voila, you have a close key that will work as expected.

And if you want to change the color of the close key, you add in your directory an image that will reference the `icon`: `IMAGE;ref=ref:close:icon;colorize=red`. It will have, by inheritance, the same layer number as the image in the reference key, and it will be used instead of that one.

- `KEY_ROW_2_COL_2;name=titled-text`

This key represents a key rendered with a title on top, a small line as a separator, and a text in the central area that is wrapped and will scroll if it does not fit. The key itself is not meant to be used as a reference because each part must be configured, so you define your key directory as usual, and inside, you add three empty files `IMAGE;ref=ref:titled-text:separator;outline=COLOR` (with `COLOR` being the color you want for the separator), `TEXT;ref=ref:titled-text:title;text=TITLE` (with `TITLE` being the text you want for the title) and `TEXT;ref=ref:titled-text:content;text=TEXT` (with `TEXT` being the text you want in the central area, or you can set `file=__self__` instead of `text=...` and put the text in the file itself)
