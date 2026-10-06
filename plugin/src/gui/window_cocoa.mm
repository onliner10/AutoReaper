// The plugin window on macOS: an NSView added to the host's view. AppKit
// delivers mouse and keyboard to it; drawRect: draws the Ui's bitmap.
#include "window.h"

#include <clap/ext/gui.h>

#import <Cocoa/Cocoa.h>

// Objective-C class names are global in the process: another build of this
// plugin loaded at the same time must not share it.
#ifndef AUTOREAPER_FAUST_VIEW
#define AUTOREAPER_FAUST_VIEW AutoReaperFaustView
#endif

namespace autoreaper {

struct PlatformWindow::Impl {
    Ui* ui = nullptr;
    NSView* view = nil;
    const Bitmap* bitmap = nullptr;
};

}  // namespace autoreaper

using autoreaper::Key;

static Key editor_key(unsigned short code) {
    switch (code) {
        case 0x7B: return Key::Left;
        case 0x7C: return Key::Right;
        case 0x7E: return Key::Up;
        case 0x7D: return Key::Down;
        case 0x73: return Key::Home;
        case 0x77: return Key::End;
        case 0x74: return Key::PageUp;
        case 0x79: return Key::PageDown;
        case 0x33: return Key::Backspace;
        case 0x75: return Key::Delete;
        case 0x24: case 0x4C: return Key::Enter;
        case 0x30: return Key::Tab;
        case 0x35: return Key::Escape;
        case 0x72: return Key::Insert;
        case 0x00: return Key::A;
        case 0x08: return Key::C;
        case 0x09: return Key::V;
        case 0x07: return Key::X;
        case 0x10: return Key::Y;
        case 0x06: return Key::Z;
        default: return Key::Unknown;
    }
}

@interface AUTOREAPER_FAUST_VIEW : NSView {
@public
    autoreaper::PlatformWindow::Impl* impl;
    NSTrackingArea* tracking;
}
@end

@implementation AUTOREAPER_FAUST_VIEW

- (BOOL)isFlipped { return YES; }
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)acceptsFirstMouse:(NSEvent*)event { return YES; }

- (void)drawRect:(NSRect)dirty {
    const autoreaper::Bitmap* bitmap = impl ? impl->bitmap : nullptr;
    if (!bitmap || bitmap->pixels.empty()) return;
    CGContextRef context = [[NSGraphicsContext currentContext] CGContext];
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef provider = CGDataProviderCreateWithData(nullptr, bitmap->pixels.data(),
                                                              bitmap->pixels.size() * 4, nullptr);
    // 0x00RRGGBB words, little-endian: BGRX bytes.
    CGImageRef image = CGImageCreate(bitmap->width, bitmap->height, 8, 32, bitmap->width * 4, space,
                                     kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst, provider, nullptr,
                                     false, kCGRenderingIntentDefault);
    CGContextSaveGState(context);
    CGContextTranslateCTM(context, 0, bitmap->height);  // the view is flipped, the image is not
    CGContextScaleCTM(context, 1, -1);
    CGContextDrawImage(context, CGRectMake(0, 0, bitmap->width, bitmap->height), image);
    CGContextRestoreGState(context);
    CGImageRelease(image);
    CGDataProviderRelease(provider);
    CGColorSpaceRelease(space);
}

- (void)dealloc {
    [tracking release];
    [super dealloc];
}

- (void)updateTrackingAreas {
    if (tracking) {
        [self removeTrackingArea:tracking];
        [tracking release];
    }
    tracking = [[NSTrackingArea alloc]
        initWithRect:NSZeroRect
             options:NSTrackingMouseMoved | NSTrackingActiveAlways | NSTrackingInVisibleRect
               owner:self
            userInfo:nil];
    [self addTrackingArea:tracking];
    [super updateTrackingAreas];
}

- (void)moveTo:(NSEvent*)event {
    const NSPoint point = [self convertPoint:event.locationInWindow fromView:nil];
    if (impl && impl->ui) impl->ui->mouse_move(float(point.x), float(point.y));
}

- (void)button:(int)button down:(bool)down event:(NSEvent*)event {
    if (down) [self.window makeFirstResponder:self];
    [self moveTo:event];
    if (impl && impl->ui) impl->ui->mouse_button(button, down);
}

- (void)mouseMoved:(NSEvent*)event { [self moveTo:event]; }
- (void)mouseDragged:(NSEvent*)event { [self moveTo:event]; }
- (void)rightMouseDragged:(NSEvent*)event { [self moveTo:event]; }
- (void)mouseDown:(NSEvent*)event { [self button:0 down:true event:event]; }
- (void)mouseUp:(NSEvent*)event { [self button:0 down:false event:event]; }
- (void)rightMouseDown:(NSEvent*)event { [self button:1 down:true event:event]; }
- (void)rightMouseUp:(NSEvent*)event { [self button:1 down:false event:event]; }
- (void)otherMouseDown:(NSEvent*)event { [self button:2 down:true event:event]; }
- (void)otherMouseUp:(NSEvent*)event { [self button:2 down:false event:event]; }

