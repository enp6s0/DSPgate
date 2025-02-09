# DSPgate

**WORK IN PROGRESS**

A lightweight gateway to enable HTTP-based control of Biamp Tesira DSPs (through its SSH connection)

### What's currently supported?

* DSP attributes listing
* `Mute` and `Level` DSP blocks

### Usage examples

#### Get all supported blocks: send `GET` to `/block`

#### Get values: send `GET` to `/block/<blockID>`

#### Set values: send `POST` (or `PATCH`) to `/block/<blockID>` with JSON body:

Mute (or unmute) all channels (special channel `0` = all channels):
```
{"channel": {"0" : {"mute": "true"}}}
```

Set level of channels:
```
{"channel": {"0" : {"level": "-10"}}}
```