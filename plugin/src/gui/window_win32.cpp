// The plugin window on Windows: a child window of the host's HWND. Windows
// delivers its messages through the host's message loop to window_proc.
#include "window.h"

#include <clap/ext/gui.h>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <windowsx.h>

#include <algorithm>
#include <string>

namespace autoreaper {

struct PlatformWindow::Impl {
    Ui* ui = nullptr;
    HWND window = nullptr;
    const Bitmap* bitmap = nullptr;
    int width = 0, height = 0;
};

namespace {

const wchar_t* kClassName = L"AutoReaperFaustWindow";

HINSTANCE this_module() {
    HMODULE module = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCWSTR>(&this_module), &module);
    return module;
}

void draw(PlatformWindow::Impl& w, HDC dc) {
    if (!w.bitmap || w.bitmap->pixels.empty()) return;
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = w.bitmap->width;
    info.bmiHeader.biHeight = -w.bitmap->height;  // top-down rows
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;               // 0x00RRGGBB, as the Ui draws
    info.bmiHeader.biCompression = BI_RGB;
    SetDIBitsToDevice(dc, 0, 0, w.bitmap->width, w.bitmap->height, 0, 0, 0, w.bitmap->height,
                      w.bitmap->pixels.data(), &info, DIB_RGB_COLORS);
}

Key editor_key(WPARAM key) {
    switch (key) {
        case VK_LEFT: return Key::Left;
        case VK_RIGHT: return Key::Right;
        case VK_UP: return Key::Up;
        case VK_DOWN: return Key::Down;
        case VK_HOME: return Key::Home;
        case VK_END: return Key::End;
        case VK_PRIOR: return Key::PageUp;
        case VK_NEXT: return Key::PageDown;
        case VK_BACK: return Key::Backspace;
        case VK_DELETE: return Key::Delete;
        case VK_RETURN: return Key::Enter;
        case VK_TAB: return Key::Tab;
        case VK_ESCAPE: return Key::Escape;
        case VK_INSERT: return Key::Insert;
        case 'A': return Key::A;
        case 'C': return Key::C;
        case 'V': return Key::V;
        case 'X': return Key::X;
        case 'Y': return Key::Y;
        case 'Z': return Key::Z;
        default: return Key::Unknown;
    }
}

bool down(int key) { return (GetKeyState(key) & 0x8000) != 0; }

LRESULT CALLBACK window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_NCCREATE) {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
    }
    auto* w = reinterpret_cast<PlatformWindow::Impl*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (!w || !w->ui) return DefWindowProcW(hwnd, message, wparam, lparam);
    Ui& ui = *w->ui;
    switch (message) {
        case WM_PAINT: {
            PAINTSTRUCT paint;
            HDC dc = BeginPaint(hwnd, &paint);
            draw(*w, dc);
            EndPaint(hwnd, &paint);
            return 0;
        }
        case WM_ERASEBKGND: return 1;
        // Keys go to the editor, not to the dialog or host around it.
        case WM_GETDLGCODE: return DLGC_WANTALLKEYS | DLGC_WANTARROWS | DLGC_WANTCHARS | DLGC_WANTTAB;
        case WM_MOUSEMOVE: ui.mouse_move(float(GET_X_LPARAM(lparam)), float(GET_Y_LPARAM(lparam))); return 0;
        case WM_LBUTTONDOWN: case WM_RBUTTONDOWN: case WM_MBUTTONDOWN:
            SetFocus(hwnd);
            SetCapture(hwnd);
            ui.mouse_move(float(GET_X_LPARAM(lparam)), float(GET_Y_LPARAM(lparam)));
            ui.mouse_button(message == WM_LBUTTONDOWN ? 0 : message == WM_RBUTTONDOWN ? 1 : 2, true);
            return 0;
        case WM_LBUTTONUP: case WM_RBUTTONUP: case WM_MBUTTONUP:
            ReleaseCapture();
            ui.mouse_button(message == WM_LBUTTONUP ? 0 : message == WM_RBUTTONUP ? 1 : 2, false);
            return 0;
        case WM_MOUSEWHEEL: ui.wheel(float(GET_WHEEL_DELTA_WPARAM(wparam)) / WHEEL_DELTA); return 0;
        case WM_KEYDOWN: case WM_SYSKEYDOWN: case WM_KEYUP: case WM_SYSKEYUP:
            ui.modifiers(down(VK_CONTROL), down(VK_SHIFT), down(VK_MENU), down(VK_LWIN) || down(VK_RWIN));
            ui.key(editor_key(wparam), message == WM_KEYDOWN || message == WM_SYSKEYDOWN);
            return 0;
        case WM_CHAR:
            if (wparam >= 32 && wparam != 127) ui.text_utf16((unsigned short)wparam);  // pairs arrive in two messages
            return 0;
        case WM_SETFOCUS: ui.focus(true); return 0;
        case WM_KILLFOCUS: ui.focus(false); return 0;
        default: return DefWindowProcW(hwnd, message, wparam, lparam);
    }
}

