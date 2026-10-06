// AutoReaper Faust: a CLAP effect that runs Faust code compiled in place. The
// code is the plugin's state, so the host stores it in the project like any
// plugin setting. Audio: main stereo + sidechain stereo in, stereo out. The
// window is a code editor with a Compile button.
#include "engine.h"
#include "gui/ui.h"
#include "gui/window.h"

#include <clap/clap.h>

#include <algorithm>
#include <cstdint>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <exception>
#include <iterator>
#include <memory>
#include <string>
#include <vector>

#if defined(_M_X64) || defined(__x86_64__)
#include <xmmintrin.h>
#endif

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace autoreaper {
namespace {

const char* kDefaultCode =
    "import(\"stdfaust.lib\");\n\n"
    "// Inputs: main L, R, sidechain L, R. MIDI: controls with [midi:key 36], [midi:ctrl 1], ...\n"
    "// Host: controls with [host:beat] (quarter notes), [host:bpm], [host:playing], [host:bar].\n"
    "process = _, _;\n";
constexpr int kDefaultWidth = 760, kDefaultHeight = 480;

// The folder of this plugin's binary (inside the bundle on macOS).
std::string plugin_dir() {
#ifdef _WIN32
    HMODULE module = nullptr;
    wchar_t path[MAX_PATH * 4];
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&plugin_dir), &module) &&
        GetModuleFileNameW(module, path, DWORD(std::size(path)))) {
        char utf8[MAX_PATH * 12];
        if (WideCharToMultiByte(CP_UTF8, 0, path, -1, utf8, sizeof(utf8), nullptr, nullptr)) {
            std::string file = utf8;
            return file.substr(0, file.find_last_of("\\/"));
        }
    }
#else
    Dl_info info;
    if (dladdr(reinterpret_cast<void*>(&plugin_dir), &info) && info.dli_fname) {
        std::string path = info.dli_fname;
        return path.substr(0, path.find_last_of('/'));
    }
#endif
    return "";
}

// Text that differs only in trailing whitespace is the same code.
bool same_code(const std::string& a, const std::string& b) {
    auto end = [](const std::string& s) { return s.find_last_not_of(" \t\r\n") + 1; };
    return a.compare(0, end(a), b, 0, end(b)) == 0;
}

// Members are destroyed in reverse order: the window first, while the Ui it
// forwards its last events (focus loss on close) to still exists.
struct Gui {
    std::unique_ptr<Ui> ui;
    std::unique_ptr<PlatformWindow> window = std::make_unique<PlatformWindow>();
    bool visible = false;
    int width = kDefaultWidth, height = kDefaultHeight;
    clap_id timer = CLAP_INVALID_ID;
    std::chrono::steady_clock::time_point last = std::chrono::steady_clock::now();
};

// Flush denormals to zero while processing: decaying filters and reverbs
// otherwise get very slow on some CPUs.
class FlushDenormals {
public:
#if defined(_M_X64) || defined(__x86_64__)
    FlushDenormals() : saved_(_mm_getcsr()) { _mm_setcsr(saved_ | 0x8040); }  // FTZ and DAZ
    ~FlushDenormals() { _mm_setcsr(saved_); }
private:
    unsigned int saved_;
#elif defined(__aarch64__) && !defined(_MSC_VER)
    FlushDenormals() {
        __asm__ __volatile__("mrs %0, fpcr" : "=r"(saved_));
        __asm__ __volatile__("msr fpcr, %0" : : "r"(saved_ | (uint64_t(1) << 24)));  // FZ
    }
    ~FlushDenormals() { __asm__ __volatile__("msr fpcr, %0" : : "r"(saved_)); }
private:
    uint64_t saved_;
#endif
};

struct Plugin {
    clap_plugin_t clap;
    const clap_host_t* host;
    const clap_host_state_t* host_state = nullptr;
    const clap_host_timer_support_t* host_timer = nullptr;
    double sample_rate = 48000;
    bool active = false;

    State state;
    int revision = 0;          // bumped when code or draft change outside the window
    bool code_runs = false;    // state.code is the program that runs
    bool pending = true;       // state.code not compiled yet (compiled at activation or when saved)
    double compiled_rate = 0;  // the sample rate of the running program
    std::vector<float> spare;  // an output channel the host does not give us
    std::unique_ptr<Gui> gui;

