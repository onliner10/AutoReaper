// The plugin window: a Faust code editor with syntax highlighting, a Compile
// button, the compile status and Faust's messages, marked on their lines.
// Platform code feeds it mouse, keyboard and clipboard, and shows bitmap().
#pragma once

#include "raster.h"

#include <functional>
#include <map>
#include <memory>
#include <string>

struct ImGuiContext;
class TextEditor;

namespace autoreaper {

struct UiModel {
    std::string code;      // the running code (last successful compile)
    std::string draft;     // editor text that differs from code, or empty
    std::string status;    // ok | error | none
    std::string messages;  // Faust's messages from the last compile
    int inputs = 0;
    int outputs = 0;
    int revision = 0;      // bumped when code or draft change outside the editor
};

enum class UiAction { None, Compile };

// Keys the editor uses; platform code maps its key codes to these.
enum class Key { Unknown, Left, Right, Up, Down, Home, End, PageUp, PageDown, Backspace, Delete, Enter, Tab, Escape,
                 Insert, A, C, V, X, Y, Z };

struct Clipboard {
    std::function<std::string()> get;
    std::function<void(const std::string&)> set;
};

// The lines of the user's code that Faust's messages point at, with the messages.
std::map<int, std::string> error_lines(const std::string& messages);

class Ui {
public:
    explicit Ui(Clipboard clipboard);
    ~Ui();
    void resize(int width, int height);
    void mouse_move(float x, float y);
    void mouse_button(int button, bool down);  // 0 left, 1 right, 2 middle
    void wheel(float steps);
    void modifiers(bool ctrl, bool shift, bool alt, bool super);
    void key(Key key, bool down);
    void text(unsigned int codepoint);
    void focus(bool focused);

    UiAction frame(const UiModel& model, double seconds);
    // The editor's text after the user changed it in the last frame.
    bool edited(std::string& text);
    const Bitmap& bitmap() const { return bitmap_; }

private:
    Clipboard clipboard_;
    std::string clipboard_text_;
    ImGuiContext* context_ = nullptr;
    std::unique_ptr<TextEditor> editor_;
    int revision_ = -1;
    std::string marked_messages_;
    bool edited_ = false;
    Bitmap bitmap_;
    FontTexture font_;
};

}  // namespace autoreaper
