cmake_minimum_required(VERSION 3.16)
project(autoreaper_faust CXX)

# AutoReaper Faust: a CLAP plugin that compiles Faust code in place (libfaust)
# and keeps the code in its own state, so the project stores it like any
# plugin's settings. Its window is a small code editor (Dear ImGui, drawn in
# software) with a Compile button.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

include(FetchContent)
FetchContent_Declare(clap GIT_REPOSITORY https://github.com/free-audio/clap.git GIT_TAG 1.2.9 GIT_SHALLOW TRUE)
FetchContent_Declare(imgui GIT_REPOSITORY https://github.com/ocornut/imgui.git GIT_TAG v1.89.9 GIT_SHALLOW TRUE)
FetchContent_Declare(texteditor GIT_REPOSITORY https://github.com/BalazsJako/ImGuiColorTextEdit.git
                     GIT_TAG ca2f9f1462e3b60e56351bc466acda448c5ea50d)
FetchContent_MakeAvailable(clap imgui texteditor)

find_path(FAUST_INCLUDE_DIR faust/dsp/llvm-dsp.h REQUIRED)
find_library(FAUST_LIBRARY faust REQUIRED)

add_library(autoreaper_faust_engine STATIC src/engine.cpp)
target_include_directories(autoreaper_faust_engine PUBLIC ${FAUST_INCLUDE_DIR})
target_link_libraries(autoreaper_faust_engine PUBLIC ${FAUST_LIBRARY})

add_library(autoreaper_faust_ui STATIC
    src/gui/ui.cpp src/gui/raster.cpp
    ${imgui_SOURCE_DIR}/imgui.cpp ${imgui_SOURCE_DIR}/imgui_draw.cpp ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp ${texteditor_SOURCE_DIR}/TextEditor.cpp)
target_include_directories(autoreaper_faust_ui PUBLIC ${imgui_SOURCE_DIR} ${texteditor_SOURCE_DIR})
# The editor predates ImGui moving PushItemFlag to imgui_internal.h.
set_source_files_properties(${texteditor_SOURCE_DIR}/TextEditor.cpp PROPERTIES
    COMPILE_OPTIONS "-include;imgui.h;-include;imgui_internal.h")

add_library(autoreaper_faust MODULE src/plugin.cpp)
target_link_libraries(autoreaper_faust PRIVATE clap autoreaper_faust_engine autoreaper_faust_ui)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    find_package(X11 REQUIRED)
    target_sources(autoreaper_faust PRIVATE src/gui/x11_window.cpp)
    target_link_libraries(autoreaper_faust PRIVATE X11::X11)
endif()
set_target_properties(autoreaper_faust PROPERTIES PREFIX "" SUFFIX ".clap" OUTPUT_NAME "AutoReaper Faust"
                      CXX_VISIBILITY_PRESET hidden)

# Headless checks: the engine and state format, and a picture of the window.
add_executable(engine_test tests/engine_test.cpp)
target_link_libraries(engine_test PRIVATE autoreaper_faust_engine)
add_executable(ui_snapshot tests/ui_snapshot.cpp)
target_link_libraries(ui_snapshot PRIVATE autoreaper_faust_ui)
