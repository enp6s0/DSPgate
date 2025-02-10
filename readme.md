# DSPgate

A lightweight gateway to enable HTTP-based control of Biamp Tesira DSPs (through its SSH connection).

Note that this is a *work-in-progress*, and not every DSP block type (or every feature of a block) is supported yet!

### What's currently supported?

* DSP attributes listing
* Mute and level adjustment on `LevelControl`, `MuteControl`, `Dante`, `USB`, and `AudioOutput` blocks
* Output mute, input level adjustment, and active source selection on `SourceSelector` blocks

### Usage examples

#### Get all supported blocks: send `GET` to `/block`

```
{
  "blocks": {
    "LevelMainMix": {
      "type": "LevelControl"
    },
    "MicMute": {
      "type": "MuteControl"
    }
  }
}
```

#### Get detailed block information (and current values): send `GET` to `/block/<blockID>`

```
{
  "channels": {
    "1": {
      "idx": 1, 
      "label": "CH L", 
      "level": {
        "current": -3.5, 
        "maximum": 0.0, 
        "minimum": -40.0
      }, 
      "muted": false
    }, 
    "2": {
      "idx": 2, 
      "label": "CH R", 
      "level": {
        "current": -3.5, 
        "maximum": 0.0, 
        "minimum": -40.0
      }, 
      "muted": false
    }
  }, 
  "ganged": true, 
  "supported": true, 
  "type": "LevelControl"
}
```

#### Set values: send `POST` (or `PATCH`) to `/block/<blockID>` with JSON body:

Mute (or unmute) all channels:
```
{"channel": {"0" : {"mute": "true"}}}
```

> The special channel ID `0` is used to refer to *all channels*

Set level of channels:
```
{"channel": {"0" : {"level": "-10"}}}
```

Combining multiple requests in one:
```
{
  "channel": {
    "0" : {"mute": "false"},
    "2" : {"level" : -4.2}
  }
}
```

> Channels are processed in the order in which it was received (top to bottom) in case of conflicts

Set source selector output mute, selected channel, and input levels:
```
{
  "mute": false, 
  "selected" : 0, 
  "sources" : {
    "1": {"level" : "0"},
    "2": {"level" : {
      "current" : -20.0
    }}
  }
}
```

> Selecting channel 0 has the effect of un-selecting an option (set selection to none)

> To select a channel, `selected` can also be labels (first match will be used), although it's probably better to just use numerical indices

> Input levels can be configured in two ways as shown, either directly or as a nested `current` item that matches the output data format

### License
Copyright 2025 @enp6s0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.