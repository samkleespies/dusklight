#!/usr/bin/env python3
"""Apply the production dependency patch and sanitize the actual ELF table parser."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import urllib.request

root = Path(__file__).resolve().parents[1]
url = ('https://raw.githubusercontent.com/getsentry/sentry-native/0.13.6/'
       'vendor/libunwind/src/elfxx.c')
source = urllib.request.urlopen(url, timeout=30).read()
assert hashlib.sha256(source).hexdigest() == 'bbd2dab22f7e857657790b0d240850fb29504d4406977de237c372169398c58f'

# Define both standard ELF layouts so the same regression runs on macOS too.
stubs = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t Elf32_Off;
typedef uint64_t Elf64_Off;
typedef struct {
    unsigned char e_ident[16];
    uint16_t e_type, e_machine;
    uint32_t e_version, e_entry, e_phoff, e_shoff, e_flags;
    uint16_t e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx;
} Elf32_Ehdr;
typedef struct {
    unsigned char e_ident[16];
    uint16_t e_type, e_machine;
    uint32_t e_version;
    uint64_t e_entry, e_phoff, e_shoff;
    uint32_t e_flags;
    uint16_t e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx;
} Elf64_Ehdr;
typedef struct {
    uint32_t sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size;
    uint32_t sh_link, sh_info, sh_addralign, sh_entsize;
} Elf32_Shdr;
typedef struct {
    uint32_t sh_name, sh_type;
    uint64_t sh_flags, sh_addr, sh_offset, sh_size;
    uint32_t sh_link, sh_info;
    uint64_t sh_addralign, sh_entsize;
} Elf64_Shdr;
#define elf_w(name) test_##name
#define Debug(...) ((void)0)
struct elf_image { void *image; size_t size; };
'''

checks = r'''
int main(void) {
    assert(sizeof(Elf32_Ehdr) == 52 && sizeof(Elf32_Shdr) == 40);
    assert(sizeof(Elf64_Ehdr) == 64 && sizeof(Elf64_Shdr) == 64);
    size_t size = sizeof(Elf_W(Ehdr)) + 2 * sizeof(Elf_W(Shdr));
    Elf_W(Ehdr) *header = calloc(1, size);
    assert(header);
    struct elf_image image = {header, size};
    header->e_shoff = sizeof(*header);
    header->e_shnum = 2;
    header->e_shentsize = sizeof(Elf_W(Shdr));
    assert(test_section_table(&image) == (void *)((char *)header + sizeof(*header)));
    image.size--;
    assert(test_section_table(&image) == NULL);
    image.size = size;
    header->e_shoff = size + 1;
    assert(test_section_table(&image) == NULL);
    header->e_shoff = (Elf_W(Off))-1;
    assert(test_section_table(&image) == NULL);
    header->e_shoff = sizeof(*header);
    header->e_shnum = UINT16_MAX;
    header->e_shentsize = UINT16_MAX; // Original int product overflows here.
    assert(test_section_table(&image) == NULL);
    header->e_shnum = 1;
    for (unsigned entry_size = 0; entry_size <= UINT16_MAX; ++entry_size) {
        header->e_shentsize = entry_size;
        assert((test_section_table(&image) != NULL) == (entry_size == sizeof(Elf_W(Shdr))));
    }
    header->e_shentsize = sizeof(Elf_W(Shdr));
    header->e_shoff = size;
    header->e_shnum = 0;
    assert(test_section_table(&image) == (void *)((char *)header + size));
    free(header);
    for (size_t bytes = 1; bytes < sizeof(Elf_W(Ehdr)); ++bytes) {
        image.image = calloc(1, bytes);
        image.size = bytes;
        assert(test_section_table(&image) == NULL);
        free(image.image);
    }
    image.image = NULL;
    image.size = size;
    assert(test_section_table(&image) == NULL);
    puts("ELF section table: sanitizer boundary checks passed");
}
'''

with tempfile.TemporaryDirectory(prefix='dusklight-libunwind-bounds-') as directory:
    temp = Path(directory)
    vendor = temp / 'vendor/libunwind/src/elfxx.c'
    vendor.parent.mkdir(parents=True)
    vendor.write_bytes(source)
    patch_command = ['cmake', f'-DSOURCE_DIR={temp}', '-P', str(root / 'cmake/ApplySentryPatch.cmake')]
    subprocess.run(patch_command, check=True)
    patched = vendor.read_text()
    subprocess.run(patch_command, check=True)  # Reconfiguration must be idempotent.
    assert vendor.read_text() == patched
    start = patched.index('static Elf_W (Shdr)*\nelf_w (section_table)')
    end = patched.index('\nstatic char*', start)
    function = patched[start:end]
    for bits in (32, 64):
        cpp = temp / f'section{bits}.c'
        binary = temp / f'section{bits}'
        cpp.write_text(f'#define Elf_W(type) Elf{bits}_##type\n' + stubs + function + checks)
        subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-g',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)
