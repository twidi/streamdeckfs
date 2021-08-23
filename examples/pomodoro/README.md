# Pomodoro example

This example is a key providing a [pomodoro timer](https://en.wikipedia.org/wiki/Pomodoro_Technique) application (without any external script, only a simple bash command) 

The pomodoro technique uses a timer to break down work into intervals, traditionally 25 minutes in length, separated by short breaks. After a fixed amount (usually 4) of work periods, a long break occurs.

This little application handle of theses steps, and all durations can be configured. And a sound is triggered at the end of each period.

So there is 4 states the key can take:

- `off`: the timer is not running and a tomato (the name "pomodoro" comes from the italian word for "tomato")

![image](https://user-images.githubusercontent.com/193474/125613443-20bd67cd-962d-48d8-b81e-bbf6b1d343c9.png)

- `work`: a period of work is ongoing, with a timer clock displayed, with a discret progress on its face, and, at the top, the total number of period of works (with, as tomatoes, the number already done + the current one). In the following capture we see that we are at a thrid of the first period of work (and that we have a total of 4 period of works to do before the long break):

![image](https://user-images.githubusercontent.com/193474/125613546-2ef4a8fc-8da5-4cd2-a2e0-cd094099e21b.png)

- `shortbreak`: the short break period between two periods of work, with a pause button slightly disappearing and a progress bar. In the following capture we see that we are in the short break following the second period of work:

![image](https://user-images.githubusercontent.com/193474/125613612-c0ce5719-6508-4df1-9f44-4a91de844532.png)

- `longbreak`: the long break period after a fixed number of work periods (same as `shortbreak` but with a usually longer period)

Possible actions are:

- when state is `off`, pressing the button will start the timer for the first work period
- when state is `work`, pressing the button will stop the timer and go to the next break (short or long) period
- when state is `shortbreak` or `longbreak`, pressing the button will stop the timer and go to the next work period
- doing a long press will stop the pomodoro application, going to the `off` state

To use it, copy the directory `KEY_ROW,COL;name=pomodoro` into a page directory and replace `ROW` and `COL` in the name by the row and column you want.

It can be configured by changing the values in the name of the following files:

- `VAR_CONF_NB;value=4`: number of work periods before a long break
- `VAR_CONF_WORK_TIME;value=25`: duration of the work periods, in minutes
- `VAR_CONF_SHORT_BREAK_TIME;value=5`: duration of the short break periods, in minutes
- `VAR_CONF_LONG_BREAK_TIME;value=5`: duration of the long break periods, in minutes

## Details

We'll detail the different files present in the directory. First, a complete view:

```
├── alert.wav
├── IMAGE;layer=1;name=progress-pause;draw=line;outline=white;width=3;coords=0,99%,$VAR_ELAPSED%,99%;enabled=$VAR_IS_BREAK
├── IMAGE;layer=2;name=progress-work;draw=pieslice;coords=22%,36%,78%,92%;angles=0,$VAR_ELAPSED%;width=0;fill=red;enabled=$VAR_IS_WORK
├── ON_LONGPRESS;VAR_STATE<=off;duration-min=300;quiet
├── ON_PRESS;VAR_IDX<=$VAR_NIDX;VAR_STATE<=$VAR_NSTATE;command=date +%s>VAR_START;duration-max=300;disabled=$VAR_IS_OFF;quiet
├── ON_PRESS;VAR_IDX<=1;VAR_STATE<=work;command=date +%s>VAR_START;duration-max=300;enabled=$VAR_IS_OFF;quiet
├── ON_START;wait=500;command=bash -c 'SECONDS=$(date +%s)^while((SECONDS<$VAR_END))^do echo $SECONDS>VAR_LAST^sleep $VAR_CONF_REFRESH^done^aplay alert.wav&date +%s>VAR_START&echo $VAR_NIDX >VAR_IDX&echo $VAR_NSTATE >VAR_STATE';disabled=$VAR_IS_OFF;quiet
├── TEXT;line=1;name=off;fit;text=🍅;enabled=$VAR_IS_OFF
├── TEXT;line=2;name=tomatoes;fit;text={"🍅"*($VAR_IDX-if("$VAR_STATE"=="work",0,1))}{"⚫️"*($VAR_CONF_NB-$VAR_IDX+if("$VAR_STATE"=="work",0,1))};margin=3%,0,77%,0;disabled=$VAR_IS_OFF
├── TEXT;line=3;name=pause;text=⏸;margin=30%,0,10%,0;fit;opacity={100-round($VAR_ELAPSED)};enabled=$VAR_IS_BREAK
├── TEXT;line=4;name=work;text=⏲;margin=30%,0,0,0;fit;opacity=50;enabled=$VAR_IS_WORK
├── VAR_CONF_LONG_BREAK_TIME;value=30
├── VAR_CONF_NB;value=4
├── VAR_CONF_REFRESH;value=10
├── VAR_CONF_SHORT_BREAK_TIME;value=5
├── VAR_CONF_WORK_TIME;value=25
├── VAR_DURATION;if={"$VAR_STATE"=="shortbreak"};then=$VAR_CONF_SHORT_BREAK_TIME;elif={"$VAR_STATE"=="longbreak"};then=$VAR_CONF_LONG_BREAK_TIME;else=$VAR_CONF_WORK_TIME
├── VAR_ELAPSED;value={100*max(1+$VAR_LAST-$VAR_START, 0)|($VAR_DURATION*60)}
├── VAR_END;value={$VAR_START+$VAR_DURATION*60}
├── VAR_IDX
├── VAR_IS_BREAK;value={"break" in "$VAR_STATE"}
├── VAR_IS_OFF;value={"$VAR_STATE"=="off"}
├── VAR_IS_WORK;value={"$VAR_STATE"=="work"}
├── VAR_LAST
├── VAR_NIDX;if={not $VAR_IS_WORK};then=$VAR_IDX;elif={$VAR_IDX>=$VAR_CONF_NB};then=1;else={$VAR_IDX+1}
├── VAR_NSTATE;if={$VAR_IS_WORK and $VAR_IDX<$VAR_CONF_NB};then=shortbreak;elif={$VAR_IS_WORK and $VAR_IDX==$VAR_CONF_NB};then=longbreak;else=work
├── VAR_START
└── VAR_STATE
```

### Variables

Let's start with the variables.

#### Configuration

We have 5 configuration variables, including these 4, described above:

- `VAR_CONF_LONG_BREAK_TIME;value=30`
- `VAR_CONF_NB;value=4`
- `VAR_CONF_SHORT_BREAK_TIME;value=5`
- `VAR_CONF_WORK_TIME;value=25`

And this one:

- `VAR_CONF_REFRESH;value=10`: how often, in seconds, the progress indicators must be updated (for periods in minutes there is no need to have very frequent updated)

#### State

States variables are updated by the different events, and have their value inside the file (because it's easy from a bash command to update the content of a file with a fixed name)

- `VAR_STATE`: the state of the application: `off`, `work`, `shortbreak` or `longbreak`
- `VAR_IDX`: the "index" of the current work, a number between `1` and the number defined by `VAR_CONF_NB`
- `VAR_START`: the timestamp of the moment the current period started
- `VAR_LAST`: the most recent udpated timestamp for the current period (allows to compute the elapsed time of the current period by computing `{$VAR_LAST - %VAR_START}`)

And we have 3 shortcuts avoiding doing many times state comparison so we can use them directly:

- `VAR_IS_OFF;value={"$VAR_STATE"=="off"}`: will be `True` if `VAR_STATE` contains exactly `off`, and `False` for any other content
- `VAR_IS_WORK;value={"$VAR_STATE"=="work"}`: will be `True` if `VAR_STATE` contains exactly `work`, and `False` for any other content
- `VAR_IS_BREAK;value={"break" in "$VAR_STATE"}`: will be `True` if `VAR_STATE` contains `break` (`shortbreak` or `longbreak`), and `False` for any other content

So we can use these variables in other files: `enabled=$VAR_IS_OFF` instead of `enabled={"$VAR_STATE" == "off"}`

#### Computations

The other variables are computed from other variables:

- `VAR_DURATION;if={"$VAR_STATE"=="shortbreak"};then=$VAR_CONF_SHORT_BREAK_TIME;elif={"$VAR_STATE"=="longbreak"};then=$VAR_CONF_LONG_BREAK_TIME;else=$VAR_CONF_WORK_TIME`

Defines the duration (in minutes) of the current period, using `if/then/elif/then/else` depending on the current state. Will be used to change the state when the period is over.

- `VAR_END;value={$VAR_START+$VAR_DURATION*60}`

Defines the timestamp when we must stop the current period to start the next one.

- `VAR_ELAPSED;value={100*max(1+$VAR_LAST-$VAR_START, 0)|($VAR_DURATION*60)}`

Percentage of the elapsed time in the current period. The adjustment (`max` and `1+`) are here to take into account some small delays between variable updates and the start of the `ON_START` event.

- `VAR_NIDX;if={not $VAR_IS_WORK};then=$VAR_IDX;elif={$VAR_IDX>=$VAR_CONF_NB};then=1;else={$VAR_IDX+1}`

The index of the next period of work. If it's the last one, will be `1`, else we simply increment the current one.

- `VAR_NSTATE;if={$VAR_IS_WORK and $VAR_IDX<$VAR_CONF_NB};then=shortbreak;elif={$VAR_IS_WORK and $VAR_IDX==$VAR_CONF_NB};then=longbreak;else=work`

The next state, i.e., the kind of the next period: `shortbreak` if we are in a period of work but not the last one, `longbreak` if we are in the last period of work, else `work`

### Events

We have four events defined on this key:

- `ON_LONGPRESS;VAR_STATE<=off;duration-min=300;quiet`

When doing a long press on the key, the pomodoro is stopped (we go to the `off` state)

- `ON_PRESS;VAR_IDX<=1;VAR_STATE<=work;command=date +%s>VAR_START;duration-max=300;enabled=$VAR_IS_OFF;quiet`

When we are in the `off` state, if we press the key, we initialize the state to start the first period of work (set the state to `work`, the work index to `1`, and set the start of the timer to the current timestamp via `command=date +%s>VAR_START` that set the output of the `date +%` command (i.e., the timestamp) into to `VAR_STATE` variable file)

- `ON_PRESS;VAR_IDX<=$VAR_NIDX;VAR_STATE<=$VAR_NSTATE;command=date +%s>VAR_START;duration-max=300;disabled=$VAR_IS_OFF;quiet`

When we are NOT in the `off` state, if we press the key, we abord to current period and go to the next one (set the state to the next one as computed by `VAR_NSTATE`, set the index of the work period as the one defined by `VAR_NIDX` (the same one if we go from work to break, but the next one if we go from break to work), and set the start of the timer to the current timestamp)

- `ON_START;wait=500;command=bash -c 'SECONDS=$(date +%s)^while((SECONDS<$VAR_END))^do echo $SECONDS>VAR_LAST^sleep $VAR_CONF_REFRESH^done^aplay alert.wav&date +%s>VAR_START&echo $VAR_NIDX >VAR_IDX&echo $VAR_NSTATE >VAR_STATE';disabled=$VAR_IS_OFF;quiet`

This is where the main work is done. Let's decompose the filename.

We have three configuration options:

  - `wait=500`: to wait for variables to be updated before running the command
  - `disabled=$VAR_IS_OFF`: we don't run a timer when the pomodoro is not started
  - `quiet`: we don't want do show info about this event in the `streamdeckfs` output

 And the command:

  - `command=bash -c 'SECONDS=$(date +%s)^while((SECONDS<$VAR_END))^do echo $SECONDS>VAR_LAST^sleep $VAR_CONF_REFRESH^done^aplay alert.wav&date +%s>VAR_START&echo $VAR_NIDX >VAR_IDX&echo $VAR_NSTATE >VAR_STATE'`

We call bash with a small script `SECONDS=$(date +%s)^while((SECONDS<$VAR_END))^do echo $SECONDS>VAR_LAST^sleep $VAR_CONF_REFRESH^done^aplay alert.wav&date +%s>VAR_START&echo $VAR_NIDX >VAR_IDX&echo $VAR_NSTATE >VAR_STATE`

`^` characters are replaced by `;`, to get: `SECONDS=$(date +%s);while((SECONDS<$VAR_END));do echo $SECONDS>VAR_LAST;sleep $VAR_CONF_REFRESH;done;aplay alert.wav&date +%s>VAR_START&echo $VAR_NIDX >VAR_IDX&echo $VAR_NSTATE >VAR_STATE`

Let's explode this single line in the same script, but multilines:

```bash
SECONDS=$(date +%s)
while ((SECONDS < $VAR_END))
do 
    echo $SECONDS > VAR_LAST
    sleep $VAR_CONF_REFRESH
done
aplay alert.wav &
date +%s > VAR_START &
echo $VAR_NIDX > VAR_IDX &
echo $VAR_NSTATE >VAR_STATE
```

Note that variables (starting with `$VAR_`) are replaced by their value before passing the script to `bash`, so we'll have real texts and numbers instead of `$VAR_CONF_REFRESH`, `$VAR_NIDX`, `$VAR_NSTATE`.

In this script we use the [`$SECONDS` bash variable](https://www.oreilly.com/library/view/shell-scripting-expert/9781118166321/c03-anchor-3.xhtml) that is updated automatically by bash to get the elapsed time.

Now let's add comments to this script to explain the different lines:

```bash
# set the current timestamp in the `$SECONDS` variable so that we can compare with the value we'll have from `$VAR_END`
SECONDS=$(date +%s)

# loop until we reached the value from the `$VAR_END` variable, which is the end of the current period
while ((SECONDS < $VAR_END))
do 
    # we update the `$VAR_LAST` variable by putting in it the current timestamp so progress indicators will be automatically updated
    echo $SECONDS > VAR_LAST
    # and we wait a fixed amount of time defined by the `$VAR_CONF_REFRESH` variable
    sleep $VAR_CONF_REFRESH
done

# the loop ended because the current period is over
# so we can play an alert sound (using `&` to not wait for the sound to finish to continue the script)
aplay alert.wav &

# and we can update the states:
# - set the start of the next period with the current timestamp
date +%s > VAR_START &
# - update the period of work index from the one computed by the `$VAR_NIDX` variable
echo $VAR_NIDX > VAR_IDX &
# - update the state from the one computed by the `$VAR_NSTATE` variable
echo $VAR_NSTATE >VAR_STATE
```

Instead of having `command=bash -c '...'` we could have a simpler file name `ON_START;wait=500;disabled=$VAR_IS_OFF;quiet`, make the file executable, and set the following content inside the file:

```bash
#!/usr/bin/env bash

SECONDS=$(date +%s)
while ((SECONDS < $SDFS_VAR_END))
do 
    echo $SECONDS > VAR_LAST
    sleep $SDFS_VAR_CONF_REFRESH
done
aplay alert.wav &
date +%s > VAR_START &
echo $SDFS_VAR_NIDX > VAR_IDX &
echo $SDFS_VAR_NSTATE >VAR_STATE
```

Note that variables are not replaced inside events files, but they are available as environment variables (see for example how we use `$SDFS_VAR_END` instead of `$SDFS_END`).

But notice that by doing this, `streamdeckfs` does not know the content of the file so it won't reload the event if a variable change. Here it will still work because we have `$VAR_IS_OFF` in the filename, that depends on `$VAR_STATE`, so whenever `$VAR_STATE` changes, the `ON_START` event will be restarted. But imagine we don't want this `disabled` configuration option but still want the script to be reloaded when `$VAR_STATE` changes, we could have done `enabled={True or "$VAR_STATE"}`: in this case it's always enabled (`True or...` is always true), but we reference the `$VAR_STATE` variable.

### Display

Not let's see how things are displayed on the key.

#### Texts

We have 4 text parts:

- `TEXT;line=1;name=off;fit;text=🍅;enabled=$VAR_IS_OFF`

Will display of tomato on the full key when the pomodoro is not started

- `TEXT;line=2;name=tomatoes;fit;text={"🍅"*($VAR_IDX-if("$VAR_STATE"=="work",0,1))}{"⚫️"*($VAR_CONF_NB-$VAR_IDX+if("$VAR_STATE"=="work",0,1))};margin=3%,0,77%,0;disabled=$VAR_IS_OFF`

Will display, at the top of the key, as many slots as we have work periods to do, using 🍅 for the periods done + the current one, and ⚫️ for the remaining work periods.

You can see the usage of the `if(condition, value-if-true, value-if-false)` function that can be used in epxressions, to count a break as the work period preceding it (so we remove one 🍅 and add one ⚫️). It's needed because of the way we compute `$VAR_NIDX`)

- `TEXT;line=3;name=pause;text=⏸;margin=30%,0,10%,0;fit;opacity={100-round($VAR_ELAPSED)};enabled=$VAR_IS_BREAK`

Pause icon displayed in the middile of the key when [we are in a break](https://www.youtube.com/watch?v=xFjqlgupAe0)

The top margin is used to leave room for the row of tomatoes at the top, and bottom margin for the progress bar (see later in #images). 

And we reduce the opacity over time to have a really visible way to know if we're need the end of the break.

- `TEXT;line=4;name=work;text=⏲;margin=30%,0,0,0;fit;opacity=50;enabled=$VAR_IS_WORK`

Timer clock displayed in the middle of the key when we are in a period of work.

The top margin is used to leave room for the row of tomatoes at the top.

The opacity is set to 50% to not be to flash when we work, and also to let the progress indicator be visible (because this is a text, and the progress indicator is an image (a drawing), the text is always displayed on top of images, so we need the text to be partially transparent to see the image underneath)

#### Images

We have two images that are used as progress indicators:

- `IMAGE;layer=1;name=progress-pause;draw=line;outline=white;width=3;coords=0,99%,$VAR_ELAPSED%,99%;enabled=$VAR_IS_BREAK`

An white horizontal progress bar on the bottom of the key when we are in a break period.

- `IMAGE;layer=2;name=progress-work;draw=pieslice;coords=22%,36%,78%,92%;angles=0,$VAR_ELAPSED%;width=0;fill=red;enabled=$VAR_IS_WORK`

A "pie slice" partially covering the face of the timer clock indicating the progress of the work period. We use the fact that angles can be defined as percentages to avoid doing maths. The percentages in the `coords` configuration option are not mathematically computed, but manuall adjusted by trial and error.