    // The audio thread owns `current`. The main thread hands a new program over
    // in `next` and frees the replaced one from `retired`.
    Program* current = nullptr;
    std::atomic<Program*> next{nullptr};
    std::atomic<Program*> retired{nullptr};
    std::array<MidiMessage, 1024> midi;  // one block's MIDI, for the audio thread

    // Compile on the main thread. On success the text becomes the running
    // code; on error the running program and code stay, with Faust's messages.
    bool try_compile(const std::string& text) {
        CompileResult result;
        try {
            result = compile(text, sample_rate, library_paths(plugin_dir()));
        } catch (const std::exception& error) {
            result.messages = std::string("Compiling failed: ") + error.what();
        } catch (...) {
            result.messages = "Compiling failed.";
        }
        state.messages = result.messages;
        pending = false;
        if (!result.program) {
            state.status = "error";
            return false;
        }
        state.status = "ok";
        state.code = text;
        state.inputs = result.program->inputs;
        state.outputs = result.program->outputs;
        code_runs = true;
        compiled_rate = sample_rate;
        install(result.program.release());
        return true;
    }

    // Compile state.code; if it does not compile, nothing runs (audio passes
    // through) rather than a program from earlier code.
    void compile_code() {
        if (try_compile(state.code)) return;
        code_runs = false;
        state.inputs = state.outputs = 0;
        compiled_rate = sample_rate;
        install(new Program());  // no instance: pass-through
    }

    // Compile what the window shows; a success clears the draft.
    void compile_draft() {
        const std::string text = state.draft.empty() ? state.code : state.draft;
        if (try_compile(text)) state.draft.clear();
        mark_dirty();
    }

    // Drop the window's uncompiled changes; the running code shows again.
    void revert_draft() {
        state.draft.clear();
        if (code_runs) {
            state.status = "ok";
            state.messages.clear();
        }
        ++revision;
        mark_dirty();
    }

    // The editor's text changed: it is a draft unless it is the running code.
    void set_draft(const std::string& text) {
        const std::string draft = same_code(text, state.code) ? "" : text;
        if (draft == state.draft) return;
        state.draft = draft;
        // Back to the running code: the failed compile of a draft no longer applies.
        if (draft.empty() && code_runs && state.status == "error") {
            state.status = "ok";
            state.messages.clear();
        }
        mark_dirty();
    }

    void mark_dirty() {
        if (host_state) host_state->mark_dirty(host);
    }

    void install(Program* program) {
        if (!active) {
            delete current;
            current = program;
            return;
        }
        delete next.exchange(program);  // an earlier one the audio thread never took
    }

    void free_retired() { delete retired.exchange(nullptr); }
};

Plugin* self(const clap_plugin_t* plugin) { return static_cast<Plugin*>(plugin->plugin_data); }

// ------------------------------------------------------------------ audio ports

uint32_t ports_count(const clap_plugin_t*, bool is_input) { return is_input ? 2 : 1; }

bool ports_get(const clap_plugin_t*, uint32_t index, bool is_input, clap_audio_port_info_t* info) {
    if (index >= (is_input ? 2u : 1u)) return false;
    std::memset(info, 0, sizeof(*info));
    info->id = index;
    std::snprintf(info->name, sizeof(info->name), "%s", index == 0 ? "Main" : "Sidechain");
    info->flags = index == 0 ? CLAP_AUDIO_PORT_IS_MAIN : 0;
    info->channel_count = 2;
    info->port_type = CLAP_PORT_STEREO;
    info->in_place_pair = CLAP_INVALID_ID;
    return true;
}

const clap_plugin_audio_ports_t kAudioPorts = {ports_count, ports_get};

// ------------------------------------------------------------------ note ports

// One MIDI input, for [midi:...] controls (triggers, CCs): a MIDI sidechain.
uint32_t note_ports_count(const clap_plugin_t*, bool is_input) { return is_input ? 1 : 0; }

bool note_ports_get(const clap_plugin_t*, uint32_t index, bool is_input, clap_note_port_info_t* info) {
    if (!is_input || index != 0) return false;
    std::memset(info, 0, sizeof(*info));
    info->id = 0;
    info->supported_dialects = CLAP_NOTE_DIALECT_CLAP | CLAP_NOTE_DIALECT_MIDI;
    info->preferred_dialect = CLAP_NOTE_DIALECT_MIDI;
    std::snprintf(info->name, sizeof(info->name), "MIDI in");
    return true;
}

