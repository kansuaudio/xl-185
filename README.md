# XL-185 Sequencer

An eight-stage sequencer with modulation lanes for Ableton Live (Max for Live MIDI effect).

XL-185 is built on **ML-185 Sequencer by Nico Starke** (2009). The stage-sequencer idea, the original
device and its design are his work, and the credit for them goes to him. XL-185 rebuilds and extends
that device; the untouched original is kept in this repository as the build input.

## What it does

XL-185 sits in front of an instrument and plays a loop of eight stages. Each stage holds a pitch for
its own number of clock steps and decides what to do with that time: rest, one note, one held note,
or repeats on every 4th, 3rd, 2nd or single step. The stage lengths don't have to add up to a bar, so
the loop drifts against the beat until Restart brings it back.

- 8 stages, 1 to 8 clock steps each; Rate 1/32 to 1/4, Swing, Transpose, Direction (forward,
  backward, back and forth, random stage), Restart every 1, 2, 4 or 8 bars, Gate for the note length
- Per stage: pitch, velocity, Mode, length, probability, slide (303-style on a mono synth with glide),
  a lock that keeps the stage safe from every random tool, and an on / off switch on the stage number
- 26 scales, 8 of them microtonal (played with pitch bend), or follow Live's own scale
- Slices mode keeps every note on a slice of a Simpler in Slicing mode
- Key In: notes or a clip on the same track transpose the sequence (Latch or Hold), with MIDI Thru
- 3 modulation lanes that step with the stages and drive any parameter Live can map, with Min, Max
  and Smooth
- Global, Mutate and Auto (mutate every 4, 8 or 16 bars), limited to the parts you tick, and a Reset per
  page; Global, Mutate and the lanes' Random can be mapped to keys and pads
- Shift arrows rotate the pattern, or one lane, by a stage
- 32 steps of undo for the random tools and the shift arrows
- Foldable settings column, parameter banks for Push

The full user guide is [XL-185 - User Guide.pdf](XL-185%20-%20User%20Guide.pdf).

## Versions

The current version is 1.1. [CHANGELOG.md](CHANGELOG.md) lists what changed; every version's device is
attached to its [release](https://github.com/kansuaudio/xl-185/releases).

## Requirements

Ableton Live 12 Suite, or Live 12 Standard with Max for Live.

## Install

Download [`device/XL-185 Sequencer.amxd`](device/XL-185%20Sequencer.amxd) and drop it on a MIDI track
in front of an instrument, or save it anywhere in your Live User Library.

## Building from source

The device is generated: `build_ml185.py` takes the original ML-185 device, edits its patcher and
embeds the three scripts. Needs Python 3.

    ./install.sh

builds `device/XL-185 Sequencer.amxd`.
Give it a path to also install the build there (the file it replaces is kept in `backups/`):

    ./install.sh "<Live User Library>/Presets/MIDI Effects/Max MIDI Effect/XL-185 Sequencer.amxd"

| Path | Contents |
|------|----------|
| `ML-185 Sequencer (original).amxd` | Nico Starke's original device, unchanged; the build input |
| `build_ml185.py` | builds XL-185 from the original and the scripts |
| `ml185scale.js` | scales, random tools, Mutate, Auto, undo, Slices |
| `ml185stage.js` | per-stage tables, note path, modulation lanes |
| `ml185box.js` | the drawn controls |
| `face.py` | renders the device face as an HTML preview |
| `CHANGELOG.md` | what changed in each version |

## Credits

Original device: ML-185 Sequencer by Nico Starke (2009, v0.9).
XL-185 rebuild and extensions: Kansu Audio.
