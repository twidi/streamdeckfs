# StopWatch example

This example is a key providing a simple stopwatch application (without any external script, only a simple bash command), with 3 states: 

- `off`: the stopwatch is not running, a "stopwatch" icon is displayed:

![image](https://user-images.githubusercontent.com/193474/125604092-acb7dad0-373c-4ead-8518-56c1cbaf9eb3.png)

- `running`: the stopwatch is running, the elapsed time is displayed and changes every second:

![image](https://user-images.githubusercontent.com/193474/125604143-4fc80f38-dd95-423a-bcf5-76501dc86530.png)

- `result`: the stopwatch is stopped, the elapsed time is displayed and does not change anymore:

![image](https://user-images.githubusercontent.com/193474/125604173-829d1652-2d04-47fe-a0a9-28c3577cf23e.png)


We go from a state to another by a simple press on the key.

To use it, copy the directory `KEY_ROW_X_COL_Y;name=stopwatch` into a page directory and replace `X` and `Y` in the name by the row and column you want.


## Tutorial

This tutorial explains in details how it is done.


We'll create a simple stop watch. We want 3 states:

- `off` : The stop watch is not started and the last time is not displayed, Clicking on the key will start the stop watch
- `running`: The stop watch is started and the elapsed time is displayed and updated regularly
- `result`: The stop watch is stopped and the running time is displayed

We'll display the time in the format `H:MM:SS`, with minutes and seconds prefixed by a `0` if needed (but not the hours)

We can do it without any code, just using filenames. We need 3 variables:

- `VAR_STATE`: The state as described above
- `VAR_START`: The timestamp when the stop watch was started
- `VAR_LAST`: The "actual" timestamp (the elapsed time is the difference between `VAR_LAST` and `VAR_START`)

To update `VAR_LAST` at fixed interval, we'll use the `every` configuration option, and we'll use the `ON_START` event of the key, that will only be enabled when the state is `running`, by using `;enabled={"$VAR_STATE" == "running"}`

We need a different action on press depending on the state

- `off`: set `VAR_STATE` to `running` and set the current timestampe in `VAR_START`
- `running`: set `VAR_STATE` to `result`
- `result`: set `VAR_STATE` to `off`

To set the timestamp in the `VAR_START` and `VAR_LAST` variables files, we'll use the fact that when executing a command, the current directory is the key directory. So we can do `command-to-get-the-timestamp > VAR_START` and `command-to-get-the-timestamp > VAR_LAST`.

The command we'll use is `date +%s` (`+` specify the format that is `%s`, the timestamp)

Let's create our file. We'll start by creating variables and events, we'll do the dispay later.

We first need a `VAR_STATE` file, with a state being `off`. We create an empty file `VAR_STATE;value=off`.

We don't need to create the `VAR_START` and `VAR_LAST` command as they'll be created (and updated) bu the commands.

Next we need a button to go from the `off` state to the `running` state that will also set the current timestamp in `VAR_START`. We create an empty file `ON_PRESS;VAR_STATE<=running;command=date +%s>VAR_START;enabled={"$VAR_STATE" == "off"};quiet`

Let's review the configuration options:

- `VAR_STATE<=running`: will remove the `;value=off` from the `VAR_STATE;value=off` file (so it will be just `VAR_STATE`) and will put `running` as the content of the file. We use `<=` to set the value inside the file instead of just `=` to update the name, because it's slightly faster from the `streamdeckfs` side.
- `command=date +%s>VAR_START`: execute the `date +%s` command and put the result in the `VAR_START` file
- `enabled={"$VAR_STATE" == "off"}`: only enable this button when the state is `off`, so when pressed, the state becomes `running` and this event will be disabled
- `quiet`: to avoid displaying the start and stop of the command on the `streamdeckfs` output

Now that we can go to the `running` state, we need to update the `VAR_LAST` variable at fixed interval. As explained above, it's done via a `ON_START` event that is only enabled when the state is `running` (taking advantage of the fact that `ON_START` is automatically started/stopped when its `disabled` state changes). 

We create an empty file `ON_START;every=1000;command=date +%s>VAR_LAST;enabled={"$VAR_STATE" == "running"};quiet`.

Let's review the configuration options:

- `every=1000`: will run the command every second, to have the elapsed time changing over time
- `command=date +%s>VAR_LAST`: execute the `date +%s` command and put the result in the `VAR_START` file
- `enabled={"$VAR_STATE" == "running"}`: only enable this button when the state is `running`
- `quiet`: to avoid displaying the start and stop of the repeated commands on the `streamdeckfs` output

Next we need a button to stop the stop watch, and display the frozen elapsed time. We create an empty file `ON_PRESS;VAR_STATE<=result;enabled={"$VAR_STATE" == "running"}` that will put `result` in the `VAR_STATE` file.

And finally a button to go back to the `off` state. We create an empty file `ON_PRESS;VAR_STATE<=off;enabled={"$VAR_STATE" == "result"}` that will put `off` in the `VAR_STATE` file.

We now have our state, our variables and our actions, so we can display the elapsed time on the key. As explained above, the format will be `H::MM:SS`, so we'll have three parts, but thanks to expressions, we can have only one text, using `text={compute-hours}:{compute-minutes}:{compute-seconds}`

To compute the different parts, we need to get the elapsed time in seconds: this can be done via `{$VAR_LAST - $VAR_START}`.

And, for each part:
- to get the hours, we compute the floor division of this number of seconds, divided by the number of seconds in one hour: `{($VAR_LAST - $VAR_START)||3600}`
- to get the minutes, we compute the floor division of the remainder of the previous one, divided by the number of seconds in one minute: `{($VAR_LAST-$VAR_START)%3600||60}`. To prefix with a `0` when the number of minutes is less that 10 minutes, we use the `format` function: `{format(($VAR_LAST-$VAR_START)%3600||60,"02")}`
- to get the number of seconds, we get the remainder of the previous computation, and we also use `format` for the same reason: `{format(($VAR_LAST-$VAR_START)%3600%60,"02")}`

So the `text` configuration option will be: `text={($VAR_LAST-$VAR_START)||3600}:{format(($VAR_LAST-$VAR_START)%3600||60,"02")}:{format(($VAR_LAST-$VAR_START)%3600%60,"02")}`

We don't want to display this elapsed time when the state is `off`, so we add `disabled={"$VAR_STATE"=="off"}`.

And we want the text to fit the key. So finally we can create the empty file `TEXT;fit;text={($VAR_LAST-$VAR_START)||3600}:{format(($VAR_LAST-$VAR_START)%3600||60,"02")}:{format(($VAR_LAST-$VAR_START)%3600%60,"02")};disabled={"$VAR_STATE"=="off"}`

We need something to display on the key when the state is `off`. For this we create an empty file `TEXT;fit;text=Stop watch;disabled={"$VAR_STATE" != "off"}`.

Or, instead, we can use an icon, that would be fully visible when the state is `off`, and partially transparent, with different colors, for the other states.

We need a [transparent PNG with a very simple stop watch](https://www.google.com/search?q=stop+watch&tbm=isch&chips=q:stop+watch,online_chips:png), that we'll stylize depending on the state:

- `off`: full opacity, blue (we'll use `#5db0e8`)
- `running`: partially transparent (we'll uset 30% opacity), green (we'll use `#bdf57f`)
- `result`: partially transparent (we'll uset 30% opacity), blue (we'll use `#5db0e8`)

To handle the different styles on a single image, we'll use a variable to handle both color and opacity, so our image can be named `IMAGE;$VAR_ICON_STYLE`.

And this variable is an empty file named `VAR_ICON_STYLE;if={"$VAR_STATE"=="off"};then=colorize=#5db0e8;elif={"$VAR_STATE"=="running"};then=colorize=#bdf57f^opacity=30;else=colorize=#5db0e8^opacity=30`.

Let's review the configuration options. We see that we don't define a `value` directly but use the `if/then/elif/then/else` options to handle the different states:

- `if={"$VAR_STATE"=="off"}`
    - `then=colorize=#5db0e8`
- `elif={"$VAR_STATE"=="running"}`
    - `then=colorize=#bdf57f^opacity=30`
- `else=colorize=#5db0e8^opacity=30`

Notice how we use `^` between the `colorize` and the `opacity` parts. It's because we cannot use `;` as it would mark the end of the `then` option. The `^` character is automatically replaced when the value is used in `IMAGE;$VAR_ICON_STYLE`.

That's it, we have everything we need. Here a listing of all the files (here put in the key (1, 4) on the first page):

```
PAGE_1;name=main/KEY_ROW_1_COL_4;name=stop-watch/
├── IMAGE;$VAR_ICON_STYLE
├── ON_PRESS;VAR_STATE<=off;enabled={"$VAR_STATE" == "result"}
├── ON_PRESS;VAR_STATE<=result;enabled={"$VAR_STATE" == "running"}
├── ON_PRESS;VAR_STATE<=running;command=date +%s>VAR_START;enabled={"$VAR_STATE" == "off"};quiet
├── ON_START;every=1000;command=date +%s>VAR_LAST;enabled={"$VAR_STATE" == "running"};quiet
├── TEXT;fit;text={($VAR_LAST-$VAR_START)||3600}:{format(($VAR_LAST-$VAR_START)%3600||60,"02")}:{format(($VAR_LAST-$VAR_START)%3600%60,"02")};disabled={"$VAR_STATE"=="off"}
├── VAR_ICON_STYLE;if={"$VAR_STATE"=="off"};then=colorize=#5db0e8;elif={"$VAR_STATE"=="running"};then=colorize=#bdf57f^opacity=30;else=colorize=#5db0e8^opacity=30
└── VAR_STATE;value=off

```
