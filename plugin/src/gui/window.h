// The plugin window: a child of the host's window that shows the Ui's bitmap
// and passes it mouse, keyboard and clipboard. One implementation per
// platform (window_x11.cpp, window_win32.cpp, window_cocoa.mm). The host's
// timer drives it on the main thread: pump() then present().
#pragma once

#include "ui.h"

#include <string>

namespace autoreaper {

class PlatformWindow {
public:
    static const char* api();  // the CLAP window API: x11, win32 or cocoa
    PlatformWindow();
    ~PlatformWindow();
    void set_ui(Ui* ui);
    // parent: an X11 Window id (as an integer), an HWND, or an NSView*.
    bool attach(void* parent, int width, int height);
    void resize(int width, int height);
    void show(bool visible);
    void pump();  // X11 reads its events here; Windows and macOS deliver them to the window
    void present(const Bitmap& bitmap);
    std::string clipboard_get();
    void clipboard_set(const std::string& text);

    struct Impl;  // per platform

private:
    Impl* impl_;
};

}  // namespace autoreaper