- (void)scrollWheel:(NSEvent*)event {
    const double steps = event.hasPreciseScrollingDeltas ? event.scrollingDeltaY / 10.0 : event.scrollingDeltaY;
    if (impl && impl->ui) impl->ui->wheel(float(steps));
}

- (void)modifiers:(NSEvent*)event {
    const NSEventModifierFlags flags = event.modifierFlags;
    if (impl && impl->ui)
        impl->ui->modifiers(flags & NSEventModifierFlagControl, flags & NSEventModifierFlagShift,
                            flags & NSEventModifierFlagOption, flags & NSEventModifierFlagCommand);
}

- (void)flagsChanged:(NSEvent*)event { [self modifiers:event]; }

- (void)keyDown:(NSEvent*)event {
    if (!impl || !impl->ui) return;
    [self modifiers:event];
    impl->ui->key(editor_key(event.keyCode), true);
    const NSEventModifierFlags flags = event.modifierFlags;
    if (flags & (NSEventModifierFlagCommand | NSEventModifierFlagControl)) return;
    NSString* characters = event.characters;
    for (NSUInteger i = 0; i < characters.length; ++i) {
        const unichar c = [characters characterAtIndex:i];
        if (c >= 32 && c != 127 && !(c >= 0xF700 && c <= 0xF8FF)) impl->ui->text(c);  // not function keys
    }
}

- (void)keyUp:(NSEvent*)event {
    if (!impl || !impl->ui) return;
    [self modifiers:event];
    impl->ui->key(editor_key(event.keyCode), false);
}

// Cmd+C, Cmd+V, Cmd+Z, Cmd+Enter: for the editor, before the host's menus take them.
- (BOOL)performKeyEquivalent:(NSEvent*)event {
    if (self.window.firstResponder != self || !(event.modifierFlags & NSEventModifierFlagCommand)) return NO;
    if (event.type == NSEventTypeKeyDown) {
        [self keyDown:event];
        [self keyUp:event];
    }
    return YES;
}

- (BOOL)becomeFirstResponder {
    if (impl && impl->ui) impl->ui->focus(true);
    return YES;
}

- (BOOL)resignFirstResponder {
    if (impl && impl->ui) impl->ui->focus(false);
    return YES;
}

@end

namespace autoreaper {

const char* PlatformWindow::api() { return CLAP_WINDOW_API_COCOA; }

PlatformWindow::PlatformWindow() : impl_(new Impl) {}

PlatformWindow::~PlatformWindow() {
    if (impl_->view) {
        static_cast<AUTOREAPER_FAUST_VIEW*>(impl_->view)->impl = nullptr;
        [impl_->view removeFromSuperview];
        [impl_->view release];
    }
    delete impl_;
}

void PlatformWindow::set_ui(Ui* ui) { impl_->ui = ui; }

bool PlatformWindow::attach(void* parent, int width, int height) {
    if (impl_->view) {
        [impl_->view removeFromSuperview];
        [impl_->view release];
    }
    AUTOREAPER_FAUST_VIEW* view = [[AUTOREAPER_FAUST_VIEW alloc] initWithFrame:NSMakeRect(0, 0, width, height)];
    view->impl = impl_;
    impl_->view = view;
    [static_cast<NSView*>(parent) addSubview:view];
    return true;
}

void PlatformWindow::resize(int width, int height) {
    if (impl_->view) [impl_->view setFrameSize:NSMakeSize(width, height)];
}

void PlatformWindow::show(bool visible) {
    if (impl_->view) [impl_->view setHidden:!visible];
}

void PlatformWindow::pump() {}

void PlatformWindow::present(const Bitmap& bitmap) {
    impl_->bitmap = &bitmap;
    if (impl_->view) [impl_->view setNeedsDisplay:YES];
}

std::string PlatformWindow::clipboard_get() {
    NSString* text = [[NSPasteboard generalPasteboard] stringForType:NSPasteboardTypeString];
    return text ? std::string(text.UTF8String) : std::string();
}

void PlatformWindow::clipboard_set(const std::string& text) {
    NSPasteboard* board = [NSPasteboard generalPasteboard];
    [board clearContents];
    [board setString:[NSString stringWithUTF8String:text.c_str()] forType:NSPasteboardTypeString];
}

}  // namespace autoreaper
