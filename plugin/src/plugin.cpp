// AutoReaper Faust: a CLAP effect that runs Faust code compiled in place. The
// code is the plugin's state, so the host stores it in the project like any
// plugin setting. Audio: main stereo + sidechain stereo in, stereo out. The
// window is a code editor with a Compile button.
#include "engine.h"
#include "gui/ui.h"
#ifdef __linux__
#include "gui/x11_window.h"
#endif

#include <clap/clap.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>

#ifndef _WIN32
#include <dlfcn.h>
#endif

namespace autoreaper {
namespace {

const char* kDefaultCode = "import(\"stdfaust.lib\");\n\n// The effect: inputs are main L, R, sidechain L, R.\nprocess = _, _;\n";
constexpr int kDefaultWidth = 760, kDefaultHeight = 480;

std::string plugin_dir() {
#ifndef _WIN32
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

#ifdef __linux__
struct Gui {
    std::unique_ptr<X11Window> window = std::make_unique<X11Window>();
    std::unique_ptr<Ui> ui;
    int width = kDefaultWidth, height = kDefaultHeight;
    clap_id timer = CLAP_INVALID_ID;
    std::chrono::steady_clock::time_point last = std::chrono::steady_clock::now();
};
#endif

struct Plugin {
    clap_plugin_t clap;
    const clap_host_t* host;
    const clap_host_state_t* host_state = nullptr;
    const clap_host_timer_support_t* host_timer = nullptr;
    double sample_rate = 48000;
    bool active = false;

    State state;
    int revision = 0;  // bumped when code or draft change outside the window
#ifdef __linux__
    std::unique_ptr<Gui> gui;
#endif

    // The audio thread owns `current`. The main thread hands a new program over
    // in `next` and frees the replaced one from `retired`.
    Program* current = nullptr;
    std::atomic<Program*> next{nullptr};
    std::atomic<Program*> retired{nullptr};

    // Compile on the main thread. On success the text becomes the running
    // code; on error the running program and code stay, with Faust's messages.
    bool try_compile(const std::string& text) {
        auto result = compile(text, sample_rate, library_paths(plugin_dir()));
        state.messages = result.messages;
        if (!result.program) {
            state.status = "error";
            return false;
        }
        state.status = "ok";
        state.code = text;
        state.inputs = result.program->inputs;
        state.outputs = result.program->outputs;
        install(result.program.release());
        return true;
    }

    // Compile what the window shows; a success clears the draft.
    void compile_draft() {
        const std::string text = state.draft.empty() ? state.code : state.draft;
        if (try_compile(text)) state.draft.clear();
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

// ------------------------------------------------------------------ state

bool state_save(const clap_plugin_t* plugin, const clap_ostream_t* stream) {
    const std::string data = serialize(self(plugin)->state);
    size_t written = 0;
    while (written < data.size()) {
        const int64_t n = stream->write(stream, data.data() + written, data.size() - written);
        if (n <= 0) return false;
        written += size_t(n);
    }
    return true;
}

// Loading (a project, or new code from AutoReaper) always compiles the code;
// if it does not compile, it is kept so that saving does not lose it.
bool state_load(const clap_plugin_t* plugin, const clap_istream_t* stream) {
    std::string data;
    char buffer[4096];
    for (;;) {
        const int64_t n = stream->read(stream, buffer, sizeof(buffer));
        if (n < 0) return false;
        if (n == 0) break;
        data.append(buffer, size_t(n));
    }
    State loaded;
    if (!deserialize(data, loaded)) return false;
    Plugin* p = self(plugin);
    if (!p->try_compile(loaded.code)) p->state.code = loaded.code;
    p->state.draft = same_code(loaded.draft, p->state.code) ? "" : loaded.draft;
    ++p->revision;
    return true;
}

const clap_plugin_state_t kState = {state_save, state_load};

// ------------------------------------------------------------------ window

#ifdef __linux__
void gui_tick(Plugin* p) {
    Gui& gui = *p->gui;
    gui.window->pump(*gui.ui);
    const auto now = std::chrono::steady_clock::now();
    const double seconds = std::chrono::duration<double>(now - gui.last).count();
    gui.last = now;
    UiModel model{p->state.code, p->state.draft, p->state.status, p->state.messages, p->state.inputs,
                  p->state.outputs, p->revision};
    const UiAction action = gui.ui->frame(model, seconds);
    std::string text;
    if (gui.ui->edited(text)) {
        const bool was_draft = !p->state.draft.empty();
        p->state.draft = same_code(text, p->state.code) ? "" : text;
        if (!was_draft && !p->state.draft.empty()) p->mark_dirty();
    }
    if (action == UiAction::Compile) p->compile_draft();
    gui.window->present(gui.ui->bitmap());
}

bool gui_is_api_supported(const clap_plugin_t*, const char* api, bool is_floating) {
    return !is_floating && !std::strcmp(api, CLAP_WINDOW_API_X11);
}

bool gui_get_preferred_api(const clap_plugin_t*, const char** api, bool* is_floating) {
    *api = CLAP_WINDOW_API_X11;
    *is_floating = false;
    return true;
}

bool gui_create(const clap_plugin_t* plugin, const char* api, bool is_floating) {
    Plugin* p = self(plugin);
    if (is_floating || std::strcmp(api, CLAP_WINDOW_API_X11) || !p->host_timer) return false;
    auto gui = std::make_unique<Gui>();
    X11Window* window = gui->window.get();
    gui->ui = std::make_unique<Ui>(Clipboard{[window] { return window->clipboard_get(); },
                                             [window](const std::string& text) { window->clipboard_set(text); }});
    gui->ui->resize(gui->width, gui->height);
    if (!p->host_timer->register_timer(p->host, 33, &gui->timer)) return false;
    p->gui = std::move(gui);
    return true;
}

void gui_destroy(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    if (!p->gui) return;
    p->host_timer->unregister_timer(p->host, p->gui->timer);
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
    return p->gui && p->gui->window->attach(window->x11, p->gui->width, p->gui->height);
}

bool gui_set_transient(const clap_plugin_t*, const clap_window_t*) { return false; }
void gui_suggest_title(const clap_plugin_t*, const char*) {}

bool gui_show(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    if (!p->gui) return false;
    p->gui->window->show(true);
    return true;
}

bool gui_hide(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    if (!p->gui) return false;
    p->gui->window->show(false);
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
#endif

// ------------------------------------------------------------------ plugin

bool plugin_init(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    p->host_state = static_cast<const clap_host_state_t*>(p->host->get_extension(p->host, CLAP_EXT_STATE));
    p->host_timer = static_cast<const clap_host_timer_support_t*>(p->host->get_extension(p->host, CLAP_EXT_TIMER_SUPPORT));
    p->try_compile(kDefaultCode);
    return true;
}

void plugin_destroy(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
#ifdef __linux__
    gui_destroy(plugin);
#endif
    delete p->current;
    delete p->next.exchange(nullptr);
    p->free_retired();
    delete p;
}

bool plugin_activate(const clap_plugin_t* plugin, double sample_rate, uint32_t, uint32_t) {
    Plugin* p = self(plugin);
    if (sample_rate != p->sample_rate) {
        p->sample_rate = sample_rate;
        p->try_compile(p->state.code);  // Faust programs are initialised for one rate
    }
    p->active = true;
    return true;
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
    if (Program* program = self(plugin)->current) reset(*program);
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
    if (process->audio_outputs_count < 1 || process->audio_outputs[0].channel_count < 2) return CLAP_PROCESS_CONTINUE;
    float* const* outputs = process->audio_outputs[0].data32;
    const int frames = int(process->frames_count);
    if (p->current) {
        autoreaper::process(*p->current, inputs, input_count, outputs, frames);
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
#ifdef __linux__
    if (!std::strcmp(id, CLAP_EXT_GUI)) return &kGui;
    if (!std::strcmp(id, CLAP_EXT_TIMER_SUPPORT)) return &kTimer;
#endif
    return nullptr;
}

void plugin_on_main_thread(const clap_plugin_t* plugin) { self(plugin)->free_retired(); }

const char* kFeatures[] = {CLAP_PLUGIN_FEATURE_AUDIO_EFFECT, CLAP_PLUGIN_FEATURE_STEREO, nullptr};

const clap_plugin_descriptor_t kDescriptor = {
    CLAP_VERSION_INIT, "com.autoreaper.faust", "Faust (AutoReaper)", "AutoReaper",
    "https://github.com/onliner10/AutoReaper", "", "", "0.1.0",
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

bool entry_init(const char*) { return true; }
void entry_deinit() {}
const void* entry_factory(const char* id) { return std::strcmp(id, CLAP_PLUGIN_FACTORY_ID) ? nullptr : &kFactory; }

}  // namespace
}  // namespace autoreaper

extern "C" CLAP_EXPORT const clap_plugin_entry_t clap_entry = {CLAP_VERSION_INIT, autoreaper::entry_init,
                                                                autoreaper::entry_deinit, autoreaper::entry_factory};
