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
- **Unfinished edits**: code changed in the window and not compiled yet is kept in the state too (as a draft),
  so saving the project keeps it, and AutoReaper's `edit_faust_fx` refuses to overwrite it.

## Build

Needs CMake 3.16+, a C++17 compiler, Faust with libfaust (2.60+), and on Linux the X11 headers. CMake fetches
the CLAP SDK, Dear ImGui and ImGuiColorTextEdit.

```bash
# Debian/Ubuntu: apt install faust libx11-dev cmake g++
cmake -S plugin -B plugin/build -DCMAKE_BUILD_TYPE=Release
cmake --build plugin/build -j
plugin/build/engine_test                 # compiles and runs a ducker, checks the state format
plugin/build/ui_snapshot window.ppm      # draws the window without a host
mkdir -p ~/.clap && cp "plugin/build/AutoReaper Faust.clap" ~/.clap/
```

Then in REAPER: Options > Preferences > Plug-ins > CLAP > Re-scan. The plugin finds the Faust libraries
(`stdfaust.lib`) in `AUTOREAPER_FAUST_LIBRARIES`, a `faustlibraries` folder beside the plugin, or the usual Faust
install locations.

## Status

| | Linux | Windows | macOS |
|---|---|---|---|
| Audio, state, compiling | tested in REAPER 7.81 | not built yet | not built yet |
| Window (editor) | tested in REAPER 7.81 (X11) | to do: a Win32 child window | to do: an NSView |

The window draws in software (ImGui's triangles into a bitmap, `src/gui/raster.cpp`), so another platform needs
only a child window that shows the bitmap and passes on mouse, keyboard and clipboard (`src/gui/x11_window.cpp`
is the Linux one). Shipping it to users also needs libfaust bundled beside the plugin with the Faust libraries.

## State format

Text with sized fields, so code needs no escaping (`src/engine.cpp`):

```
AutoReaperFaust 1
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
