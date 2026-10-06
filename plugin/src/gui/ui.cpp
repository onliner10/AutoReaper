#include "ui.h"

#include <TextEditor.h>
#include <imgui.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <sstream>

namespace autoreaper {

namespace {

const TextEditor::LanguageDefinition& faust_language() {
    static TextEditor::LanguageDefinition language = [] {
        TextEditor::LanguageDefinition faust = TextEditor::LanguageDefinition::C();
        faust.mName = "Faust";
        faust.mKeywords.clear();
        for (const char* word : {"import", "process", "with", "letrec", "where", "declare", "environment", "library",
                                 "component", "case", "seq", "par", "sum", "prod", "inputs", "outputs", "route",
                                 "waveform", "soundfile", "int", "float", "mem", "prefix", "rdtable", "rwtable",
                                 "select2", "select3", "ffunction", "fconstant", "fvariable", "attach", "enable",
                                 "control", "ondemand"})
            faust.mKeywords.insert(word);
        faust.mIdentifiers.clear();
        for (const char* word : {"button", "checkbox", "hslider", "vslider", "nentry", "hgroup", "vgroup", "tgroup",
                                 "hbargraph", "vbargraph", "aa", "an", "ba", "co", "de", "dm", "dx", "ef", "en", "fd",
                                 "fi", "ho", "it", "la", "ma", "mi", "mo", "no", "os", "pf", "pl", "pm", "qu", "re",
                                 "ro", "sf", "si", "so", "sp", "sy", "ve", "vl", "wa", "wd", "ma"}) {
            TextEditor::Identifier identifier;
            identifier.mDeclaration = "Faust";
            faust.mIdentifiers.insert({word, identifier});
        }
        faust.mPreprocIdentifiers.clear();
        faust.mPreprocChar = '\x01';  // Faust has no preprocessor
        return faust;
    }();
    return language;
}

// "faust : 3 : ERROR : undefined symbol : foo" -> line 3 (lines of library
// files start with their path instead of "faust").
TextEditor::ErrorMarkers markers(const std::string& messages) {
    TextEditor::ErrorMarkers result;
    std::istringstream lines(messages);
    std::string line;
    while (std::getline(lines, line)) {
        if (line.rfind("faust : ", 0) != 0) continue;
        const size_t colon = 5;
        char* end = nullptr;
        const long number = std::strtol(line.c_str() + colon + 3, &end, 10);
        if (number <= 0 || end == line.c_str() + colon + 3) continue;
        std::string message = line.substr(end - line.c_str());
        message.erase(0, message.find_first_not_of(" :"));
        std::string& text = result[int(number)];
        text += (text.empty() ? "" : "\n") + message;
    }
    return result;
}

ImGuiKey imgui_key(Key key) {
    switch (key) {
        case Key::Left: return ImGuiKey_LeftArrow;
        case Key::Right: return ImGuiKey_RightArrow;
        case Key::Up: return ImGuiKey_UpArrow;
        case Key::Down: return ImGuiKey_DownArrow;
        case Key::Home: return ImGuiKey_Home;
        case Key::End: return ImGuiKey_End;
        case Key::PageUp: return ImGuiKey_PageUp;
        case Key::PageDown: return ImGuiKey_PageDown;
        case Key::Backspace: return ImGuiKey_Backspace;
        case Key::Delete: return ImGuiKey_Delete;
        case Key::Enter: return ImGuiKey_Enter;
        case Key::Tab: return ImGuiKey_Tab;
        case Key::Escape: return ImGuiKey_Escape;
        case Key::Insert: return ImGuiKey_Insert;
        case Key::A: return ImGuiKey_A;
        case Key::C: return ImGuiKey_C;
        case Key::V: return ImGuiKey_V;
        case Key::X: return ImGuiKey_X;
        case Key::Y: return ImGuiKey_Y;
        case Key::Z: return ImGuiKey_Z;
        default: return ImGuiKey_None;
    }
}

const ImVec4 kGreen(0.45f, 0.85f, 0.45f, 1), kRed(1, 0.45f, 0.4f, 1), kYellow(1, 0.8f, 0.3f, 1), kGrey(0.7f, 0.7f, 0.7f, 1);

}  // namespace

Ui::Ui(Clipboard clipboard) : clipboard_(std::move(clipboard)) {
    ImGuiContext* previous = ImGui::GetCurrentContext();
    context_ = ImGui::CreateContext();
    ImGui::SetCurrentContext(context_);
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.LogFilename = nullptr;
    io.ClipboardUserData = this;
    io.GetClipboardTextFn = [](void* user) -> const char* {
        Ui* ui = static_cast<Ui*>(user);
        ui->clipboard_text_ = ui->clipboard_.get ? ui->clipboard_.get() : ui->clipboard_text_;
        return ui->clipboard_text_.c_str();
    };
    io.SetClipboardTextFn = [](void* user, const char* text) {
        Ui* ui = static_cast<Ui*>(user);
        ui->clipboard_text_ = text;
        if (ui->clipboard_.set) ui->clipboard_.set(ui->clipboard_text_);
    };
    ImGui::StyleColorsDark();
    ImGuiStyle& style = ImGui::GetStyle();
    style.AntiAliasedLines = style.AntiAliasedFill = style.AntiAliasedLinesUseTex = false;
    style.WindowRounding = style.FrameRounding = 0;
    io.Fonts->AddFontDefault();
    unsigned char* pixels = nullptr;
    io.Fonts->GetTexDataAsAlpha8(&pixels, &font_.width, &font_.height);
    font_.alpha = pixels;
    io.Fonts->SetTexID(ImTextureID(intptr_t(1)));
    editor_ = std::make_unique<TextEditor>();
    editor_->SetLanguageDefinition(faust_language());
    editor_->SetPalette(TextEditor::GetDarkPalette());
    editor_->SetShowWhitespaces(false);
    editor_->SetTabSize(4);
    ImGui::SetCurrentContext(previous);
}

Ui::~Ui() {
    editor_.reset();
    ImGui::DestroyContext(context_);
}

void Ui::resize(int width, int height) { bitmap_.resize(width, height); }

#define WITH_CONTEXT ImGuiContext* previous = ImGui::GetCurrentContext(); ImGui::SetCurrentContext(context_)
#define RESTORE_CONTEXT ImGui::SetCurrentContext(previous)

void Ui::mouse_move(float x, float y) { WITH_CONTEXT; ImGui::GetIO().AddMousePosEvent(x, y); RESTORE_CONTEXT; }
void Ui::mouse_button(int button, bool down) { WITH_CONTEXT; ImGui::GetIO().AddMouseButtonEvent(button, down); RESTORE_CONTEXT; }
void Ui::wheel(float steps) { WITH_CONTEXT; ImGui::GetIO().AddMouseWheelEvent(0, steps); RESTORE_CONTEXT; }
void Ui::text(unsigned int codepoint) { WITH_CONTEXT; ImGui::GetIO().AddInputCharacter(codepoint); RESTORE_CONTEXT; }
void Ui::focus(bool focused) { WITH_CONTEXT; ImGui::GetIO().AddFocusEvent(focused); RESTORE_CONTEXT; }

void Ui::modifiers(bool ctrl, bool shift, bool alt, bool super) {
    WITH_CONTEXT;
    ImGuiIO& io = ImGui::GetIO();
    io.AddKeyEvent(ImGuiMod_Ctrl, ctrl);
    io.AddKeyEvent(ImGuiMod_Shift, shift);
    io.AddKeyEvent(ImGuiMod_Alt, alt);
    io.AddKeyEvent(ImGuiMod_Super, super);
    RESTORE_CONTEXT;
}

void Ui::key(Key key, bool down) {
    const ImGuiKey mapped = imgui_key(key);
    if (mapped == ImGuiKey_None) return;
    WITH_CONTEXT;
    ImGui::GetIO().AddKeyEvent(mapped, down);
    RESTORE_CONTEXT;
}

UiAction Ui::frame(const UiModel& model, double seconds) {
    WITH_CONTEXT;
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(float(bitmap_.width), float(bitmap_.height));
    io.DeltaTime = float(seconds > 0 ? seconds : 1.0 / 30);
    if (model.revision != revision_) {
        revision_ = model.revision;
        editor_->SetText(model.draft.empty() ? model.code : model.draft);
    }
    const std::string marked = model.status == "error" ? model.messages : "";
    if (marked != marked_messages_) {
        marked_messages_ = marked;
        editor_->SetErrorMarkers(markers(marked));
    }

    UiAction action = UiAction::None;
    ImGui::NewFrame();
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(io.DisplaySize);
    ImGui::Begin("Faust", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                                       ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoBringToFrontOnFocus);
    if (ImGui::Button("Compile") || (io.KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_Enter, false))) action = UiAction::Compile;
    ImGui::SameLine();
    const bool draft = !model.draft.empty();
    if (draft && model.status == "error") {
        ImGui::TextColored(kRed, "Compile failed (see below); the last compiled code keeps running.");
    } else if (draft) {
        ImGui::TextColored(kYellow, "Changed, not compiled: Compile (Ctrl+Enter) runs it.");
    } else if (model.status == "ok") {
        ImGui::TextColored(kGreen, "Running. %d in, %d out (main 1-2, sidechain 3-4).", model.inputs, model.outputs);
    } else if (model.status == "error") {
        ImGui::TextColored(kRed, "This code does not compile; nothing runs (audio passes through).");
    } else {
        ImGui::TextColored(kGrey, "Not compiled yet.");
    }
    float messages_height = 0;
    int message_lines = 0;
    for (char c : model.messages) message_lines += c == '\n';
    if (!model.messages.empty() && model.status == "error")
        messages_height = (std::min(message_lines + 1, 6)) * ImGui::GetTextLineHeightWithSpacing() + 8;
    editor_->Render("code", ImVec2(0, -messages_height), true);
    if (editor_->IsTextChanged()) edited_ = true;
    if (messages_height > 0) {
        ImGui::BeginChild("messages", ImVec2(0, 0), false);
        ImGui::PushStyleColor(ImGuiCol_Text, kRed);
        ImGui::TextWrapped("%s", model.messages.c_str());
        ImGui::PopStyleColor();
        ImGui::EndChild();
    }
    ImGui::End();
    ImGui::Render();
    rasterize(*ImGui::GetDrawData(), font_, bitmap_);
    RESTORE_CONTEXT;
    return action;
}

bool Ui::edited(std::string& text) {
    if (!edited_) return false;
    edited_ = false;
    text = editor_->GetText();
    return true;
}

}  // namespace autoreaper
