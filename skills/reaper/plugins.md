# Working with plugins (VST, VST3, CLAP, AU, JS)

Read this before adding, configuring or routing plugins. Nothing here is specific to one plugin:
learn each plugin from what it exposes, its manual, and measurements.

Add a plugin only when the user asked for one or agreed to your proposal. Never put one on the
master or on a new track on your own: first look for what the project already has (the FX on the
track, existing automation lanes) and work with that, or propose the addition and wait.

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
   asked. A note "closest reachable" means the value is outside the range. Some plugins format unset
   values wrongly or not at all; `set_fx_parameters` notices when the readback differs from the
   prediction and then searches by setting and reading back (the note says "found by setting and
   reading back"). A change it cannot reach fails and leaves that parameter as it was. `changed` is
   true when any parameter's value or display moved.
3. **Measure the effect** (`capture` + `analyze.py --compare`) on the same range before and after,
   ideally on the track solo and in the mix. When comparing plugins or settings, match levels first, or
   the louder one wins.
4. **Keep changes reversible.** Every tool call is one Undo step (`sidechain_send` included, also for
   pin changes). To compare alternatives, add the new plugin bypassed, set it up, then switch which one
   is bypassed instead of deleting the old one.

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
A multi-out instrument earlier in the chain writes every output it has mapped, silence included: if
its outputs reach the key channels, they overwrite the key before the plugin hears it. Read the
instrument's output pins (`TrackFX_GetPinMappings(track, fx, 1, pin)`) and map its unused outputs off
the key channels, or keep the key above the highest channel it uses. Then render the target with the
source and check the plugin actually reacts (gain reduction in time with the key).

**MIDI trigger** (plugins that duck or gate on MIDI notes): `kind: "midi"`. A plugin listens to all
MIDI on its track. If the target track also has an instrument or its own notes, those notes trigger
the plugin too, and the trigger notes would play the instrument. Then use `midi_bus: 2`: the trigger
arrives on MIDI bus 2, the instrument keeps bus 1. REAPER has no script API for a plugin's MIDI input
bus, so ask the user to set it: plugin window, pin connector button ("2 in 2 out") > I/O > MIDI input >
Bus 2. After that set the plugin's trigger/sidechain mode to MIDI.

**Host-synced ducking** (a plugin's own tempo-synced envelope) keeps pumping where the kick pauses;
MIDI triggering follows the actual kick notes. Say which one you chose and why.

## Faust effects

When no installed plugin does the job simply, and the user agrees, write the effect in Faust with
`add_faust_fx`. It runs in the Faust (AutoReaper) plugin; the user sees and edits the code in its window, and the
project stores it. Use it for small, precise processing you can state in a few lines: a ducker keyed by another
track, a gain or filter utility, a gate, a custom envelope. Prefer the standard library (`import("stdfaust.lib");`:
`an.amp_follower_ar`, `ba.db2linear`, `si.smoo`, `fi.lowpass`, `co.compressor_stereo`, ...).

- Inputs are main L, R, then sidechain L, R; outputs L, R. A sidechain ducker:

  ```faust
  import("stdfaust.lib");
  depth = -9;          // dB while the key is loud
  threshold = 0.05;    // key level that starts the ducking
  key(kl, kr) = (abs(kl) + abs(kr)) / 2 : an.amp_follower_ar(0.002, 0.15);
  gain(kl, kr) = ba.db2linear(depth * (key(kl, kr) > threshold)) : si.smoo;
  process(l, r, kl, kr) = l * g, r * g with { g = gain(kl, kr); };
  ```

  Then `sidechain_send` from the key track, `kind: "audio"`, `channels: 3`, `fx: <the effect>`.
- Write settings as named constants with a comment, so the user can read and change them in the window.
- If the code does not compile, nothing is added (or the edit is not applied) and `messages` has Faust's errors
  (`faust : <line> : ERROR : ...`, lines counted from the first line of your code). Fix and retry.
- To change it, `read_faust_fx` first and pass its `version` to `edit_faust_fx`. A refusal means the user changed
  the code or is editing it (`draft`): show them what you wanted to change instead of overwriting.
- Measure the result like any plugin: `capture` before and after, `analyze.py --steps 16` for ducking.
- If `add_faust_fx` says the plugin is missing, tell the user it is built from the repository's `plugin/` folder.

## Raw REAPER API for anything else

`reaper_eval` reads and `reaper_eval_write` changes anything the tools above do not cover, for example:
pin mappings (`TrackFX_GetPinMappings` / `TrackFX_SetPinMappings`, a bitmask of channels:
channel n is `1 << (n-1)`), pin names (`TrackFX_GetNamedConfigParm(track, fx, "in_pin_0")`), send flags
(`I_SRCCHAN` = -1 for MIDI only; `I_MIDIFLAGS`: low 5 bits source channel, 31 disables MIDI,
`(flags >> 22) & 255` destination bus), parameter modulation and links
(`param.N.mod.*`, `param.N.plink.*` named config), or take FX (`TakeFX_*`).

REAPER adds no Undo point for `TrackFX_SetPinMappings` alone (the change is applied but Ctrl+Z skips
it). In the same `reaper_eval_write` call, switch that FX off and on again
(`TrackFX_SetEnabled(track, fx, false)` then `true`); the Undo point then restores the pins too.
