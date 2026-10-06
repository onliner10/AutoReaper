// Faust code compiled in place with libfaust (LLVM), and the plugin state.
#pragma once

#include <memory>
#include <string>
#include <vector>

class dsp;
class llvm_dsp_factory;
class midi_handler;
class MidiUI;

namespace autoreaper {

// Controls the host sets from its transport, by metadata: [host:bpm] tempo,
// [host:beat] position in quarter notes, [host:bar] position where the current
// bar starts, [host:playing] 1 or 0, [host:num] and [host:den] time signature.
struct HostZones {
    std::vector<float*> bpm, beat, bar, playing, num, den;
    bool empty() const {
        return bpm.empty() && beat.empty() && bar.empty() && playing.empty() && num.empty() && den.empty();
    }
};

// A compiled program: factory, one instance, its MIDI and host controls, and
// scratch buffers so compute() can read silence for missing inputs and write
// outputs nobody reads.
struct Program {
    ~Program();
    llvm_dsp_factory* factory = nullptr;
    dsp* instance = nullptr;
    std::unique_ptr<midi_handler> midi;  // feeds the [midi:...] controls
    std::unique_ptr<MidiUI> midi_ui;
    HostZones host;
    double sample_rate = 48000;
    int inputs = 0;
    int outputs = 0;
    int capacity = 0;                         // frames per compute() call
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

// The host's transport at the first frame of a block.
struct Transport {
    bool playing = false;
    double bpm = 120;
    double beat = 0;  // quarter notes from the project start
    double bar = 0;   // quarter notes from the project start to the current bar
    int num = 4, den = 4;
};

// A MIDI message at a frame of the block: status, data1, data2.
struct MidiMessage {
    int frame = 0;
    unsigned char status = 0, data1 = 0, data2 = 0;
};

// Run one block: main stereo + sidechain stereo in (Faust inputs take them in
// that order), stereo out (one output feeds both). The block is split at each
// MIDI message so a [midi:...] control changes on its frame, and, when the
// program has [host:...] controls, at every 1/48 of a quarter note while
// playing (and at least every 32 frames), so a beat count changes on the
// frame where the beat starts.
void run(Program& program, const float* const* inputs, int input_count, float* const* outputs, int frames,
         const Transport& transport, const MidiMessage* midi, int midi_count);

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
