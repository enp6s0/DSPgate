# DSPgate

A lightweight gateway to enable HTTP-based control of Biamp Tesira DSPs (through its SSH connection)

Note that this is a *work-in-progress*, and not every DSP block type is supported yet

### What's currently supported?

* DSP attributes listing
* Mute and level adjustment on `LevelControl`, `MuteControl`, `Dante`, `USB`, and `AudioOutput` blocks

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

Mute (or unmute) all channels (special channel `0` = all channels):
```
{"channel": {"0" : {"mute": "true"}}}
```

Set level of channels:
```
{"channel": {"0" : {"level": "-10"}}}
```

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