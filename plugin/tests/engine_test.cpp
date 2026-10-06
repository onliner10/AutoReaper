// Headless checks: compile, process (ducking keyed by the sidechain), errors, state.
#include "../src/engine.h"

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
        process(*result.program, in, 4, out, 512 < frames - at ? 512 : frames - at);
    }
    const float before = rms(ol, 4800, 24000), after = rms(ol, 33600, 48000);
    const float drop = 20 * std::log10(after / before);
    std::printf("ducking: %.2f dB\n", drop);
    CHECK(drop < -11 && drop > -13);

    // Missing sidechain input: reads silence, no ducking.
    reset(*result.program);
    process(*result.program, inputs, 2, outputs, 4800);
    CHECK(rms(ol, 2400, 4800) > 0.3f);

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