const clap_plugin_note_ports_t kNotePorts = {note_ports_count, note_ports_get};

// ------------------------------------------------------------------ state

bool state_save(const clap_plugin_t* plugin, const clap_ostream_t* stream) try {
    Plugin* p = self(plugin);
    if (p->pending) p->compile_code();  // report this computer's compile, not the loaded one
    const std::string data = serialize(p->state);
    size_t written = 0;
    while (written < data.size()) {
        const int64_t n = stream->write(stream, data.data() + written, data.size() - written);
        if (n <= 0) return false;
        written += size_t(n);
    }
    return true;
} catch (...) {
    return false;
}

// Loading (a project, or new code from AutoReaper) always compiles the code;
// if it does not compile, it is kept so that saving does not lose it, and
// nothing runs. A plugin not yet activated (a project opening) compiles once
// it knows the sample rate.
bool state_load(const clap_plugin_t* plugin, const clap_istream_t* stream) try {
    constexpr size_t kMaxState = size_t(64) << 20;
    std::string data;
    char buffer[4096];
    for (;;) {
        const int64_t n = stream->read(stream, buffer, sizeof(buffer));
        if (n < 0) return false;
        if (n == 0) break;
        if (data.size() + size_t(n) > kMaxState) return false;
        data.append(buffer, size_t(n));
    }
    State loaded;
    if (!deserialize(data, loaded)) return false;
    Plugin* p = self(plugin);
    p->state.code = loaded.code;
    if (p->active) {
        p->compile_code();
    } else {
        p->pending = true;
        p->state.status = loaded.status;
        p->state.messages = loaded.messages;
        p->state.inputs = loaded.inputs;
        p->state.outputs = loaded.outputs;
    }
    p->state.draft = same_code(loaded.draft, p->state.code) ? "" : loaded.draft;
    ++p->revision;
    return true;
} catch (...) {
    return false;
}

const clap_plugin_state_t kState = {state_save, state_load};

// ------------------------------------------------------------------ window

void gui_tick(Plugin* p) {
    Gui& gui = *p->gui;
    gui.window->pump();
    const auto now = std::chrono::steady_clock::now();
    const double seconds = std::chrono::duration<double>(now - gui.last).count();
    gui.last = now;
    if (!gui.visible) return;
    UiModel model{p->state.code, p->state.draft, p->state.status, p->state.messages, p->state.inputs,
                  p->state.outputs, p->revision};
    const UiAction action = gui.ui->frame(model, seconds);
    std::string text;
    if (gui.ui->edited(text)) p->set_draft(text);
    if (action == UiAction::Compile) p->compile_draft();
    if (action == UiAction::Revert) p->revert_draft();
    gui.window->present(gui.ui->bitmap());
}

bool gui_is_api_supported(const clap_plugin_t*, const char* api, bool is_floating) {
    return !is_floating && !std::strcmp(api, PlatformWindow::api());
}

bool gui_get_preferred_api(const clap_plugin_t*, const char** api, bool* is_floating) {
    *api = PlatformWindow::api();
    *is_floating = false;
    return true;
}

bool gui_create(const clap_plugin_t* plugin, const char* api, bool is_floating) try {
    Plugin* p = self(plugin);
    if (is_floating || std::strcmp(api, PlatformWindow::api()) || !p->host_timer) return false;
    auto gui = std::make_unique<Gui>();
    PlatformWindow* window = gui->window.get();
    gui->ui = std::make_unique<Ui>(Clipboard{[window] { return window->clipboard_get(); },
                                             [window](const std::string& text) { window->clipboard_set(text); }});
    window->set_ui(gui->ui.get());
    gui->ui->resize(gui->width, gui->height);
    if (!p->host_timer->register_timer(p->host, 33, &gui->timer)) return false;
    p->gui = std::move(gui);
    return true;
} catch (...) {
    return false;
}

void gui_destroy(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    if (!p->gui) return;
    p->host_timer->unregister_timer(p->host, p->gui->timer);
    p->gui->window->set_ui(nullptr);  // no more events to the Ui while the window closes
    p->gui.reset();
}

