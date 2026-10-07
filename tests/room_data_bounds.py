#!/usr/bin/env python3
"""Exercise the production room-loader functions without game assets.

Compile their source with inert resource/heap dependencies and disabled debug
assertions, so the tests cover the release-build bounds checks.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'src/d/d_stage.cpp').read_text()


def function(signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


stubs = r'''
#include <cassert>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
using u8 = uint8_t;
using s8 = int8_t;
using u32 = uint32_t;
#define JUT_ASSERT(...) ((void)0)
#define OS_REPORT(...) ((void)0)
#define JKR_NEW_ARRAY_ARGS(type, count, ...) new type[count]
#define JKR_DELETE_ARRAY(pointer) delete[] pointer
#define SAFE_SPRINTF(buffer, ...) snprintf(buffer, sizeof(buffer), __VA_ARGS__)
#define ARRAY_SIZEU(array) (sizeof(array) / sizeof((array)[0]))
static unsigned heap_allocations;
struct Heap {
    void* alloc(u32 size, int) { ++heap_allocations; return new char[size]; }
    void free(void* p) { delete[] static_cast<char*>(p); }
};
static Heap heap;
static Heap* mDoExt_getArchiveHeap() { return &heap; }
struct Archive {
    u32 readResource(void*, u32 size, const char*) { return size; }
};
static Archive archive;
static Archive* dComIfGp_getFieldMapArchive2() { return &archive; }
static u32 dLib_getExpandSizeFromAramArchive(Archive*, const char*) { return 1; }
static const char* dComIfGp_getStartStageName() { return "test"; }
class dStage_roomControl_c {
public:
    class roomDzs_c {
    public:
        u8 m_num = 0;
        void** m_dzs = nullptr;
        void create(u8);
        void remove();
        void* add(u8, u8);
    };
    static roomDzs_c rooms;
    static void createRoomDzs(u8 n) { rooms.create(n); }
    static void* addRoomDzs(u8 i, u8 room) { return rooms.add(i, room); }
};
dStage_roomControl_c::roomDzs_c dStage_roomControl_c::rooms;
struct dStage_Mult_info { u8 mRoomNo; };
struct dStage_Multi_c { int num; dStage_Mult_info* m_entries; };
struct dStage_dt_c {
    int visits = 0;
    int last_room = -1;
    void setRoomNo(u8 room) { ++visits; last_room = room; }
    int getStagInfo() { return 0; }
};
static dStage_dt_c stage;
static dStage_dt_c* dComIfGp_getStage() { return &stage; }
static int dStage_stagInfo_GetUpButton(int) { return 0; }
struct FuncTable { const char* name; void (*callback)(); };
static void dStage_stageKeepTresureInit() {}
static void dStage_filiInfo2Init() {}
static void dStage_mapPathInitCommonLayer() {}
static void dStage_RoomKeepDoorInit() {}
static void dStage_mapPathInit() {}
static unsigned fallback_reads;
static void* dComIfG_getOldStageRes(const char*) { ++fallback_reads; return nullptr; }
static void* dComIfG_getStageRes(const char*) { ++fallback_reads; return nullptr; }
static void dStage_dt_c_offsetToPtr(void*) {}
static void dStage_dt_c_decode(void*, dStage_dt_c*, FuncTable*, unsigned) {}
static void dStage_setLayerTagName(FuncTable*, unsigned, int) {}
struct dComIfG_play_c { static int getLayerNo(int) { return 0; } };
'''

checks = r'''
int main() {
    auto& rooms = dStage_roomControl_c::rooms;
    for (u8 count : {u8(0), u8(64), u8(255)}) {
        rooms.create(count);
        assert(rooms.m_num == 0 && rooms.m_dzs == nullptr);
    }
    assert(rooms.add(0, 0) == nullptr);
    rooms.create(1);
    void** original = rooms.m_dzs;
    rooms.create(2);
    assert(rooms.m_num == 1 && rooms.m_dzs == original);
    assert(rooms.add(1, 0) == nullptr);
    assert(rooms.add(255, 0) == nullptr);
    assert(heap_allocations == 0);
    assert(rooms.add(0, 0) != nullptr);
    assert(heap_allocations == 1);
    rooms.remove();

    dStage_Mult_info entries[63]{};
    for (int i = 0; i < 63; ++i) entries[i].mRoomNo = static_cast<u8>(i);
    for (int count : {INT_MIN, -1, 0, 64, 127, 128, 255, 256, INT_MAX}) {
        dStage_Multi_c multi{count, entries};
        readMult(&stage, &multi, false);
        assert(stage.visits == 0 && rooms.m_num == 0);
        assert(heap_allocations == 1 && fallback_reads == 0);
    }
    readMult(&stage, nullptr, false);
    dStage_Multi_c missing{1, nullptr};
    readMult(&stage, &missing, false);
    assert(stage.visits == 0 && rooms.m_num == 0);

    dStage_Multi_c valid{63, entries};
    readMult(&stage, &valid, false);
    assert(stage.visits == 63 && stage.last_room == 62);
    assert(rooms.m_num == 63 && heap_allocations == 64);
    rooms.remove();

    // A retained room table can be shorter than the next metadata list.
    // Missing slots must fall back to resource lookup without touching memory.
    rooms.create(1);
    dStage_Multi_c longer{2, entries};
    readMult(&stage, &longer, false);
    readMult(&stage, &longer, true);
    assert(fallback_reads == 2);
    rooms.remove();
    puts("Room metadata bounds: release-mode sanitizer checks passed");
}
'''

functions = '\n\n'.join(function(signature) for signature in [
    'void dStage_roomControl_c::roomDzs_c::create(',
    'void dStage_roomControl_c::roomDzs_c::remove(',
    'void* dStage_roomControl_c::roomDzs_c::add(',
    'static void readMult(',
])
with tempfile.TemporaryDirectory(prefix='dusklight-room-bounds-') as directory:
    cpp = Path(directory) / 'room_bounds.cpp'
    binary = Path(directory) / 'room_bounds'
    cpp.write_text('#include <initializer_list>\n' + stubs + functions + checks)
    subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17', '-g',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=30)
