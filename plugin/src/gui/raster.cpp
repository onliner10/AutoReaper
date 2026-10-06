#include "raster.h"

#include <imgui.h>

#include <algorithm>
#include <cmath>

namespace autoreaper {

namespace {

struct Vertex {
    float x, y, u, v;
    float r, g, b, a;  // 0..255
};

Vertex vertex(const ImDrawVert& v) {
    return {v.pos.x, v.pos.y, v.uv.x, v.uv.y,
            float(v.col & 0xff), float((v.col >> 8) & 0xff), float((v.col >> 16) & 0xff), float(v.col >> 24)};
}

inline uint32_t blend(uint32_t dst, float r, float g, float b, float a) {  // a: 0..1
    const float dr = float((dst >> 16) & 0xff), dg = float((dst >> 8) & 0xff), db = float(dst & 0xff);
    const uint32_t nr = uint32_t(dr + (r - dr) * a), ng = uint32_t(dg + (g - dg) * a), nb = uint32_t(db + (b - db) * a);
    return (nr << 16) | (ng << 8) | nb;
}

// Pixel centres inside the triangle and the clip rectangle, with barycentric
// interpolation of texture coordinates and colour.
void triangle(const Vertex& a, const Vertex& b, const Vertex& c, const FontTexture& font, int clip_x0, int clip_y0,
              int clip_x1, int clip_y1, Bitmap& target) {
    const float area = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    if (std::fabs(area) < 1e-6f) return;
    const int x0 = std::max(clip_x0, int(std::floor(std::min({a.x, b.x, c.x}))));
    const int x1 = std::min(clip_x1, int(std::ceil(std::max({a.x, b.x, c.x}))));
    const int y0 = std::max(clip_y0, int(std::floor(std::min({a.y, b.y, c.y}))));
    const int y1 = std::min(clip_y1, int(std::ceil(std::max({a.y, b.y, c.y}))));
    if (x0 >= x1 || y0 >= y1) return;
    const bool flat = a.r == b.r && a.r == c.r && a.g == b.g && a.g == c.g && a.b == b.b && a.b == c.b && a.a == b.a &&
                      a.a == c.a;
    const float inv = 1.f / area;
    for (int y = y0; y < y1; ++y) {
        const float py = y + 0.5f;
        uint32_t* row = target.pixels.data() + size_t(y) * target.width;
        for (int x = x0; x < x1; ++x) {
            const float px = x + 0.5f;
            const float w0 = ((b.x - px) * (c.y - py) - (b.y - py) * (c.x - px)) * inv;
            const float w1 = ((c.x - px) * (a.y - py) - (c.y - py) * (a.x - px)) * inv;
            const float w2 = 1.f - w0 - w1;
            if (w0 < 0 || w1 < 0 || w2 < 0) continue;
            const float u = a.u * w0 + b.u * w1 + c.u * w2, v = a.v * w0 + b.v * w1 + c.v * w2;
            const int tx = std::clamp(int(u * font.width), 0, font.width - 1);
            const int ty = std::clamp(int(v * font.height), 0, font.height - 1);
            const float texture = font.alpha[ty * font.width + tx] / 255.f;
            float r = a.r, g = a.g, bl = a.b, al = a.a;
            if (!flat) {
                r = a.r * w0 + b.r * w1 + c.r * w2;
                g = a.g * w0 + b.g * w1 + c.g * w2;
                bl = a.b * w0 + b.b * w1 + c.b * w2;
                al = a.a * w0 + b.a * w1 + c.a * w2;
            }
            const float alpha = texture * al / 255.f;
            if (alpha <= 0.002f) continue;
            row[x] = blend(row[x], r, g, bl, alpha);
        }
    }
}

}  // namespace

void rasterize(const ImDrawData& data, const FontTexture& font, Bitmap& target) {
    std::fill(target.pixels.begin(), target.pixels.end(), 0);
    const ImVec2 origin = data.DisplayPos;
    for (int n = 0; n < data.CmdListsCount; ++n) {
        const ImDrawList* list = data.CmdLists[n];
        for (const ImDrawCmd& cmd : list->CmdBuffer) {
            if (cmd.UserCallback) continue;
            const int cx0 = std::max(0, int(cmd.ClipRect.x - origin.x)), cy0 = std::max(0, int(cmd.ClipRect.y - origin.y));
            const int cx1 = std::min(target.width, int(std::ceil(cmd.ClipRect.z - origin.x)));
            const int cy1 = std::min(target.height, int(std::ceil(cmd.ClipRect.w - origin.y)));
            if (cx0 >= cx1 || cy0 >= cy1) continue;
            for (unsigned int i = 0; i + 2 < cmd.ElemCount; i += 3) {
                const ImDrawIdx* idx = list->IdxBuffer.Data + cmd.IdxOffset + i;
                Vertex a = vertex(list->VtxBuffer[cmd.VtxOffset + idx[0]]);
                Vertex b = vertex(list->VtxBuffer[cmd.VtxOffset + idx[1]]);
                Vertex c = vertex(list->VtxBuffer[cmd.VtxOffset + idx[2]]);
                for (Vertex* p : {&a, &b, &c}) {
                    p->x -= origin.x;
                    p->y -= origin.y;
                }
                triangle(a, b, c, font, cx0, cy0, cx1, cy1, target);
            }
        }
    }
}

}  // namespace autoreaper
