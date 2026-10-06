cmake_minimum_required(VERSION 3.20)
project(autoreaper_faust VERSION 0.1.0 LANGUAGES CXX)
if(APPLE)
    enable_language(OBJCXX)
endif()

# AutoReaper Faust: a CLAP plugin that compiles Faust code in place (libfaust)
# and keeps the code in its own state, so the project stores it like any
# plugin's settings. Its window is a small code editor (Dear ImGui, drawn in
# software) with a Compile button. Linux, Windows and macOS.
#
# FAUST_DIR points at a Faust install (with include/faust and lib) when CMake
# does not find it by itself, e.g. -DFAUST_DIR="C:/Program Files/Faust".

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(MSVC)
    add_compile_options(/utf-8)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX)
endif()

include(FetchContent)
FetchContent_Declare(clap GIT_REPOSITORY https://github.com/free-audio/clap.git GIT_TAG 1.2.9 GIT_SHALLOW TRUE)
FetchContent_Declare(imgui GIT_REPOSITORY https://github.com/ocornut/imgui.git GIT_TAG v1.89.9 GIT_SHALLOW TRUE)
FetchContent_Declare(texteditor GIT_REPOSITORY https://github.com/BalazsJako/ImGuiColorTextEdit.git
                     GIT_TAG ca2f9f1462e3b60e56351bc466acda448c5ea50d)
FetchContent_MakeAvailable(clap imgui texteditor)

set(FAUST_DIR "" CACHE PATH "A Faust install with include/faust and lib")
find_path(FAUST_INCLUDE_DIR faust/dsp/llvm-dsp.h HINTS "${FAUST_DIR}/include" REQUIRED)
find_library(FAUST_LIBRARY NAMES faust libfaust faust.dll HINTS "${FAUST_DIR}/lib" REQUIRED)
message(STATUS "libfaust: ${FAUST_LIBRARY} (headers in ${FAUST_INCLUDE_DIR})")

add_library(autoreaper_faust_engine STATIC src/engine.cpp)
target_include_directories(autoreaper_faust_engine PUBLIC ${FAUST_INCLUDE_DIR})
target_link_libraries(autoreaper_faust_engine PUBLIC ${FAUST_LIBRARY})

add_library(autoreaper_faust_ui STATIC
    src/gui/ui.cpp src/gui/raster.cpp
    ${imgui_SOURCE_DIR}/imgui.cpp ${imgui_SOURCE_DIR}/imgui_draw.cpp ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp ${texteditor_SOURCE_DIR}/TextEditor.cpp)
target_include_directories(autoreaper_faust_ui PUBLIC ${imgui_SOURCE_DIR} ${texteditor_SOURCE_DIR})
# The editor predates ImGui moving PushItemFlag to imgui_internal.h.
if(MSVC)
    set_source_files_properties(${texteditor_SOURCE_DIR}/TextEditor.cpp PROPERTIES
        COMPILE_OPTIONS "/FIimgui.h;/FIimgui_internal.h")
else()
    set_source_files_properties(${texteditor_SOURCE_DIR}/TextEditor.cpp PROPERTIES
        COMPILE_OPTIONS "-include;imgui.h;-include;imgui_internal.h")
endif()

add_library(autoreaper_faust MODULE src/plugin.cpp)
target_link_libraries(autoreaper_faust PRIVATE clap autoreaper_faust_engine autoreaper_faust_ui)
set_target_properties(autoreaper_faust PROPERTIES OUTPUT_NAME "AutoReaper Faust" CXX_VISIBILITY_PRESET hidden)
if(WIN32)
    target_sources(autoreaper_faust PRIVATE src/gui/window_win32.cpp)
    target_link_libraries(autoreaper_faust PRIVATE user32 gdi32)
    set_target_properties(autoreaper_faust PROPERTIES PREFIX "" SUFFIX ".clap")
elseif(APPLE)
    target_sources(autoreaper_faust PRIVATE src/gui/window_cocoa.mm)
    string(REPLACE "." "_" view_suffix "${PROJECT_VERSION}")
    target_compile_definitions(autoreaper_faust PRIVATE AUTOREAPER_FAUST_VIEW=AutoReaperFaustView_${view_suffix})
    target_link_libraries(autoreaper_faust PRIVATE "-framework Cocoa")
    # A CLAP plugin on macOS is a bundle: AutoReaper Faust.clap/Contents/MacOS/AutoReaper Faust.
    set_target_properties(autoreaper_faust PROPERTIES BUNDLE TRUE BUNDLE_EXTENSION clap
        MACOSX_BUNDLE_GUI_IDENTIFIER com.autoreaper.faust MACOSX_BUNDLE_BUNDLE_VERSION ${PROJECT_VERSION}
        MACOSX_BUNDLE_SHORT_VERSION_STRING ${PROJECT_VERSION})
else()
    find_package(X11 REQUIRED)
    target_sources(autoreaper_faust PRIVATE src/gui/window_x11.cpp)
    target_link_libraries(autoreaper_faust PRIVATE X11::X11)
    set_target_properties(autoreaper_faust PROPERTIES PREFIX "" SUFFIX ".clap")
endif()

# A package for users carries libfaust beside the plugin (plugin/package.py):
# load it from there, never from where it was built.
option(AUTOREAPER_PACKAGE "Find libfaust beside the plugin (for plugin/package.py)" OFF)
if(AUTOREAPER_PACKAGE)
    if(WIN32)
        target_link_options(autoreaper_faust PRIVATE "/DELAYLOAD:faust.dll")
        target_link_libraries(autoreaper_faust PRIVATE delayimp)
    elseif(APPLE)
        set_target_properties(autoreaper_faust PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE
                              INSTALL_RPATH "@loader_path/../Frameworks")
    else()
        set_target_properties(autoreaper_faust PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE INSTALL_RPATH "$ORIGIN")
    endif()
endif()

# Headless checks: the engine and state format, and a picture of the window.
add_executable(engine_test tests/engine_test.cpp)
target_link_libraries(engine_test PRIVATE autoreaper_faust_engine)
add_executable(ui_snapshot tests/ui_snapshot.cpp)
target_link_libraries(ui_snapshot PRIVATE autoreaper_faust_ui)
# Loads the built plugin as a host would: clap_host_test "<path to AutoReaper Faust.clap>".
add_executable(clap_host_test tests/clap_host_test.cpp)
target_link_libraries(clap_host_test PRIVATE clap ${CMAKE_DL_LIBS})
add_dependencies(clap_host_test autoreaper_faust)
