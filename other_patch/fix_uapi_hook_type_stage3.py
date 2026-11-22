#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_uapi_hook_type_stage3.py - minimal incremental edit: add HOOK_TYPE to uapi/supercall.h
Fail-fast, idempotent.
"""
import sys
from pathlib import Path

MARK = "fix_uapi_hook_type_stage3"

def die(msg):
    print("FATAL[" + MARK + "]: " + msg)
    sys.exit(1)

def find_uapi():
    cands = [
        Path("KernelSU/uapi/supercall.h"),
        Path("kernel_workspace/KernelSU/uapi/supercall.h"),
        Path("../KernelSU/uapi/supercall.h"),
    ]
    for c in cands:
        if c.is_file():
            return c
    die("uapi/supercall.h not found in any known location")

p = find_uapi()
t = p.read_text(encoding="utf-8")

if "KSU_IOCTL_HOOK_TYPE" in t:
    print("[OK] uapi/supercall.h already patched (idempotent skip)")
    sys.exit(0)

anchor = "static const __u32 KSU_IOCTL_LOOKUP_SYMBOLS = _IOW('K', 22, struct ksu_lookup_symbols_cmd);"
if t.count(anchor) != 1:
    die("anchor (LOOKUP_SYMBOLS) count=" + str(t.count(anchor)))

ADD = anchor + '''
/* stage3: manager status fields (BakaSU private ioctl range 100+) */
struct ksu_hook_type_cmd {
    char hook_type[32]; /* Output: hook type string */
};
static const __u32 KSU_IOCTL_HOOK_TYPE = _IOC(_IOC_READ, 'K', 101, 0);'''

t = t.replace(anchor, ADD)

if t.count("KSU_IOCTL_HOOK_TYPE") != 1:
    die("post-verify: KSU_IOCTL_HOOK_TYPE count")
if t.count("struct ksu_hook_type_cmd") != 1:
    die("post-verify: struct count")

p.write_text(t, encoding="utf-8")
print("[OK] uapi/supercall.h: struct + 101 cmd added (" + str(p) + ")")
