#include "x11_window.h"

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>

#include <chrono>
#include <cstring>
#include <thread>

namespace autoreaper {

struct X11Window::Impl {
    Display* display = nullptr;
    Window window = 0;
    GC gc = nullptr;
    XImage* image = nullptr;
    int width = 0, height = 0;
    Atom clipboard = 0, utf8 = 0, targets = 0, property = 0;
    std::string owned;  // our CLIPBOARD text

    void drop_image() {
        if (!image) return;
        image->data = nullptr;  // the pixels belong to the Ui's bitmap
        XDestroyImage(image);
        image = nullptr;
    }
};

X11Window::X11Window() : impl_(new Impl) {}

X11Window::~X11Window() {
    if (impl_->display) {
        impl_->drop_image();
        if (impl_->gc) XFreeGC(impl_->display, impl_->gc);
        if (impl_->window) XDestroyWindow(impl_->display, impl_->window);
        XCloseDisplay(impl_->display);
    }
    delete impl_;
}

bool X11Window::attach(unsigned long parent, int width, int height) {
    Impl& x = *impl_;
    if (!x.display) x.display = XOpenDisplay(nullptr);
    if (!x.display) return false;
    if (x.window) XDestroyWindow(x.display, x.window);
    x.width = width;
    x.height = height;
    x.window = XCreateSimpleWindow(x.display, Window(parent), 0, 0, width, height, 0, 0, 0);
    XSelectInput(x.display, x.window, ExposureMask | ButtonPressMask | ButtonReleaseMask | PointerMotionMask |
                                          KeyPressMask | KeyReleaseMask | StructureNotifyMask | FocusChangeMask);
    if (!x.gc) x.gc = XCreateGC(x.display, x.window, 0, nullptr);
    x.clipboard = XInternAtom(x.display, "CLIPBOARD", False);
    x.utf8 = XInternAtom(x.display, "UTF8_STRING", False);
    x.targets = XInternAtom(x.display, "TARGETS", False);
    x.property = XInternAtom(x.display, "AUTOREAPER_FAUST_CLIPBOARD", False);
    XMapWindow(x.display, x.window);
    XFlush(x.display);
    return true;
}

void X11Window::resize(int width, int height) {
    impl_->width = width;
    impl_->height = height;
    if (impl_->display && impl_->window) XResizeWindow(impl_->display, impl_->window, width, height);
}

void X11Window::show(bool visible) {
    if (!impl_->display || !impl_->window) return;
    if (visible) XMapWindow(impl_->display, impl_->window);
    else XUnmapWindow(impl_->display, impl_->window);
    XFlush(impl_->display);
}

static Key editor_key(KeySym symbol) {
    switch (symbol) {
        case XK_Left: case XK_KP_Left: return Key::Left;
        case XK_Right: case XK_KP_Right: return Key::Right;
        case XK_Up: case XK_KP_Up: return Key::Up;
        case XK_Down: case XK_KP_Down: return Key::Down;
        case XK_Home: case XK_KP_Home: return Key::Home;
        case XK_End: case XK_KP_End: return Key::End;
        case XK_Page_Up: case XK_KP_Page_Up: return Key::PageUp;
        case XK_Page_Down: case XK_KP_Page_Down: return Key::PageDown;
        case XK_BackSpace: return Key::Backspace;
        case XK_Delete: case XK_KP_Delete: return Key::Delete;
        case XK_Return: case XK_KP_Enter: return Key::Enter;
        case XK_Tab: case XK_ISO_Left_Tab: return Key::Tab;
        case XK_Escape: return Key::Escape;
        case XK_Insert: return Key::Insert;
        case XK_a: return Key::A;
        case XK_c: return Key::C;
        case XK_v: return Key::V;
        case XK_x: return Key::X;
        case XK_y: return Key::Y;
        case XK_z: return Key::Z;
        default: return Key::Unknown;
    }
}

void X11Window::pump(Ui& ui) {
    Impl& x = *impl_;
    if (!x.display) return;
    while (XPending(x.display)) {
        XEvent event;
        XNextEvent(x.display, &event);
        if (event.xany.window != x.window) continue;
        switch (event.type) {
            case MotionNotify:
                ui.mouse_move(float(event.xmotion.x), float(event.xmotion.y));
                break;
            case ButtonPress:
            case ButtonRelease: {
                const bool down = event.type == ButtonPress;
                const unsigned int button = event.xbutton.button;
                ui.mouse_move(float(event.xbutton.x), float(event.xbutton.y));
                if (down) XSetInputFocus(x.display, x.window, RevertToParent, CurrentTime);
                if (button == Button4 || button == Button5) {
                    if (down) ui.wheel(button == Button4 ? 1.f : -1.f);
                } else if (button <= Button3) {
                    ui.mouse_button(button == Button1 ? 0 : button == Button3 ? 1 : 2, down);
                }
                break;
            }
            case KeyPress:
            case KeyRelease: {
                const bool down = event.type == KeyPress;
                const unsigned int state = event.xkey.state;
                const bool ctrl = state & ControlMask;
                ui.modifiers(ctrl, state & ShiftMask, state & Mod1Mask, state & Mod4Mask);
                const KeySym symbol = XLookupKeysym(&event.xkey, 0);
                ui.key(editor_key(symbol), down);
                if (down && !ctrl) {
                    char text[32];
                    KeySym typed;
                    const int count = XLookupString(&event.xkey, text, sizeof(text), &typed, nullptr);
                    for (int i = 0; i < count; ++i) {
                        const unsigned char c = static_cast<unsigned char>(text[i]);
                        if (c >= 32 && c != 127) ui.text(c);  // Latin-1 is Unicode's first 256
                    }
                }
                break;
            }
            case FocusIn: ui.focus(true); break;
            case FocusOut: ui.focus(false); break;
            case SelectionRequest: serve_selection(&event); break;
            case SelectionClear: x.owned.clear(); break;
            default: break;
        }
    }
}

void X11Window::present(const Bitmap& bitmap) {
    Impl& x = *impl_;
    if (!x.display || !x.window || bitmap.pixels.empty()) return;
    if (!x.image || x.image->width != bitmap.width || x.image->height != bitmap.height ||
        x.image->data != reinterpret_cast<char*>(const_cast<uint32_t*>(bitmap.pixels.data()))) {
        x.drop_image();
        const int screen = DefaultScreen(x.display);
        x.image = XCreateImage(x.display, DefaultVisual(x.display, screen), DefaultDepth(x.display, screen), ZPixmap, 0,
                               reinterpret_cast<char*>(const_cast<uint32_t*>(bitmap.pixels.data())), bitmap.width,
                               bitmap.height, 32, 0);
        if (!x.image) return;
    }
    XPutImage(x.display, x.window, x.gc, x.image, 0, 0, 0, 0, bitmap.width, bitmap.height);
    XFlush(x.display);
}

void X11Window::clipboard_set(const std::string& text) {
    Impl& x = *impl_;
    if (!x.display || !x.window) return;
    x.owned = text;
    XSetSelectionOwner(x.display, x.clipboard, x.window, CurrentTime);
    XFlush(x.display);
}

std::string X11Window::clipboard_get() {
    Impl& x = *impl_;
    if (!x.display || !x.window) return "";
    const Window owner = XGetSelectionOwner(x.display, x.clipboard);
    if (owner == x.window) return x.owned;
    if (owner == None) return "";
    XConvertSelection(x.display, x.clipboard, x.utf8, x.property, x.window, CurrentTime);
    XFlush(x.display);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
    XEvent event;
    while (!XCheckTypedWindowEvent(x.display, x.window, SelectionNotify, &event)) {
        if (std::chrono::steady_clock::now() > deadline) return "";
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    if (event.xselection.property == None) return "";
    Atom type;
    int format;
    unsigned long items, remaining;
    unsigned char* data = nullptr;
    std::string text;
    if (XGetWindowProperty(x.display, x.window, x.property, 0, 1 << 20, True, AnyPropertyType, &type, &format, &items,
                           &remaining, &data) == Success && data) {
        if (format == 8) text.assign(reinterpret_cast<char*>(data), items);
        XFree(data);
    }
    return text;
}

void X11Window::serve_selection(void* request_event) {
    Impl& x = *impl_;
    const XSelectionRequestEvent& request = static_cast<XEvent*>(request_event)->xselectionrequest;
    XEvent reply{};
    reply.xselection.type = SelectionNotify;
    reply.xselection.requestor = request.requestor;
    reply.xselection.selection = request.selection;
    reply.xselection.target = request.target;
    reply.xselection.time = request.time;
    reply.xselection.property = None;
    const Atom property = request.property != None ? request.property : request.target;
    if (request.target == x.targets) {
        const Atom offered[] = {x.targets, x.utf8, XA_STRING};
        XChangeProperty(x.display, request.requestor, property, XA_ATOM, 32, PropModeReplace,
                        reinterpret_cast<const unsigned char*>(offered), 3);
        reply.xselection.property = property;
    } else if (request.target == x.utf8 || request.target == XA_STRING) {
        XChangeProperty(x.display, request.requestor, property, request.target, 8, PropModeReplace,
                        reinterpret_cast<const unsigned char*>(x.owned.data()), int(x.owned.size()));
        reply.xselection.property = property;
    }
    XSendEvent(x.display, request.requestor, False, 0, &reply);
    XFlush(x.display);
}

}  // namespace autoreaper
