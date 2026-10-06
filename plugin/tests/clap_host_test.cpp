// Loads the built plugin the way a host does and checks it: the factory, the
// ports, new code through the state (it compiles at once), ducking keyed by
// the sidechain, a compile error, and the state reporting all of it.
//   clap_host_test "<path to AutoReaper Faust.clap>"
#include <clap/clap.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

static int failures = 0;
#define CHECK(condition)                                                    \
    do {                                                                    \
        if (!(condition)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
            ++failures;                                                     \
        }                                                                   \
    } while (0)

static const clap_plugin_entry_t* load(std::string path) {
#ifdef _WIN32
    HMODULE module = LoadLibraryA(path.c_str());
    if (!module) std::printf("LoadLibrary failed: %lu\n", GetLastError());
    return module ? reinterpret_cast<const clap_plugin_entry_t*>(GetProcAddress(module, "clap_entry")) : nullptr;
#else
#ifdef __APPLE__
    // A bundle: the binary is Contents/MacOS/<name>.
    const std::string name = path.substr(path.find_last_of('/') + 1);
    path += "/Contents/MacOS/" + name.substr(0, name.rfind(".clap"));
#endif
    void* module = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!module) std::printf("dlopen failed: %s\n", dlerror());
    return module ? static_cast<const clap_plugin_entry_t*>(dlsym(module, "clap_entry")) : nullptr;
#endif
}

struct Buffer {
    std::string data;
    size_t at = 0;
};

static int64_t write_stream(const clap_ostream_t* stream, const void* bytes, uint64_t size) {
    static_cast<Buffer*>(stream->ctx)->data.append(static_cast<const char*>(bytes), size_t(size));
    return int64_t(size);
}

static int64_t read_stream(const clap_istream_t* stream, void* bytes, uint64_t size) {
    Buffer& buffer = *static_cast<Buffer*>(stream->ctx);
    const size_t count = std::min<size_t>(size_t(size), buffer.data.size() - buffer.at);
    std::memcpy(bytes, buffer.data.data() + buffer.at, count);
    buffer.at += count;
    return int64_t(count);
}

static std::string save(const clap_plugin_t* plugin, const clap_plugin_state_t* state) {
    Buffer buffer;
    clap_ostream_t stream{&buffer, write_stream};
    CHECK(state->save(plugin, &stream));
    return buffer.data;
}

static bool load_code(const clap_plugin_t* plugin, const clap_plugin_state_t* state, const std::string& code) {
    Buffer buffer;
    buffer.data = "AutoReaperFaust 1\nstatus none\ninputs 0\noutputs 0\nmessages 0\n\ncode " +
                  std::to_string(code.size()) + "\n" + code + "\n";
    clap_istream_t stream{&buffer, read_stream};
    return state->load(plugin, &stream);
}

static uint32_t no_events(const clap_input_events_t*) { return 0; }
static const clap_event_header_t* no_event(const clap_input_events_t*, uint32_t) { return nullptr; }

int main(int argc, char** argv) {
    if (argc < 2) {
        std::printf("usage: clap_host_test <plugin.clap>\n");
        return 2;
    }
    const clap_plugin_entry_t* entry = load(argv[1]);
    CHECK(entry != nullptr);
    if (!entry) return 1;
    CHECK(entry->init(argv[1]));
    auto* factory = static_cast<const clap_plugin_factory_t*>(entry->get_factory(CLAP_PLUGIN_FACTORY_ID));
    CHECK(factory && factory->get_plugin_count(factory) == 1);
    const clap_plugin_descriptor_t* descriptor = factory->get_plugin_descriptor(factory, 0);
    CHECK(!std::strcmp(descriptor->id, "com.autoreaper.faust"));

    clap_host_t host{CLAP_VERSION_INIT, nullptr, "clap_host_test", "AutoReaper", "", "1",
                     [](const clap_host_t*, const char*) -> const void* { return nullptr; },
                     [](const clap_host_t*) {}, [](const clap_host_t*) {}, [](const clap_host_t*) {}};
    const clap_plugin_t* plugin = factory->create_plugin(factory, &host, descriptor->id);
    CHECK(plugin && plugin->init(plugin));

    auto* ports = static_cast<const clap_plugin_audio_ports_t*>(plugin->get_extension(plugin, CLAP_EXT_AUDIO_PORTS));
    auto* notes = static_cast<const clap_plugin_note_ports_t*>(plugin->get_extension(plugin, CLAP_EXT_NOTE_PORTS));
    auto* state = static_cast<const clap_plugin_state_t*>(plugin->get_extension(plugin, CLAP_EXT_STATE));
    CHECK(ports && ports->count(plugin, true) == 2 && ports->count(plugin, false) == 1);
    CHECK(notes && notes->count(plugin, true) == 1);
    CHECK(state && plugin->get_extension(plugin, CLAP_EXT_GUI) != nullptr);

    const std::string ducker =
        "import(\"stdfaust.lib\");\n"
        "process(l, r, kl, kr) = l * g, r * g with { g = ba.db2linear(-12 * (abs(kl) > 0.1)); };\n";
    CHECK(load_code(plugin, state, ducker));
    std::string saved = save(plugin, state);
    std::printf("state after loading the ducker:\n%s\n", saved.c_str());
    CHECK(saved.find("status ok") != std::string::npos && saved.find(ducker) != std::string::npos);
    CHECK(saved.find("faust 2.") != std::string::npos);

    CHECK(plugin->activate(plugin, 48000, 1, 512));
    CHECK(plugin->start_processing(plugin));
    std::vector<float> l(512, 0.5f), r(512, 0.5f), kl(512, 0.f), kr(512, 0.f), ol(512), orr(512);
    for (int i = 256; i < 512; ++i) kl[i] = kr[i] = 1.f;
    float* main_in[2] = {l.data(), r.data()};
    float* key_in[2] = {kl.data(), kr.data()};
    float* out[2] = {ol.data(), orr.data()};
    clap_audio_buffer_t inputs[2] = {{main_in, nullptr, 2, 0, 0}, {key_in, nullptr, 2, 0, 0}};
    clap_audio_buffer_t outputs[1] = {{out, nullptr, 2, 0, 0}};
    clap_input_events_t in_events{nullptr, no_events, no_event};
    clap_output_events_t out_events{nullptr, [](const clap_output_events_t*, const clap_event_header_t*) { return true; }};
    clap_process_t process{0, 512, nullptr, inputs, outputs, 2, 1, &in_events, &out_events};
    CHECK(plugin->process(plugin, &process) == CLAP_PROCESS_CONTINUE);
    std::printf("output before the key: %.4f, with the key: %.4f\n", ol[100], ol[400]);
    CHECK(std::fabs(ol[100] - 0.5f) < 1e-6f);
    CHECK(std::fabs(ol[400] - 0.5f * std::pow(10.f, -12.f / 20)) < 1e-4f);
    plugin->stop_processing(plugin);
    plugin->deactivate(plugin);

    CHECK(load_code(plugin, state, "process = _ : nosuch;"));
    saved = save(plugin, state);
    CHECK(saved.find("status error") != std::string::npos && saved.find("nosuch") != std::string::npos);

    plugin->destroy(plugin);
    entry->deinit();
    std::printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
