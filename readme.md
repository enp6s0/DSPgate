# DSPgate

**WORK IN PROGRESS**

A lightweight gateway to enable HTTP-based control of Biamp Tesira DSPs (through its SSH connection)

### What's currently supported?

* DSP attributes listing
* `Mute` and `Level` DSP blocks

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