#include "engine.h"

#include <faust/dsp/llvm-dsp.h>
#include <faust/gui/MidiUI.h>
#include <faust/gui/UI.h>
#include <faust/midi/midi.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <sstream>

// Faust's GUI keeps a list of every GUI (MidiUI is one); defined once here.
std::list<GUI*> GUI::fGuiList;
ztimedmap GUI::gTimedZoneMap;

namespace autoreaper {

namespace {

constexpr int kCapacity = 1024;
constexpr int kGrid = 48;         // host controls change on this fraction of a quarter note
constexpr int kHostStep = 32;     // and at least this often (frames)

// Collects the controls marked [host:...]; unknown names become messages.
struct HostZonesUI : public UI {
    HostZones& zones;
    std::string& messages;
    HostZonesUI(HostZones& z, std::string& m) : zones(z), messages(m) {}
    void declare(FAUSTFLOAT* zone, const char* key, const char* value) override {
        if (!zone || std::strcmp(key, "host")) return;
        const std::string name = value;
        if (name == "bpm") zones.bpm.push_back(zone);
        else if (name == "beat") zones.beat.push_back(zone);
        else if (name == "bar") zones.bar.push_back(zone);
        else if (name == "playing") zones.playing.push_back(zone);
        else if (name == "num") zones.num.push_back(zone);
        else if (name == "den") zones.den.push_back(zone);
        else messages += "AutoReaper: unknown [host:" + name + "]; use bpm, beat, bar, playing, num or den.\n";
    }
    void openTabBox(const char*) override {}
    void openHorizontalBox(const char*) override {}
    void openVerticalBox(const char*) override {}
    void closeBox() override {}
    void addButton(const char*, FAUSTFLOAT*) override {}
    void addCheckButton(const char*, FAUSTFLOAT*) override {}
    void addVerticalSlider(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT) override {}
    void addHorizontalSlider(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT) override {}
    void addNumEntry(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT, FAUSTFLOAT) override {}
    void addHorizontalBargraph(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT) override {}
    void addVerticalBargraph(const char*, FAUSTFLOAT*, FAUSTFLOAT, FAUSTFLOAT) override {}
    void addSoundfile(const char*, const char*, Soundfile**) override {}
};

void set_all(const std::vector<float*>& zones, double value) {
    for (float* zone : zones) *zone = float(value);
}

bool is_dir(const std::string& path) {
    std::error_code error;
    return std::filesystem::is_directory(std::filesystem::u8path(path), error);
}

}  // namespace

Program::~Program() {
    midi_ui.reset();  // its zones live in the instance
    midi.reset();
    delete instance;  // before its factory
    if (factory) deleteDSPFactory(factory);
}

std::vector<std::string> library_paths(const std::string& plugin_dir) {
    std::vector<std::string> candidates;
    if (const char* explicit_path = std::getenv("AUTOREAPER_FAUST_LIBRARIES")) candidates.push_back(explicit_path);
    if (!plugin_dir.empty()) {
        candidates.push_back(plugin_dir + "/faustlibraries");
        candidates.push_back(plugin_dir + "/../Resources/faustlibraries");  // inside a macOS bundle
    }
    for (const char* path : {"/usr/share/faust", "/usr/local/share/faust", "/opt/homebrew/share/faust",
                             "C:/Program Files/Faust/share/faust", "C:/Program Files (x86)/Faust/share/faust"})
        candidates.push_back(path);
    std::vector<std::string> found;
    for (const auto& path : candidates)
        if (is_dir(path) && std::find(found.begin(), found.end(), path) == found.end()) found.push_back(path);
    return found;
}

CompileResult compile(const std::string& code, double sample_rate, const std::vector<std::string>& libraries) {
    CompileResult result;
    std::vector<const char*> argv;
    for (const auto& path : libraries) {
        argv.push_back("-I");
        argv.push_back(path.c_str());
    }
    std::string error;
    llvm_dsp_factory* factory = createDSPFactoryFromString("faust", code, int(argv.size()), argv.data(), "", error, -1);
    if (!factory) {
        result.messages = error.empty() ? "Faust could not compile the code." : error;
        return result;
    }
    auto program = std::make_unique<Program>();
    program->factory = factory;
    program->instance = factory->createDSPInstance();
    if (!program->instance) {
        result.messages = "Faust compiled the code but could not create an instance.";
        return result;
    }
    program->instance->init(int(sample_rate));
    program->sample_rate = sample_rate;
    program->midi = std::make_unique<midi_handler>();
    program->midi_ui = std::make_unique<MidiUI>(program->midi.get());
    program->instance->buildUserInterface(program->midi_ui.get());
    std::string host_messages;
    HostZonesUI host(program->host, host_messages);
    program->instance->buildUserInterface(&host);
    program->inputs = program->instance->getNumInputs();
    program->outputs = program->instance->getNumOutputs();
    program->capacity = kCapacity;
    program->scratch.assign(program->inputs + program->outputs, std::vector<float>(kCapacity, 0.f));
    program->input_pointers.resize(program->inputs);
    program->output_pointers.resize(program->outputs);
    for (int i = 0; i < program->outputs; ++i) program->output_pointers[i] = program->scratch[program->inputs + i].data();
    result.messages = error + host_messages;  // warnings, if any
    result.program = std::move(program);
    return result;
}

void process(Program& program, const float* const* inputs, int input_count, float* const* outputs, int frames) {
    for (int offset = 0; offset < frames; offset += program.capacity) {
        const int count = std::min(program.capacity, frames - offset);
        for (int i = 0; i < program.inputs; ++i) {
            float* buffer = program.scratch[i].data();
            if (i < input_count && inputs[i]) std::memcpy(buffer, inputs[i] + offset, sizeof(float) * count);
            else std::memset(buffer, 0, sizeof(float) * count);
            program.input_pointers[i] = buffer;
        }
        program.instance->compute(count, program.input_pointers.data(), program.output_pointers.data());
        for (int side = 0; side < 2; ++side) {
            float* out = outputs[side] + offset;
            if (program.outputs == 0) std::memset(out, 0, sizeof(float) * count);
            else std::memcpy(out, program.output_pointers[std::min(side, program.outputs - 1)], sizeof(float) * count);
        }
    }
}

void run(Program& program, const float* const* inputs, int input_count, float* const* outputs, int frames,
         const Transport& transport, const MidiMessage* midi, int midi_count) {
    const bool host = !program.host.empty();
    const double frames_per_beat = program.sample_rate * 60.0 / std::max(1.0, transport.bpm);
    const double bar_length = 4.0 * std::max(1, transport.num) / std::max(1, transport.den);
    int at = 0, next_midi = 0;
    while (at < frames) {
        for (; next_midi < midi_count && midi[next_midi].frame <= at; ++next_midi) {
            const MidiMessage& m = midi[next_midi];
            if ((m.status & 0xF0) == 0xC0 || (m.status & 0xF0) == 0xD0)
                program.midi->handleData1(0, m.status & 0xF0, m.status & 0x0F, m.data1);
            else
                program.midi->handleData2(0, m.status & 0xF0, m.status & 0x0F, m.data1, m.data2);
        }
        int end = frames;
        if (next_midi < midi_count) end = std::min(end, midi[next_midi].frame);
        if (host) {
            double beat = transport.beat + (transport.playing ? at / frames_per_beat : 0.0);
            const double grid = std::round(beat * kGrid);
            if (std::fabs(beat * kGrid - grid) < 1e-6) beat = grid / kGrid;  // exactly on the grid
            double bar = transport.bar;
            if (transport.playing && beat >= bar + bar_length) bar += std::floor((beat - bar) / bar_length) * bar_length;
            set_all(program.host.beat, beat);
            set_all(program.host.bar, bar);
            set_all(program.host.bpm, transport.bpm);
            set_all(program.host.playing, transport.playing ? 1 : 0);
            set_all(program.host.num, transport.num);
            set_all(program.host.den, transport.den);
            end = std::min(end, at + kHostStep);
            if (transport.playing) {
                const double next_line = (std::floor(beat * kGrid + 1e-6) + 1) / kGrid;
                end = std::min(end, int(std::ceil((next_line - transport.beat) * frames_per_beat - 1e-6)));
            }
        }
        end = std::max(end, at + 1);
        const float* in[4] = {nullptr, nullptr, nullptr, nullptr};
        for (int i = 0; i < input_count && i < 4; ++i) in[i] = inputs[i] ? inputs[i] + at : nullptr;
        float* out[2] = {outputs[0] + at, outputs[1] + at};
        process(program, in, input_count, out, end - at);
        at = end;
    }
}

void reset(Program& program) { program.instance->instanceClear(); }

std::string faust_version() { return getCLibFaustVersion(); }

// Text with sized fields, so code and messages need no escaping:
//   AutoReaperFaust 1 / faust 2.70.3 / status ok / inputs 4 / outputs 2 / messages <n>\n<n bytes> / code <n>\n<n bytes>
//   / draft <n>\n<n bytes> (only while there is one)
std::string serialize(const State& state) {
    std::ostringstream out;
    out << "AutoReaperFaust 1\n"
        << "faust " << faust_version() << "\n"
        << "status " << state.status << "\n"
        << "inputs " << state.inputs << "\n"
        << "outputs " << state.outputs << "\n"
        << "messages " << state.messages.size() << "\n" << state.messages << "\n"
        << "code " << state.code.size() << "\n" << state.code << "\n";
    if (!state.draft.empty()) out << "draft " << state.draft.size() << "\n" << state.draft << "\n";
    return out.str();
}

bool deserialize(const std::string& data, State& state) {
    if (data.rfind("AutoReaperFaust 1\n", 0) != 0) return false;
    State parsed;
    size_t at = data.find('\n') + 1;
    while (at < data.size()) {
        const size_t end = data.find('\n', at);
        if (end == std::string::npos) break;
        const std::string line = data.substr(at, end - at);
        at = end + 1;
        const size_t space = line.find(' ');
        if (space == std::string::npos) continue;
        const std::string key = line.substr(0, space), value = line.substr(space + 1);
        if (key == "messages" || key == "code" || key == "draft") {
            const size_t size = std::strtoull(value.c_str(), nullptr, 10);
            if (at + size > data.size()) return false;
            (key == "code" ? parsed.code : key == "draft" ? parsed.draft : parsed.messages) = data.substr(at, size);
            at += size + 1;
        } else if (key == "status") {
            parsed.status = value;
        } else if (key == "inputs") {
            parsed.inputs = std::atoi(value.c_str());
        } else if (key == "outputs") {
            parsed.outputs = std::atoi(value.c_str());
        }
    }
    state = parsed;
    return true;
}

}  // namespace autoreaper