bool gui_set_scale(const clap_plugin_t*, double) { return false; }

bool gui_get_size(const clap_plugin_t* plugin, uint32_t* width, uint32_t* height) {
    Plugin* p = self(plugin);
    *width = p->gui ? p->gui->width : kDefaultWidth;
    *height = p->gui ? p->gui->height : kDefaultHeight;
    return true;
}

bool gui_can_resize(const clap_plugin_t*) { return true; }

bool gui_get_resize_hints(const clap_plugin_t*, clap_gui_resize_hints_t* hints) {
    hints->can_resize_horizontally = hints->can_resize_vertically = true;
    hints->preserve_aspect_ratio = false;
    hints->aspect_ratio_width = hints->aspect_ratio_height = 1;
    return true;
}

bool gui_adjust_size(const clap_plugin_t*, uint32_t* width, uint32_t* height) {
    if (*width < 420) *width = 420;
    if (*height < 240) *height = 240;
    return true;
}

bool gui_set_size(const clap_plugin_t* plugin, uint32_t width, uint32_t height) {
    Plugin* p = self(plugin);
    if (!p->gui) return false;
    p->gui->width = int(width);
    p->gui->height = int(height);
    p->gui->ui->resize(int(width), int(height));
    p->gui->window->resize(int(width), int(height));
    return true;
}

bool gui_set_parent(const clap_plugin_t* plugin, const clap_window_t* window) {
    Plugin* p = self(plugin);
    if (!p->gui) return false;
#if defined(_WIN32)
    void* parent = window->win32;
#elif defined(__APPLE__)
    void* parent = window->cocoa;
#else
    void* parent = reinterpret_cast<void*>(uintptr_t(window->x11));
#endif
    return p->gui->window->attach(parent, p->gui->width, p->gui->height);
}

bool gui_set_transient(const clap_plugin_t*, const clap_window_t*) { return false; }
void gui_suggest_title(const clap_plugin_t*, const char*) {}

bool gui_show(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    if (!p->gui) return false;
    p->gui->window->show(true);
    p->gui->visible = true;
    return true;
}

bool gui_hide(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    if (!p->gui) return false;
    p->gui->window->show(false);
    p->gui->visible = false;
    return true;
}

const clap_plugin_gui_t kGui = {gui_is_api_supported, gui_get_preferred_api, gui_create, gui_destroy, gui_set_scale,
                                gui_get_size, gui_can_resize, gui_get_resize_hints, gui_adjust_size, gui_set_size,
                                gui_set_parent, gui_set_transient, gui_suggest_title, gui_show, gui_hide};

void timer_tick(const clap_plugin_t* plugin, clap_id timer) {
    Plugin* p = self(plugin);
    if (p->gui && timer == p->gui->timer) gui_tick(p);
}

const clap_plugin_timer_support_t kTimer = {timer_tick};

// ------------------------------------------------------------------ plugin

bool plugin_init(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    p->host_state = static_cast<const clap_host_state_t*>(p->host->get_extension(p->host, CLAP_EXT_STATE));
    p->host_timer = static_cast<const clap_host_timer_support_t*>(p->host->get_extension(p->host, CLAP_EXT_TIMER_SUPPORT));
    p->state.code = kDefaultCode;  // compiled at activation, unless a state comes first
    return true;
}

void plugin_destroy(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    gui_destroy(plugin);
    delete p->current;
    delete p->next.exchange(nullptr);
    p->free_retired();
    delete p;
}

bool plugin_activate(const clap_plugin_t* plugin, double sample_rate, uint32_t, uint32_t max_frames) try {
    Plugin* p = self(plugin);
    p->sample_rate = sample_rate;
    // Faust programs are initialised for one rate.
    if (p->pending || sample_rate != p->compiled_rate) p->compile_code();
    p->spare.assign(std::max<uint32_t>(max_frames, 1), 0.f);
    p->active = true;
    return true;
} catch (...) {
    return false;
}

void plugin_deactivate(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    p->active = false;
    if (Program* pending = p->next.exchange(nullptr)) {
        delete p->current;
        p->current = pending;
    }
    p->free_retired();
}

