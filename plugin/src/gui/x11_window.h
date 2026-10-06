// The plugin window on Linux: an X11 child of the host's window that shows the
// Ui's bitmap and feeds it mouse, keyboard and the CLIPBOARD selection. The
// host's timer drives it (pump, then present), all on the main thread.
#pragma once

#include "ui.h"

#include <string>

namespace autoreaper {

class X11Window {
public:
    X11Window();
    ~X11Window();
    bool attach(unsigned long parent, int width, int height);
    void resize(int width, int height);
    void show(bool visible);
    void pump(Ui& ui);
    void present(const Bitmap& bitmap);
    std::string clipboard_get();
    void clipboard_set(const std::string& text);

private:
    void serve_selection(void* request_event);
    struct Impl;
    Impl* impl_;
};

}  // namespace autoreaper
