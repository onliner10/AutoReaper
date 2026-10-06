// Faust code compiled in place with libfaust (LLVM), and the plugin state.
#pragma once

#include <memory>
#include <string>
#include <vector>

class dsp;
class llvm_dsp_factory;

namespace autoreaper {

// A compiled program: factory, one instance, and scratch buffers so compute()
// can read silence for missing inputs and write outputs nobody reads.
struct Program {
    ~Program();
    llvm_dsp_factory* factory = nullptr;
    dsp* instance = nullptr;
    int inputs = 0;
    int outputs = 0;
    int capacity = 0;                       // frames per compute() call
    std::vector<std::vector<float>> scratch;  // one per input and output
    std::vector<float*> input_pointers;
    std::vector<float*> output_pointers;
};

struct CompileResult {
    std::unique_ptr<Program> program;  // null on error
    std::string messages;              // Faust's errors (or warnings)
};

// Faust library folders: AUTOREAPER_FAUST_LIBRARIES, the folder beside the
// plugin, then the usual install locations.
std::vector<std::string> library_paths(const std::string& plugin_dir);

CompileResult compile(const std::string& code, double sample_rate, const std::vector<std::string>& libraries);

// Run the program on main stereo + sidechain stereo into stereo out. Faust
// inputs take the channels in that order; one output feeds both sides.
void process(Program& program, const float* const* inputs, int input_count, float* const* outputs, int frames);

// Clear delay lines and envelopes (transport jumps).
void reset(Program& program);

// What the project stores: the running code, unfinished edits, and the result
// of the last compile (informative: loading always compiles the code again).
struct State {
    std::string code;              // last code that compiled
    std::string draft;             // edited in the plugin window, not compiled
    std::string status = "none";  // ok | error | none
    std::string messages;
    int inputs = 0;
    int outputs = 0;
};

std::string serialize(const State& state);
bool deserialize(const std::string& data, State& state);

}  // namespace autoreaper
