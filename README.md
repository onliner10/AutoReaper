# AutoReaper

A [Claude Code](https://claude.com/claude-code) plugin for [REAPER](https://www.reaper.fm).
Claude can read your open project (tracks, FX, items, MIDI, routing, tempo map), make the edits
you ask for with ReaScript, and render parts of the song to measure them, since it cannot listen.

It has two parts:

- an MCP server (Python, started by Claude Code through `uv`) that provides the tools, and
- **AutoReaper Bridge**, a Lua script you run inside REAPER. The server and the bridge pass
  requests through files in `~/.autoreaper/bridge`; nothing listens on the network.

## Tools

| Tool | What it does |
|---|---|
| `reaper_status` | Whether REAPER and the bridge are running; REAPER version, open project |
| `install_bridge` | Copies the bridge script into REAPER's `Scripts` folder; a running bridge reloads it |
| `inspect_project` | Tracks (GUIDs, volume, pan, mute/solo, folders), FX, items, markers, tempo; filter by track name or bars |
| `inspect_signal_flow` | Folder tree, sends, sidechains, sources, master FX |
| `search_installed_fx` | Installed plugin names, without loading any |
| `reaper_eval` | Read-only ReaScript Lua in a sandbox (getters only) |
| `reaper_eval_write` | ReaScript Lua that changes the project: one Undo step, project backed up first |
| `read_receipt` | The result of a request that timed out, without running it again |
| `add_fx` | Adds a plugin by name or words from it; lists candidates when several match |
| `fx_parameters` | A plugin's parameters as it displays them, with ranges, list options and input pins |
| `set_fx_parameters` | Sets parameters by display value ("130 Hz", "-18 dB", "Spectral") and checks the readback |
| `edit_fx` | Bypass, enable, offline, remove, move or show a plugin |
| `sidechain_send` | Audio sidechain (send, track channels, plugin pins) or MIDI trigger send on a MIDI bus |
| `capture` | Renders a bar or time range (full mix or tracks soloed by GUID or name) offline to a WAV |
| `add_faust_fx` | Adds an effect written in Faust (needs the Faust plugin below); nothing is added if it does not compile |
| `read_faust_fx` | A Faust effect's code, version, compile status and messages, and the user's uncompiled changes |
| `edit_faust_fx` | Replaces a Faust effect's code; refused if the code changed since it was read or the user is editing it |

A `reaper` skill tells Claude how to use them: inspect first, measure before and after, and edit
only what you asked for. It includes `scripts/analyze.py`, which Claude runs on captured WAVs:

- levels and a per-bar table of band energy (sub to air), RMS, spectral centroid and side/mid,
- `--compare before.wav`: the per-bar difference in dB, for "did this change help?",
- `--steps 16`: the same per sixteenth of the bar, averaged over bars, for groove and ducking
  (`--bars 57-64` limits any table to a range),
- `--spectrogram`: a PNG with bar lines, for an overview. Conclusions come from the numbers.

The analysis runs outside the MCP server (`uv run --script`, which installs numpy, soundfile and
Pillow for the script on first use), so the server itself stays small.

## Faust effects

When no installed plugin does something simply (a ducker keyed by the drums, a utility, a custom filter),
Claude can write the effect in [Faust](https://faustdoc.grame.fr). It runs in the **Faust (AutoReaper)** CLAP
plugin from [`plugin/`](plugin/README.md), which keeps the code as its own state: the project stores it like any
plugin's settings, Undo restores earlier code, and the project plays on another computer that has the plugin.
The plugin window is a code editor where you can read and change the code and click Compile; Faust's errors show
on their lines. Effects can listen to a sidechain (audio on the plugin's Sidechain pins, or MIDI notes and CCs)
and follow REAPER's transport (beat, tempo, bar) to react on every quarter note. The plugin is built from source for now and tested on Linux (see its README).

## Requirements

- REAPER 6 or 7 on Windows, macOS or Linux.
- Claude Code.
- [uv](https://docs.astral.sh/uv/getting-started/installation/). Claude Code starts the server
  with `uv run`; on first start uv fetches Python 3.10+ if needed and installs its two
  dependencies (`mcp`, `filelock`) into the plugin folder. The analysis script's dependencies are
  installed by uv the first time Claude runs it.

## Install

1. Add the plugin in Claude Code:

   ```
   /plugin marketplace add onliner10/AutoReaper
   /plugin install autoreaper@autoreaper
   ```

   or from a terminal:

   ```bash
   claude plugin marketplace add onliner10/AutoReaper
   ```

   ```bash
   claude plugin install autoreaper@autoreaper
   ```

   Then restart Claude Code so the server starts.

2. Install the bridge into REAPER. Ask Claude to "set up AutoReaper" and it calls
   `install_bridge`. Or copy [`src/autoreaper/lua/bridge.lua`](src/autoreaper/lua/bridge.lua)
   yourself into the `Scripts` folder of REAPER's resource path (Options > Show REAPER resource
   path) as `AutoReaper Bridge.lua`.

3. In REAPER: **Actions > Show action list > New action > Load ReaScript**, choose
   `AutoReaper Bridge.lua`, then **Run**. It keeps running in the background until REAPER closes.
   To start it with REAPER, add this line to `Scripts/__startup.lua` in the resource folder:

   ```lua
   dofile(reaper.GetResourcePath() .. "/Scripts/AutoReaper Bridge.lua")
   ```

4. For `capture`: open **File > Render**, set the render speed to **Full-speed Offline** once and
   close the dialog. AutoReaper refuses to render in realtime.

5. Ask Claude something like "what's on my tracks?". It calls `reaper_status` and
   `inspect_project`.

### Updating

`/plugin update autoreaper@autoreaper`, restart Claude Code, then ask Claude to run `install_bridge`:
it copies the new bridge script and the running bridge reloads it by itself. Bridges from 0.1.x cannot
reload; for those, run the bridge action in REAPER again once and, when REAPER asks whether to
terminate the running script or launch a new instance, choose **New instance**.

## Safety

- `reaper_eval` runs in a restricted Lua environment with REAPER's getter functions and no
  `io`, `os` or `load`. Anything else stops with `needs_write_access`.
- `reaper_eval_write` is full ReaScript with your user's rights: it can do anything a REAPER
  script can, including reading and writing files. Claude Code asks before each call unless you
  allow the tool. Each call is one Undo step (Ctrl+Z), and a copy of the project file (no media)
  is saved to `~/.autoreaper/backups` first; backups older than 3 days or beyond 128 MB in total
  are deleted.
- `capture` stops playback if REAPER is playing (it never stops a recording), temporarily changes
  render settings and mute/solo, and restores them afterwards. The project is not changed.
- An edit is refused if a different project became active since Claude last read it.
- A request that times out is never sent again automatically.

To stop Claude Code asking before the read-only tools, allow them in `/permissions`, for example
`mcp__plugin_autoreaper_autoreaper__inspect_project`.

## Files

Everything AutoReaper writes is under `~/.autoreaper` (`%USERPROFILE%\.autoreaper` on Windows):

| Folder | Contents |
|---|---|
| `bridge/` | Request and receipt files, heartbeat, `bridge-errors.log` |
| `captures/` | Rendered WAVs, their `.json` bar grids and spectrogram PNGs |
| `backups/` | Project copies made before edits |
| `results/` | Tool results too large to show inline |

Set `AUTOREAPER_HOME` to move it. The bridge reads the same variable, so REAPER must see it too
(set it as a user environment variable, then restart REAPER and Claude Code).

## Troubleshooting

- **"The AutoReaper Bridge is not running"**: run the bridge action in REAPER. If it is running,
  check that REAPER and Claude Code use the same folder: `reaper_status` shows the mailbox path the
  server uses.
- **"Choose Full-speed Offline once in REAPER Render settings"**: see step 4.
- **The server does not start**: check that `uv --version` works in a terminal, then look at
  `/mcp` in Claude Code.
- Errors inside the bridge are logged to `~/.autoreaper/bridge/bridge-errors.log`.

## Development

```bash
uv run pytest
```

The tests cover the analysis script, argument checks and the bridge protocol, including the
real `bridge.lua` and read-only sandbox running in Lua 5.4 against a fake REAPER API (through
`lupa`). To try a working copy in Claude Code without installing it:

```bash
claude --plugin-dir /path/to/AutoReaper
```

## License

MIT, see [LICENSE](LICENSE).
