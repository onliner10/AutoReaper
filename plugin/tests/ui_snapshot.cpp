// Draws the plugin window for a failed compile and writes it as a PPM image, to
// look at the editor without a host: ui_snapshot out.ppm. Also checks that
// Faust's messages of old and new versions mark the right lines.
#include "../src/gui/ui.h"

#include <cstdio>

using namespace autoreaper;

int main(int argc, char** argv) {
    const auto old_format = error_lines("faust : 5 : ERROR : undefined symbol : nosuch");
    const auto new_format = error_lines("faust:7 : ERROR : undefined symbol : nosuch\n"
                                        "/usr/share/faust/basics.lib : 3 : ERROR : not the user's line");
    if (old_format.size() != 1 || old_format.count(5) != 1 || new_format.size() != 1 || new_format.count(7) != 1 ||
        new_format.at(7) != "ERROR : undefined symbol : nosuch") {
        std::printf("FAIL: error lines\n");
        return 1;
    }
    Ui ui(Clipboard{});
    ui.resize(760, 480);
    UiModel model;
    model.code = "import(\"stdfaust.lib\");\nprocess = _, _;\n";
    model.draft =
        "import(\"stdfaust.lib\");\n"
        "// Duck the lead while the drums play.\n"
        "key(kl, kr) = (abs(kl) + abs(kr)) / 2 : an.amp_follower_ar(0.002, 0.15);\n"
        "gain(kl, kr) = ba.db2linear(-9 * (key(kl, kr) > 0.05)) : si.smoo;\n"
        "process(l, r, kl, kr) = l * g, r * g with { g = gain(kl, kr) : nosuch; };\n";
    model.status = "error";
    model.messages = "faust:5 : ERROR : undefined symbol : nosuch";
    model.inputs = 4;
    model.outputs = 2;
    model.revision = 1;
    for (int i = 0; i < 3; ++i) ui.frame(model, 1.0 / 30);  // ImGui lays out over a couple of frames
    const Bitmap& bitmap = ui.bitmap();
    FILE* file = std::fopen(argc > 1 ? argv[1] : "ui.ppm", "wb");
    if (!file) return 1;
    std::fprintf(file, "P6\n%d %d\n255\n", bitmap.width, bitmap.height);
    for (uint32_t pixel : bitmap.pixels) {
        const unsigned char rgb[3] = {static_cast<unsigned char>(pixel >> 16), static_cast<unsigned char>(pixel >> 8),
                                      static_cast<unsigned char>(pixel)};
        std::fwrite(rgb, 1, 3, file);
    }
    std::fclose(file);
    return 0;
}