std::wstring wide(const std::string& text) {
    std::wstring result(MultiByteToWideChar(CP_UTF8, 0, text.data(), int(text.size()), nullptr, 0), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), int(text.size()), result.data(), int(result.size()));
    return result;
}

std::string narrow(const wchar_t* text) {
    const int size = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    std::string result(size > 0 ? size - 1 : 0, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, -1, result.data(), size, nullptr, nullptr);
    return result;
}

}  // namespace

const char* PlatformWindow::api() { return CLAP_WINDOW_API_WIN32; }

PlatformWindow::PlatformWindow() : impl_(new Impl) {}

PlatformWindow::~PlatformWindow() {
    impl_->ui = nullptr;  // DestroyWindow sends focus and paint messages
    if (impl_->window) {
        SetWindowLongPtrW(impl_->window, GWLP_USERDATA, 0);
        DestroyWindow(impl_->window);
    }
    delete impl_;
}

void PlatformWindow::set_ui(Ui* ui) { impl_->ui = ui; }

bool PlatformWindow::attach(void* parent, int width, int height) {
    static ATOM registered = [] {
        WNDCLASSW window_class{};
        window_class.style = CS_DBLCLKS;
        window_class.lpfnWndProc = window_proc;
        window_class.hInstance = this_module();
        window_class.hCursor = LoadCursor(nullptr, IDC_IBEAM);
        window_class.lpszClassName = kClassName;
        return RegisterClassW(&window_class);
    }();
    if (!registered && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return false;
    if (impl_->window) DestroyWindow(impl_->window);
    impl_->width = width;
    impl_->height = height;
    impl_->window = CreateWindowExW(0, kClassName, L"", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS, 0, 0, width, height,
                                    static_cast<HWND>(parent), nullptr, this_module(), impl_);
    return impl_->window != nullptr;
}

void PlatformWindow::resize(int width, int height) {
    impl_->width = width;
    impl_->height = height;
    if (impl_->window) SetWindowPos(impl_->window, nullptr, 0, 0, width, height, SWP_NOZORDER | SWP_NOMOVE);
}

void PlatformWindow::show(bool visible) {
    if (impl_->window) ShowWindow(impl_->window, visible ? SW_SHOW : SW_HIDE);
}

void PlatformWindow::pump() {}

void PlatformWindow::present(const Bitmap& bitmap) {
    impl_->bitmap = &bitmap;
    if (!impl_->window) return;
    HDC dc = GetDC(impl_->window);
    draw(*impl_, dc);
    ReleaseDC(impl_->window, dc);
}

std::string PlatformWindow::clipboard_get() {
    std::string text;
    if (!OpenClipboard(impl_->window)) return text;
    if (HANDLE data = GetClipboardData(CF_UNICODETEXT)) {
        if (auto* chars = static_cast<const wchar_t*>(GlobalLock(data))) {
            text = narrow(chars);
            GlobalUnlock(data);
        }
    }
    CloseClipboard();
    std::string unix;  // the editor wants \n
    for (char c : text)
        if (c != '\r') unix += c;
    return unix;
}

void PlatformWindow::clipboard_set(const std::string& text) {
    std::string windows;
    for (char c : text) {
        if (c == '\n') windows += '\r';
        windows += c;
    }
    const std::wstring chars = wide(windows);
    if (!OpenClipboard(impl_->window)) return;
    EmptyClipboard();
    if (HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE, (chars.size() + 1) * sizeof(wchar_t))) {
        auto* target = static_cast<wchar_t*>(GlobalLock(memory));
        std::copy(chars.begin(), chars.end(), target);
        target[chars.size()] = 0;
        GlobalUnlock(memory);
        if (!SetClipboardData(CF_UNICODETEXT, memory)) GlobalFree(memory);
    }
    CloseClipboard();
}

}  // namespace autoreaper
