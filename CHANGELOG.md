# Changelog

## 1.1 - 2026-11-16

### New

- **Stage on / off.** Click a stage's number to switch it off, click again to bring it back. Off is
  silent, keeps its time in the cycle and keeps its Mode. Parameters "Stage1 On" to "Stage8 On", and a
  Push bank "On".
- **Keys and pads.** Global, Mutate (both pages) and the lanes' Random can be mapped to a key or a MIDI
  pad. Every press runs the tool.
- **Grey stages.** A stage that does not play, beyond Length or switched off, turns grey. It cannot be
  changed by hand until it plays again; its padlock still works.
- **Gate.** One control in the settings column, 1 to 100 %, for the length of every note that does not
  slide. 100 % is the full length, as before. Notes with Slide on ignore it. Parameter "Gate".
- **Shift arrows.** On the Sequencer page, < and > over the last stages rotate the whole pattern by one
  stage inside Length: pitch, velocity, Mode, Len, Prob, Slide, on / off and lock travel together. On the
  Mod page each lane has its own pair for its knob values. One step for the undo arrow per press.

### Changed

- **Reset** puts its whole page back, whatever is ticked. On the Sequencer page: every row of every
  unlocked stage (Length is left alone). On the Mod page: the knobs and each lane's Min, Max and Smooth
  (the mappings stay). The ticks steer Global and Mutate only.
- **The undo arrow** takes back only what the last tool changed. What you set by hand afterwards stays.
- **Mutate and Auto** on the Sequencer page pick only stages that are switched on.
- The Map button is narrower and lines up with the boxes below it; unmap is a capital X.

### Fixed

- Mod lanes could move the mapped parameter only inside the first 1 % of its range. The device now scales
  each lane to the parameter's own range itself.
- With Live under load, a late clock check could restart the stage that was playing: a held note was
  attacked again, a "once" stage played twice.
- A mapped Map button could turn solid orange without showing the parameter's name.
- Lane letters and the Pitch and Vel labels sit level with their dice.

### Good to know

- A controller button that sends a momentary CC (127 on press, 0 on release) runs a mapped tool twice.
  Send a note instead, or set the button to toggle.
- Every 1.0 parameter keeps its name and range.

## 1.0 - 2026-11-12

First release.
