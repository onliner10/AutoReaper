// Draws ImGui's triangles into a 32-bit 0x00RRGGBB pixel buffer, without a GPU:
// plugin windows then need only a way to show a bitmap on each platform.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

struct ImDrawData;

namespace autoreaper {

struct Bitmap {
    int width = 0;
    int height = 0;
    std::vector<uint32_t> pixels;
    void resize(int w, int h) {
        width = w;
        height = h;
        pixels.assign(std::size_t(w) * h, 0);
    }
};

// The font atlas as alpha values; ImGui's texture id is ignored (one atlas).
struct FontTexture {
    int width = 0;
    int height = 0;
    const unsigned char* alpha = nullptr;
};

void rasterize(const ImDrawData& data, const FontTexture& font, Bitmap& target);

}  // namespace autoreaper