bool plugin_start_processing(const clap_plugin_t*) { return true; }
void plugin_stop_processing(const clap_plugin_t*) {}
void plugin_reset(const clap_plugin_t* plugin) {
    Program* program = self(plugin)->current;
    if (program && program->instance) reset(*program);
}

clap_process_status plugin_process(const clap_plugin_t* plugin, const clap_process_t* process) {
    Plugin* p = self(plugin);
    // Take a new program only once the previous replaced one was freed.
    if (!p->retired.load(std::memory_order_acquire)) {
        if (Program* fresh = p->next.exchange(nullptr, std::memory_order_acq_rel)) {
            p->retired.store(p->current, std::memory_order_release);
            p->current = fresh;
            p->host->request_callback(p->host);
        }
    }
    const float* inputs[4] = {nullptr, nullptr, nullptr, nullptr};
    int input_count = 0;
    for (uint32_t port = 0; port < process->audio_inputs_count && port < 2; ++port) {
        const clap_audio_buffer_t& buffer = process->audio_inputs[port];
        for (uint32_t channel = 0; channel < 2; ++channel) {
            const uint32_t source = channel < buffer.channel_count ? channel : 0;
            inputs[port * 2 + channel] = buffer.data32 && buffer.channel_count ? buffer.data32[source] : nullptr;
        }
        input_count = int(port * 2 + 2);
    }
    if (process->audio_outputs_count < 1 || process->audio_outputs[0].channel_count < 1 ||
        !process->audio_outputs[0].data32)
        return CLAP_PROCESS_CONTINUE;
    clap_audio_buffer_t& output = process->audio_outputs[0];
    output.constant_mask = 0;
    const int frames = int(process->frames_count);
    if (output.channel_count < 2 && size_t(frames) > p->spare.size()) return CLAP_PROCESS_ERROR;
    // A mono output gets the left channel; the right goes to a spare buffer.
    float* const outputs[2] = {output.data32[0], output.channel_count > 1 ? output.data32[1] : p->spare.data()};
    FlushDenormals flush;

    // Notes and MIDI messages, in time order as CLAP delivers them.
    int midi_count = 0;
    const uint32_t event_count = process->in_events->size(process->in_events);
    for (uint32_t i = 0; i < event_count && midi_count < int(p->midi.size()); ++i) {
        const clap_event_header_t* header = process->in_events->get(process->in_events, i);
        if (header->space_id != CLAP_CORE_EVENT_SPACE_ID) continue;
        MidiMessage& m = p->midi[midi_count];
        m.frame = int(header->time);
        if (header->type == CLAP_EVENT_NOTE_ON || header->type == CLAP_EVENT_NOTE_OFF) {
            const auto* note = reinterpret_cast<const clap_event_note_t*>(header);
            if (note->key < 0 || note->key > 127) continue;
            const int channel = note->channel < 0 ? 0 : note->channel & 15;
            const int velocity = int(note->velocity * 127 + 0.5);
            const bool on = header->type == CLAP_EVENT_NOTE_ON;
            m.status = (unsigned char)((on ? 0x90 : 0x80) | channel);
            m.data1 = (unsigned char)note->key;
            m.data2 = (unsigned char)(on ? std::max(1, std::min(127, velocity)) : velocity);
        } else if (header->type == CLAP_EVENT_MIDI) {
            const auto* midi = reinterpret_cast<const clap_event_midi_t*>(header);
            if (midi->data[0] < 0x80 || midi->data[0] >= 0xF0) continue;
            m.status = midi->data[0];
            m.data1 = midi->data[1];
            m.data2 = midi->data[2];
        } else {
            continue;
        }
        ++midi_count;
    }

    Transport transport;
    if (const clap_event_transport_t* t = process->transport) {
        transport.playing = t->flags & CLAP_TRANSPORT_IS_PLAYING;
        if (t->flags & CLAP_TRANSPORT_HAS_TEMPO) transport.bpm = t->tempo;
        if (t->flags & CLAP_TRANSPORT_HAS_BEATS_TIMELINE) {
            transport.beat = double(t->song_pos_beats) / CLAP_BEATTIME_FACTOR;
            transport.bar = double(t->bar_start) / CLAP_BEATTIME_FACTOR;
        }
        if (t->flags & CLAP_TRANSPORT_HAS_TIME_SIGNATURE) {
            transport.num = t->tsig_num;
            transport.den = t->tsig_denom;
        }
    }

    if (p->current && p->current->instance) {
        run(*p->current, inputs, input_count, outputs, frames, transport, p->midi.data(), midi_count);
    } else {
        for (int side = 0; side < 2; ++side) {
            if (inputs[side] && inputs[side] != outputs[side]) std::memcpy(outputs[side], inputs[side], sizeof(float) * frames);
            else if (!inputs[side]) std::memset(outputs[side], 0, sizeof(float) * frames);
        }
    }
    return CLAP_PROCESS_CONTINUE;
}

