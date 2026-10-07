#!/usr/bin/env python3
"""Run the production diagnostic renderer with memory-backed frames and sanitizers."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]


def without_includes(path):
    return '\n'.join(line for line in path.read_text().splitlines()
                     if not line.startswith('#include'))


stubs = r'''
#include <cassert>
#include <climits>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <string>
#include <vector>
using u8 = uint8_t;
using u16 = uint16_t;
using u32 = uint32_t;
using s64 = int64_t;
#define JKR_NEW new
#define ARRAY_SIZEU(array) (sizeof(array) / sizeof((array)[0]))
#define ALIGN_NEXT(value, alignment) (((value) + (alignment) - 1) & ~((alignment) - 1))
#define U16_ADD_2(value, amount) ((value) = u16((value) + (amount)))
#define UNUSED(value) ((void)(value))
namespace JUtility {
struct TColor { u8 r, g, b, a; TColor(u8 r=0, u8 g=0, u8 b=0, u8 a=0): r(r),g(g),b(b),a(a) {} };
}
static size_t flushed_bytes;
static void DCStoreRange(void*, size_t bytes) { flushed_bytes = bytes; }
'''

checks = r'''
int main() {
    JUTDirectPrint* renderer = JUTDirectPrint::start();
    const std::string long_text(4096, 'A');
    // An oversized format result must render exactly the stored prefix. Compare
    // actual pixels, including clear=true and clear=false paths.
    for (bool clear : {false, true}) {
        std::vector<u16> actual(320 * 240, 0xbeef), expected = actual;
        auto print = [&](const char* text) {
            if (clear) renderer->print(8, 8, "%s", text);
            else renderer->drawString_f(8, 8, "%s", text);
        };
        renderer->changeFrameBuffer(actual.data(), 320, 240);
        print(long_text.c_str());
        assert(flushed_bytes == actual.size() * sizeof(u16));
        renderer->changeFrameBuffer(expected.data(), 320, 240);
        print(std::string(255, 'A').c_str());
        assert(actual == expected);
    }
    for (int width : {1, 16, 320, 400, 640}) {
        for (int height : {1, 14, 240, 300, 480}) {
            const int stride = (width + 15) & ~15;
            std::vector<u16> pixels(stride * height, 0xbeef);
            renderer->changeFrameBuffer(pixels.data(), width, height);
            // Edge coordinates, negative rectangles, and huge off-screen values.
            for (int pos : {0, 1, width - 1, height - 1, 65535}) {
                renderer->print(pos, pos, "%s", long_text.c_str());
                renderer->drawString_f(pos, pos, "A\tB\nC");
            }
            renderer->erase(-10, -10, 20, 20);
            renderer->erase(INT_MIN, INT_MIN, INT_MAX, INT_MAX);
            renderer->erase(INT_MAX, INT_MAX, INT_MAX, INT_MAX);
            renderer->erase(0, 0, -1, -1);
            for (int pos : {INT_MIN, -1, INT_MAX}) renderer->drawChar(pos, pos, 10);
            for (int ch : {INT_MIN, -1, 45, 99, 155, INT_MAX}) renderer->drawChar(0, 0, ch);
        }
    }
    // In-bounds glyphs and clear rectangles must keep their existing output.
    std::vector<u16> pixels(320 * 240, 0xbeef);
    renderer->changeFrameBuffer(pixels.data(), 320, 240);
    renderer->erase(4, 4, 2, 2);
    for (int y = 0; y < 240; ++y) {
        for (int x = 0; x < 320; ++x) {
            assert(pixels[y * 320 + x] == (x >= 4 && x < 6 && y >= 4 && y < 6 ? 0x1080 : 0xbeef));
        }
    }
    auto expected = pixels;
    renderer->drawString_f(8, 8, "A");
    assert(pixels != expected);
    auto actual = pixels;
    renderer->changeFrameBuffer(expected.data(), 320, 240);
    renderer->drawChar(8, 8, 10); // Production ASCII table maps A to glyph 10.
    assert(actual == expected);
    // Zero-sized or unrepresentable stride must disable the framebuffer.
    for (int width : {0, 65521, 65535}) {
        renderer->changeFrameBuffer(pixels.data(), width, 240);
        assert(!renderer->isActive() && renderer->getFrameBuffer() == nullptr);
        renderer->print(0, 0, "%s", long_text.c_str());
        renderer->erase(0, 0, INT_MAX, INT_MAX);
        renderer->drawChar(0, 0, 10);
    }
    renderer->changeFrameBuffer(pixels.data(), 320, 0);
    assert(!renderer->isActive());
    renderer->changeFrameBuffer(nullptr, 320, 240);
    assert(!renderer->isActive());
    puts("Diagnostic renderer: sanitizer and pixel regression checks passed");
}
'''

header = without_includes(root / 'libs/JSystem/include/JSystem/JUtility/JUTDirectPrint.h')
source = without_includes(root / 'libs/JSystem/src/JUtility/JUTDirectPrint.cpp')
with tempfile.TemporaryDirectory(prefix='dusklight-direct-print-') as directory:
    cpp = Path(directory) / 'direct_print.cpp'
    binary = Path(directory) / 'direct_print'
    cpp.write_text(stubs + header + '\n' + source + checks)
    subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17', '-g',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=30)
