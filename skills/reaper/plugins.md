# Working with plugins (VST, VST3, CLAP, AU, JS)

Read this before adding, configuring or routing plugins. Nothing here is specific to one plugin:
learn each plugin from what it exposes, its manual, and measurements.

## Tools

| Tool | Use |
|---|---|
| `search_installed_fx` | Find the exact installed name when you are not sure it exists |
| `add_fx` | Add by name or words ("pro q", "ReaEQ"); several matches return candidates and add nothing. `bypassed: true` to set it up silently |
| `fx_parameters` | What the plugin exposes: index, name, 0..1 value, value as displayed, display range, settings of switch/list parameters. `query` filters by name; `pins: true` lists input pins |
| `set_fx_parameters` | Set by display value ("130 Hz", "1.2 kHz", "-18 dB", "4:1", "35 %", "Spectral", "On"), read back. One Undo step for the batch |
| `edit_fx` | bypass / enable / offline / online / remove / move / show (opens the window for the user) |
| `sidechain_send` | Audio or MIDI sidechain send, including the plugin's sidechain pins |

Tracks are a GUID, `"master"` or an exact unique name; an FX is its GUID, chain index or a unique
part of its name. Prefer GUIDs from `inspect_project` when names repeat.

## Workflow

1. **Read before setting.** `fx_parameters` with a `query` for the area you need (e.g. "release",
   "side", "mode"). The `range` shows units and limits; `choices` shows a list parameter's options.
   Parameter names can be vague; check the display range or the manual before assuming what one does.
2. **Set by display value, never by guessing 0..1.** Then compare each result's `after` with what you
   asked. A note "closest reachable" means the value is outside the range. If a plugin cannot display
   unset values, `set_fx_parameters` says so; then pass `normalized: true` and check the readback.
3. **Measure the effect** (`capture` + `analyze.py --compare`) on the same range before and after,
   ideally on the track solo and in the mix. When comparing plugins or settings, match levels first, or
   the louder one wins.
4. **Keep changes reversible.** Every tool call is one Undo step. To compare alternatives, add the new
   plugin bypassed, set it up, then switch which one is bypassed instead of deleting the old one.

## What the host cannot see

- Only parameters the plugin exposes to the host are visible. Macros, modulation matrices, LFO
  routings, sample or wavetable choices and some modes often exist only in the plugin window. If the
  answer depends on them, open it with `edit_fx` action `show` and ask the user, or read the manual.
- Presets: `fx_parameters` reports the current preset name when the plugin reports one.
- Many synths and effects have free-running LFOs, analog drift, random modulation or modulated
  reverbs, so two renders of the same range are not identical. Before trusting a small difference,
  render the same unchanged range twice and compare: that difference is your noise floor.
- Plugin manuals (PDF) explain behaviour parameters do not: read the relevant section when a setting
  does not do what its name suggests.
- Demo versions may insert silence or noise, or stop after a time. If a render has dropouts, check
  whether a plugin is a demo before blaming the mix.

## Sidechains

**Audio key input** (compressor or ducker keyed by another track): `sidechain_send` with
`kind: "audio"`, `channels: 3` and `fx: <the plugin>`. It sends the source to channels 3/4 of the
target, widens the target track to 4 channels and connects the plugin's input pins named like
"Side Chain", "Aux" or "Key" (else pins 3/4) to those channels. Then switch the plugin's own sidechain
or key-input setting to external with `set_fx_parameters` (look for it with `query: "side"`).
Check `pins_after`. If the target already uses channels 3/4 (multi-out instruments), use 5.

**MIDI trigger** (plugins that duck or gate on MIDI notes): `kind: "midi"`. A plugin listens to all
MIDI on its track. If the target track also has an instrument or its own notes, those notes trigger
the plugin too, and the trigger notes would play the instrument. Then use `midi_bus: 2`: the trigger
arrives on MIDI bus 2, the instrument keeps bus 1. REAPER has no script API for a plugin's MIDI input
bus, so ask the user to set it: plugin window, pin connector button ("2 in 2 out") > I/O > MIDI input >
Bus 2. After that set the plugin's trigger/sidechain mode to MIDI.

**Host-synced ducking** (a plugin's own tempo-synced envelope) keeps pumping where the kick pauses;
MIDI triggering follows the actual kick notes. Say which one you chose and why.

## Raw REAPER API for anything else

`reaper_eval` reads and `reaper_eval_write` changes anything the tools above do not cover, for example:
pin mappings (`TrackFX_GetPinMappings` / `TrackFX_SetPinMappings`, a bitmask of channels:
channel n is `1 << (n-1)`), pin names (`TrackFX_GetNamedConfigParm(track, fx, "in_pin_0")`), send flags
(`I_SRCCHAN` = -1 for MIDI only; `I_MIDIFLAGS`: low 5 bits source channel, 31 disables MIDI,
`(flags >> 22) & 255` destination bus), parameter modulation and links
(`param.N.mod.*`, `param.N.plink.*` named config), or take FX (`TakeFX_*`).
