#include "engine.h"

#include <faust/dsp/llvm-dsp.h>

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <sys/stat.h>

namespace autoreaper {

namespace {

constexpr int kCapacity = 1024;

bool is_dir(const std::string& path) {
    struct stat info;
    return stat(path.c_str(), &info) == 0 && (info.st_mode & S_IFDIR);
}

}  // namespace

Program::~Program() {
    delete instance;  // before its factory
    if (factory) deleteDSPFactory(factory);
}

std::vector<std::string> library_paths(const std::string& plugin_dir) {
    std::vector<std::string> candidates;
    if (const char* explicit_path = std::getenv("AUTOREAPER_FAUST_LIBRARIES")) candidates.push_back(explicit_path);
    if (!plugin_dir.empty()) candidates.push_back(plugin_dir + "/faustlibraries");
    for (const char* path : {"/usr/share/faust", "/usr/local/share/faust", "/opt/homebrew/share/faust",
                             "C:/Program Files/Faust/share/faust"})
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
    program->inputs = program->instance->getNumInputs();
    program->outputs = program->instance->getNumOutputs();
    program->capacity = kCapacity;
    program->scratch.assign(program->inputs + program->outputs, std::vector<float>(kCapacity, 0.f));
    program->input_pointers.resize(program->inputs);
    program->output_pointers.resize(program->outputs);
    for (int i = 0; i < program->outputs; ++i) program->output_pointers[i] = program->scratch[program->inputs + i].data();
    result.messages = error;  // warnings, if any
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

void reset(Program& program) { program.instance->instanceClear(); }

// Text with sized fields, so code and messages need no escaping:
//   AutoReaperFaust 1 / status ok / inputs 4 / outputs 2 / messages <n>\n<n bytes> / code <n>\n<n bytes>
//   / draft <n>\n<n bytes> (only while there is one)
std::string serialize(const State& state) {
    std::ostringstream out;
    out << "AutoReaperFaust 1\n"
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
