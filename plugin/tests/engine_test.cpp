// Headless checks: compile, process (ducking keyed by the sidechain), errors, state.
#define _USE_MATH_DEFINES  // M_PI on Windows
#include "../src/engine.h"

#include <faust/dsp/llvm-dsp.h>

#include <cmath>
#include <cstdio>
#include <vector>

using namespace autoreaper;

static int failures = 0;
#define CHECK(condition)                                                    \
    do {                                                                    \
        if (!(condition)) {                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
            ++failures;                                                     \
        }                                                                   \
    } while (0)

static const char* kDucker = R"(import("stdfaust.lib");
// Duck the main input by 12 dB while the sidechain is loud.
key(kl, kr) = (abs(kl) + abs(kr)) / 2 : an.amp_follower_ar(0.001, 0.1);
gain(kl, kr) = ba.db2linear(-12 * (key(kl, kr) > 0.1)) : si.smoo;
process(l, r, kl, kr) = l * g, r * g with { g = gain(kl, kr); };
)";

static float rms(const std::vector<float>& x, int from, int to) {
    double sum = 0;
    for (int i = from; i < to; ++i) sum += x[i] * x[i];
    return float(std::sqrt(sum / (to - from)));
}

int main() {
    std::printf("libfaust %s, host target %s, compiling for %s\n", faust_version().c_str(),
                getDSPMachineTarget().c_str(), jit_target().c_str());
    std::fflush(stdout);
    const auto libraries = library_paths("");
    CHECK(!libraries.empty());

    auto result = compile(kDucker, 48000, libraries);
    CHECK(result.program != nullptr);
    if (!result.program) {
        std::printf("%s\n", result.messages.c_str());
        return 1;
    }
    CHECK(result.program->inputs == 4 && result.program->outputs == 2);

    // One second: sine on the main input, sidechain loud in the second half.
    const int frames = 48000;
    std::vector<float> l(frames), r(frames), kl(frames, 0.f), kr(frames, 0.f), ol(frames), orr(frames);
    for (int i = 0; i < frames; ++i) {
        l[i] = r[i] = 0.5f * std::sin(2 * M_PI * 440 * i / 48000.0);
        if (i >= frames / 2) kl[i] = kr[i] = 0.8f;
    }
    const float* inputs[4] = {l.data(), r.data(), kl.data(), kr.data()};
    float* outputs[2] = {ol.data(), orr.data()};
    for (int at = 0; at < frames; at += 512) {  // host-sized blocks
        const float* in[4] = {inputs[0] + at, inputs[1] + at, inputs[2] + at, inputs[3] + at};
        float* out[2] = {outputs[0] + at, outputs[1] + at};
        run(*result.program, in, 4, out, 512 < frames - at ? 512 : frames - at, Transport{}, nullptr, 0);
    }
    const float before = rms(ol, 4800, 24000), after = rms(ol, 33600, 48000);
    const float drop = 20 * std::log10(after / before);
    std::printf("ducking: %.2f dB\n", drop);
    CHECK(drop < -11 && drop > -13);

    // Missing sidechain input: reads silence, no ducking.
    reset(*result.program);
    run(*result.program, inputs, 2, outputs, 4800, Transport{}, nullptr, 0);
    CHECK(rms(ol, 2400, 4800) > 0.3f);

    // MIDI: a note 36 halfway through a block mutes from exactly that frame.
    auto gate = compile("process(l, r, kl, kr) = l * (1 - t), r * (1 - t) with { t = button(\"kick [midi:key 36]\") > 0; };",
                        48000, libraries);
    CHECK(gate.program != nullptr);
    if (gate.program) {
        std::vector<float> ones(512, 1.f), a(512), b(512);
        const float* in[4] = {ones.data(), ones.data(), nullptr, nullptr};
        float* out[2] = {a.data(), b.data()};
        const MidiMessage note_on[] = {{300, 0x90, 36, 100}, {301, 0x90, 40, 100}};
        run(*gate.program, in, 4, out, 512, Transport{}, note_on, 2);
        CHECK(a[299] == 1.f && a[300] == 0.f && a[511] == 0.f);
        const MidiMessage note_off[] = {{100, 0x80, 36, 0}};
        run(*gate.program, in, 4, out, 512, Transport{}, note_off, 1);
        CHECK(a[99] == 0.f && a[100] == 1.f);
        const MidiMessage other[] = {{0, 0x90, 37, 100}};
        run(*gate.program, in, 4, out, 512, Transport{}, other, 1);
        CHECK(a[511] == 1.f);  // other notes do nothing
    }

    // Host sync: an impulse on the frame where each quarter note starts.
    auto pulse = compile(
        "beat = nentry(\"beat [host:beat]\", 0, 0, 1e9, 0.0001);\n"
        "playing = nentry(\"playing [host:playing]\", 0, 0, 1, 1);\n"
        "process(l, r, kl, kr) = t, t with { q = floor(beat); t = (q != q') * playing; };",
        48000, libraries);
    CHECK(pulse.program != nullptr && pulse.messages.empty());
    if (pulse.program) {
        Transport transport;
        transport.playing = true;
        transport.bpm = 120;   // 24000 frames per quarter note at 48 kHz
        transport.beat = 0.5;  // start halfway into the first beat
        std::vector<int> pulses;
        std::vector<float> a(512), b(512);
        float* out[2] = {a.data(), b.data()};
        const float* in[4] = {nullptr, nullptr, nullptr, nullptr};
        for (int block = 0; block < 300; ++block) {
            run(*pulse.program, in, 4, out, 512, transport, nullptr, 0);
            for (int i = 0; i < 512; ++i)
                if (a[i] > 0.5f) pulses.push_back(block * 512 + i);
            transport.beat += 512 / 24000.0;
        }
        std::printf("quarter-note pulses at frames:");
        for (int frame : pulses) std::printf(" %d", frame);
        std::printf("\n");
        // Beats 1..6 start at 12000 + k * 24000 frames.
        CHECK(pulses.size() == 6);
        for (size_t k = 0; k < pulses.size(); ++k) CHECK(pulses[k] == int(12000 + k * 24000));
    }
    auto typo = compile("process = nentry(\"t [host:tempo]\", 0, 0, 1, 1);", 48000, libraries);
    CHECK(typo.program && typo.messages.find("unknown [host:tempo]") != std::string::npos);

    auto broken = compile("process = _ : nosuchfunction;", 48000, libraries);
    CHECK(broken.program == nullptr);
    CHECK(broken.messages.find("nosuchfunction") != std::string::npos);
    std::printf("error: %s", broken.messages.c_str());

    State state;
    state.code = "process = _;\n// with \"quotes\" and\nnew lines";
    state.status = "error";
    state.messages = "line 1: something\nline 2";
    state.inputs = 1;
    State back;
    CHECK(deserialize(serialize(state), back));
    CHECK(back.code == state.code && back.messages == state.messages && back.status == "error" && back.inputs == 1);
    CHECK(back.draft.empty());
    state.draft = "process = *(0.5);\n";
    CHECK(deserialize(serialize(state), back) && back.draft == state.draft && back.code == state.code);
    CHECK(!deserialize("garbage", back));

    std::printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
