#!/usr/bin/env python3
"""Sanitize actual stream-loader functions with in-memory DVD/ARAM and no debug assertions."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'libs/JSystem/src/JAudio2/JASAramStream.cpp').read_text()
header = (root / 'libs/JSystem/include/JSystem/JAudio2/JASAramStream.h').read_text()


def extract(text, signature):
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


stubs = r'''
#include <cassert>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
using u8 = uint8_t; using u16 = uint16_t; using u32 = uint32_t; using u64 = uint64_t;
using s16 = int16_t; using s32 = int32_t; using f32 = float;
#define TARGET_PC 1
// Fixtures hold decoded values; the production Header and BlockHeader layouts
// are used below. This harness does not test the endian wrapper itself.
#define BE(type) type
#define JUT_ASSERT(...) ((void)0)
#define JUT_WARN(...) ((void)0)
#define STREAM_FORMAT_ADPCM4 0
#define CMD_LOOP_END_LOADED 5
#define OS_MESSAGE_BLOCK 1
#define EXPAND_SWITCH_UNKNOWN0 0
using OSMessage = void*;
struct JASCriticalSection {};
struct DVDFileInfo { u32 length = 0; };
static std::vector<u8> dvd, aram;
static unsigned reads, transfers, tasks;
static bool short_read, hasErrored;
static constexpr u32 block_bytes = 288;
static u8 read_buffer[(block_bytes + 32) * 2];
static int DVDReadPrio(DVDFileInfo*, void* dst, s32 size, s32 offset, int) {
    ++reads;
    assert(size >= 0 && offset >= 0);
    assert(size_t(size) <= sizeof(read_buffer));
    if (size_t(offset) > dvd.size()) return -1;
    size_t count = dvd.size() - offset;
    if (count > size_t(size)) count = size;
    if (short_read && count) --count;
    memcpy(dst, dvd.data() + offset, count);
    return int(count);
}
static bool JKRMainRamToAram(void* src, u32 offset, u32 size, int, int, void*, int, void*) {
    assert(offset <= aram.size() && size <= aram.size() - offset);
    memcpy(aram.data() + offset, src, size);
    ++transfers;
    return true;
}
static void OSSendMessage(int*, OSMessage, int) {}
struct LoadThread {
    bool sendCmdMsg(void (*)(void*), void*, unsigned) { ++tasks; return true; }
};
static LoadThread thread;
class JASAramStream {
public:
    static constexpr int CHANNEL_MAX = 6;
    @HEADER@;
    @BLOCK_HEADER@;
    struct TaskData { JASAramStream* stream; u32 param0; int param1; };
    static inline u8* sReadBuffer = read_buffer;
    static inline u32 sBlockSize = block_bytes, sChannelMax = 2;
    static inline LoadThread* sLoadThread = &thread;
    static void firstLoadTask(void*) {}
    u32 getBlockSamples() const;
    bool headerLoad(u32, int);
    bool load();
    DVDFileInfo mDvdFileInfo;
    u8 mIsCancelled = 0;
    u16 mFormat = 0, mChannelNum = 0;
    u32 mSampleRate = 0, mLoopStart = 0, mLoopEnd = 0;
    bool mLoop = false;
    float mVolume = 0;
    u32 mPendingLoadTasks = 1, mBlock = 0, mAramBlocksPerChannel = 0, mBufCount = 0, mRingEndIndex = 0;
    int mBlockRingIndex = 0, mAramAddress = 0, mLoadCommandQueue = 0;
    s16 mpLasts[CHANNEL_MAX]{}, mpPenults[CHANNEL_MAX]{};
};
'''
stubs = stubs.replace('@HEADER@', extract(header, 'struct Header {'))
stubs = stubs.replace('@BLOCK_HEADER@', extract(header, 'struct BlockHeader {'))

checks = r'''
using Stream = JASAramStream;
static_assert(sizeof(Stream::Header) == 64 && sizeof(Stream::BlockHeader) == 32);
static Stream::Header valid_header;

static Stream fixture(int format = 0, int channels = 2, bool loop = false) {
    hasErrored = short_read = false;
    reads = transfers = tasks = 0;
    Stream::sReadBuffer = read_buffer;
    Stream::sBlockSize = block_bytes;
    Stream::sChannelMax = 2;
    Stream stream;
    const size_t stride = size_t(block_bytes) * channels + sizeof(Stream::BlockHeader);
    dvd.assign(sizeof(Stream::Header) + 3 * stride, 0);
    aram.assign(size_t(block_bytes) * channels * 4, 0);
    valid_header = {};
    valid_header.tag = 'STRM'; valid_header.format = format; valid_header.bits = 16;
    valid_header.channels = channels; valid_header.block_size = block_bytes;
    valid_header.mSampleRate = 32000; valid_header.mVolume = 127;
    valid_header.loop = loop; valid_header.loop_start = 0;
    valid_header.loop_end = 3 * (format ? block_bytes / 2 : (block_bytes << 4) / 9);
    memcpy(dvd.data(), &valid_header, sizeof(valid_header));
    for (int block = 0; block < 3; ++block) {
        Stream::BlockHeader bh{}; bh.tag = 'BLCK'; bh.mSize = block_bytes;
        size_t offset = sizeof(Stream::Header) + block * stride;
        memcpy(dvd.data() + offset, &bh, sizeof(bh));
        memset(dvd.data() + offset + sizeof(bh), block + 1, size_t(block_bytes) * channels);
    }
    stream.mDvdFileInfo.length = dvd.size();
    return stream;
}

int main() {
    // Both formats, channel counts and loop modes must still load valid blocks.
    for (int format : {0, 1}) for (int channels : {1, 2}) for (bool loop : {false, true}) {
        auto stream = fixture(format, channels, loop);
        assert(stream.headerLoad(aram.size(), -1));
        assert(tasks == 1 && stream.mChannelNum == channels && stream.mVolume == 1.0f);
        for (int i = 0; i < (loop ? 6 : 3); ++i) {
            u32 ring = stream.mBlockRingIndex;
            stream.mPendingLoadTasks = 1;
            assert(stream.load());
            for (int ch = 0; ch < channels; ++ch) {
                size_t offset = size_t(ring) * block_bytes + size_t(block_bytes) * stream.mAramBlocksPerChannel * ch;
                for (unsigned j = 0; j < block_bytes; ++j) assert(aram[offset + j] == i % 3 + 1);
            }
        }
        assert(transfers == unsigned(channels * (loop ? 6 : 3)) && !hasErrored);
    }
    for (int scenario = 0; scenario < 12; ++scenario) {
        auto stream = fixture();
        auto h = valid_header;
        switch (scenario) {
        case 0: h.channels = 0; break;
        case 1: h.channels = 3; break;
        case 2: h.channels = UINT16_MAX; break;
        case 3: h.tag = 0; break;
        case 4: h.format = 2; break;
        case 5: h.bits = 8; break;
        case 6: h.block_size = block_bytes + 32; break;
        case 7: h.loop_end = 0; break;
        case 8: h.loop_end = -1; break;
        case 9: h.loop = 1; h.loop_start = -1; break;
        case 10: h.loop = 1; h.loop_start = h.loop_end; break;
        case 11: h.loop = 1; h.loop_start = h.loop_end - 1; break;
        }
        memcpy(dvd.data(), &h, sizeof(h));
        assert(!stream.headerLoad(aram.size(), -1));
        assert(hasErrored && tasks == 0 && transfers == 0);
    }
    for (size_t bytes = 0; bytes < sizeof(Stream::Header); ++bytes) {
        auto stream = fixture(); dvd.resize(bytes); stream.mDvdFileInfo.length = bytes;
        assert(!stream.headerLoad(aram.size(), -1) && hasErrored && reads == 0);
    }
    for (u32 size : {0u, block_bytes * 2, block_bytes * 4 - 1}) {
        auto stream = fixture(); assert(!stream.headerLoad(size, -1) && hasErrored);
    }
    {
        auto stream = fixture(); short_read = true;
        assert(!stream.headerLoad(aram.size(), -1) && hasErrored);
    }
    for (int scenario = 0; scenario < 9; ++scenario) {
        auto stream = fixture(); assert(stream.headerLoad(aram.size(), -1));
        auto* bh = reinterpret_cast<Stream::BlockHeader*>(dvd.data() + sizeof(Stream::Header));
        switch (scenario) {
        case 0: bh->tag = 0; break;
        case 1: bh->mSize = 0; break;
        case 2: bh->mSize = block_bytes + 32; break;
        case 3: bh->mSize = UINT32_MAX; break;
        case 4: short_read = true; break;
        case 5: stream.mDvdFileInfo.length = sizeof(Stream::Header) - 1; break;
        case 6: stream.mDvdFileInfo.length = sizeof(Stream::Header) + sizeof(Stream::BlockHeader) - 1; break;
        case 7: stream.mBlock = 8000000; stream.mLoopEnd = UINT32_MAX; stream.mDvdFileInfo.length = UINT32_MAX; break;
        case 8: stream.mLoopEnd = 1; stream.mDvdFileInfo.length = UINT32_MAX; break;
        }
        assert(!stream.load() && transfers == 0);
        if (scenario >= 5) assert(reads == 1); // Reject bad ranges before DVD I/O.
    }
    // Last blocks may be shorter, but each channel's declared data must fit.
    for (bool truncate : {false, true}) {
        auto stream = fixture(); assert(stream.headerLoad(aram.size(), -1));
        stream.mLoopEnd = 1;
        auto* bh = reinterpret_cast<Stream::BlockHeader*>(dvd.data() + sizeof(Stream::Header));
        bh->mSize = 32;
        dvd.resize(sizeof(Stream::Header) + sizeof(Stream::BlockHeader) + 64 - int(truncate));
        stream.mDvdFileInfo.length = dvd.size();
        assert(stream.load() == !truncate);
        assert(transfers == (truncate ? 0u : 2u));
    }
    puts("Audio stream: release-mode sanitizer and valid-stream checks passed");
}
'''

functions = '\n\n'.join(extract(source, signature) for signature in [
    'u32 JASAramStream::getBlockSamples() const',
    'bool JASAramStream::headerLoad(',
    'bool JASAramStream::load()',
])
with tempfile.TemporaryDirectory(prefix='dusklight-audio-bounds-') as directory:
    cpp = Path(directory) / 'audio_bounds.cpp'
    binary = Path(directory) / 'audio_bounds'
    cpp.write_text(stubs + functions + checks)
    subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17', '-g',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=30)
