# Faust (AutoReaper) plugin

A CLAP effect that runs [Faust](https://faustdoc.grame.fr) code. The code is the plugin's state, so REAPER stores
it in the project like any plugin's settings: copy the effect, save it in an FX chain or open the project on
another computer, and the code comes along. Undo restores earlier code.

- **Window**: a code editor with Faust syntax highlighting, a **Compile** button (or Ctrl+Enter), the compile
  status and Faust's messages, marked on the lines they point at.
- **Compiling**: in place, with libfaust (LLVM), only when asked: Compile in the window, or new code from
  AutoReaper's `add_faust_fx` / `edit_faust_fx` tools, or when a project loads. If the code does not compile, the
  effect keeps running its last compiled code.
- **Audio**: inputs are main L, R and sidechain L, R (the pins "Sidechain 1/2"); outputs are L, R. A Faust
  program with one output feeds both.
- **MIDI** (a note input port): Faust's MIDI metadata drives controls, e.g. `button("kick [midi:key 36]")` is 1
  while note 36 is held; also `keyon`, `keyoff`, `ctrl`, `pitchwheel`, `chanpress`, `pgm`, with an optional
  channel (`[midi:key 36 10]`). The block is split at each message, so a control changes on its exact frame.
- **Host sync**: controls marked `[host:beat]` (quarter notes from the project start), `[host:bpm]`,
  `[host:playing]`, `[host:bar]` (quarter notes to the current bar), `[host:num]`, `[host:den]` follow the
  transport. While playing, the block is split on every 1/48 of a quarter note (and at least every 32 frames),
  so `floor(beat)` changes on the frame where the beat starts. Unknown names are reported as warnings.
- **Unfinished edits**: code changed in the window and not compiled yet is kept in the state too (as a draft),
  so saving the project keeps it, and AutoReaper's `edit_faust_fx` refuses to overwrite it.

## Install a package

The package workflow (`.github/workflows/package.yml`) builds a zip per system that includes libfaust and the
Faust libraries (Faust 2.88.0), so no Faust install is needed: `autoreaper-faust-windows-x64`,
`-macos-arm64`, `-macos-x64` and `-linux-x64`. On a tag `faust-plugin-v<version>`, or from the Actions tab
(Package the Faust plugin > Run workflow on the default branch, with publish), it publishes them with
`SHA256SUMS.txt` as a GitHub release; the MCP tool `install_faust_plugin` downloads the one for REAPER's system
from the release matching its `VERSION` (`src/autoreaper/faust_plugin.py`, kept equal to this project's version)
and installs it as below. Each zip holds a folder `AutoReaper Faust` with `INSTALL.txt`:

| System | Copy | To |
|---|---|---|
| Windows | the folder `AutoReaper Faust` (plugin, `faust.dll`, `faustlibraries`) | `%LOCALAPPDATA%\Programs\Common\CLAP` or `C:\Program Files\Common Files\CLAP` |
| macOS | `AutoReaper Faust.clap` (a bundle with libfaust inside) | `~/Library/Audio/Plug-Ins/CLAP` |
| Linux | the folder `AutoReaper Faust` (plugin, `libfaust.so.2`, `faustlibraries`) | `~/.clap` |

Then in REAPER: Options > Preferences > Plug-ins > CLAP > Re-scan. Windows needs the Microsoft Visual C++
Redistributable (x64), which most computers have. The macOS bundle is signed ad hoc, not notarized: if macOS
blocks a downloaded copy, run `xattr -dr com.apple.quarantine ~/Library/Audio/Plug-Ins/CLAP/"AutoReaper Faust.clap"`.

To make a package yourself: build with `-DAUTOREAPER_PACKAGE=ON` against a Faust release (its `lib`, `include`,
`share/faust`), then `python plugin/package.py --build plugin/build --faust <Faust release> --out dist`.

## Build

Needs CMake 3.20+, a C++17 compiler and Faust with libfaust; on Linux also the X11 headers. Use Faust 2.88 or
newer: older libfaust ignores the JIT target the plugin asks for and compiles for the CPU model, which crashes
(Illegal instruction) on machines or VMs that do not enable all of that model's instructions. CMake
fetches the CLAP SDK, Dear ImGui and ImGuiColorTextEdit. CI builds and tests it on all three systems
(`.github/workflows/ci.yml`).

| System | Faust | Configure |
|---|---|---|
| Linux (Debian/Ubuntu) | `apt install faust libx11-dev` | `cmake -S plugin -B plugin/build -DCMAKE_BUILD_TYPE=Release` |
| macOS | `brew install faust` | add `-DFAUST_DIR=$(brew --prefix)` |
| Windows | the `win64.exe` installer from [Faust's releases](https://github.com/grame-cncm/faust/releases) | add `-DFAUST_DIR="C:/Program Files/Faust"` (Visual Studio 2022) |

```bash
cmake --build plugin/build --config Release
plugin/build/engine_test                 # ducking, MIDI and beat timing to the frame, the state format
plugin/build/ui_snapshot window.ppm      # draws the window without a host
plugin/build/clap_host_test "plugin/build/AutoReaper Faust.clap"   # loads the plugin as a host does
```

(With Visual Studio the programs are in `plugin/build/Release`.) Install the plugin where REAPER looks for CLAP
plugins, then Options > Preferences > Plug-ins > CLAP > Re-scan:

| System | Copy | To |
|---|---|---|
| Linux | `AutoReaper Faust.clap` | `~/.clap` |
| macOS | the `AutoReaper Faust.clap` bundle | `~/Library/Audio/Plug-Ins/CLAP` |
| Windows | `AutoReaper Faust.clap` | `%LOCALAPPDATA%\Programs\Common\CLAP` (or `C:\Program Files\Common Files\CLAP`) |

The plugin loads libfaust from the Faust install: on Windows its `lib` folder (with `faust.dll`) must be on
`PATH`, or copy the DLL beside the plugin. It finds the Faust libraries (`stdfaust.lib`) in
`AUTOREAPER_FAUST_LIBRARIES`, a `faustlibraries` folder beside the plugin (in a macOS bundle: `Contents/Resources`),
or the Faust install's `share/faust`.

On Windows, REAPER may take some keys for its own shortcuts while the plugin window has focus; if typing in the
editor triggers actions, enable "Send all keyboard input to plug-in" in the FX window's menu.

## Status

| | Linux | macOS | Windows |
|---|---|---|---|
| Builds; engine test (ducking, MIDI and beat timing to the frame); the plugin loaded as a host loads it (ports, state, sidechain ducking, compile errors); the window drawn | CI, Faust 2.70.3 | CI (arm64), Faust 2.85.9 | CI (x64), Faust 2.88.0 |
| The window in a host: editing, keyboard, clipboard, Compile, errors | tested in REAPER 7.81 | written, not yet tried in a host | written, not yet tried in a host |
| In REAPER: project save and reopen, AutoReaper's tools, sidechains | tested in REAPER 7.81 | not yet tried | not yet tried |

A plain build uses the Faust installed on the computer; a package carries its own (see Install a package).

## State format

Text with sized fields, so code needs no escaping (`src/engine.cpp`):

```
AutoReaperFaust 1
faust <version>        (the libfaust that compiled it; informative)
status ok|error|none
inputs <n>
outputs <n>
messages <bytes>
<Faust's messages from the last compile>
code <bytes>
<the code that runs>
draft <bytes>          (only while there is one)
<code changed in the window, not compiled yet>
```

Loading a state always compiles `code`; `status`, `messages`, `inputs` and `outputs` only report the result.