const void* plugin_get_extension(const clap_plugin_t*, const char* id) {
    if (!std::strcmp(id, CLAP_EXT_AUDIO_PORTS)) return &kAudioPorts;
    if (!std::strcmp(id, CLAP_EXT_STATE)) return &kState;
    if (!std::strcmp(id, CLAP_EXT_NOTE_PORTS)) return &kNotePorts;
    if (!std::strcmp(id, CLAP_EXT_GUI)) return &kGui;
    if (!std::strcmp(id, CLAP_EXT_TIMER_SUPPORT)) return &kTimer;
    return nullptr;
}

void plugin_on_main_thread(const clap_plugin_t* plugin) { self(plugin)->free_retired(); }

const char* kFeatures[] = {CLAP_PLUGIN_FEATURE_AUDIO_EFFECT, CLAP_PLUGIN_FEATURE_STEREO, nullptr};

const clap_plugin_descriptor_t kDescriptor = {
    CLAP_VERSION_INIT, "com.autoreaper.faust", "Faust", "AutoReaper",
    "https://github.com/onliner10/AutoReaper", "", "", AUTOREAPER_FAUST_VERSION,
    "Runs Faust code compiled in place; the code is stored with the project.", kFeatures};

const clap_plugin_t* create_plugin(const clap_plugin_factory_t*, const clap_host_t* host, const char* id) {
    if (std::strcmp(id, kDescriptor.id)) return nullptr;
    auto* p = new Plugin();
    p->host = host;
    p->clap = {&kDescriptor, p, plugin_init, plugin_destroy, plugin_activate, plugin_deactivate,
               plugin_start_processing, plugin_stop_processing, plugin_reset, plugin_process,
               plugin_get_extension, plugin_on_main_thread};
    return &p->clap;
}

uint32_t factory_count(const clap_plugin_factory_t*) { return 1; }
const clap_plugin_descriptor_t* factory_descriptor(const clap_plugin_factory_t*, uint32_t index) {
    return index == 0 ? &kDescriptor : nullptr;
}

const clap_plugin_factory_t kFactory = {factory_count, factory_descriptor, create_plugin};

// A packaged plugin keeps faust.dll beside it and links it delay-loaded:
// load it from there before the first libfaust call, so it need not be on
// PATH. Elsewhere the loader finds libfaust through the plugin's rpath.
bool entry_init(const char* plugin_path) {
#ifdef _WIN32
    if (GetModuleHandleW(L"faust.dll")) return true;
    std::string folder = plugin_path ? plugin_path : "";
    folder = folder.substr(0, folder.find_last_of("\\/") == std::string::npos ? 0 : folder.find_last_of("\\/"));
    const std::string dll = folder + "\\faust.dll";
    std::wstring wide(MultiByteToWideChar(CP_UTF8, 0, dll.c_str(), -1, nullptr, 0), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, dll.c_str(), -1, wide.data(), int(wide.size()));
    if (!folder.empty() && LoadLibraryExW(wide.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH)) return true;
    return LoadLibraryW(L"faust.dll") != nullptr;  // a Faust install on PATH
#else
    (void)plugin_path;
    return true;
#endif
}
void entry_deinit() {}
const void* entry_factory(const char* id) { return std::strcmp(id, CLAP_PLUGIN_FACTORY_ID) ? nullptr : &kFactory; }

}  // namespace
}  // namespace autoreaper

extern "C" CLAP_EXPORT const clap_plugin_entry_t clap_entry = {CLAP_VERSION_INIT, autoreaper::entry_init,
                                                                autoreaper::entry_deinit, autoreaper::entry_factory};
