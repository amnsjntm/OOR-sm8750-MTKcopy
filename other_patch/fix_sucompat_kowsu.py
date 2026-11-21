#!/usr/bin/env python3
# KowSU + susfs4oki 类型冲突后处理 v4（完整版：sucompat.c/h + syscall_event_bridge.c）
import sys, os, glob

def find_file(rel_candidates, name):
    for c in rel_candidates:
        if os.path.isfile(c):
            return c
    hits = glob.glob("**/" + name, recursive=True)
    if hits:
        return hits[0]
    return None

# ── sucompat.c ──
p = find_file([
    "feature/sucompat.c",
    "../KernelSU/feature/sucompat.c",
    "drivers/kernelsu/feature/sucompat.c",
    "../common/drivers/kernelsu/feature/sucompat.c",
], "sucompat.c")
if not p:
    print("FATAL: sucompat.c not found")
    sys.exit(1)
base = os.path.dirname(p)
print("patching:", p)
src = open(p).read()

src = src.replace(
    "bool ksu_su_compat_enabled __read_mostly = true;",
    "DEFINE_STATIC_KEY_TRUE(ksu_su_compat_enabled);")
src = src.replace(
    "*value = ksu_su_compat_enabled ? 1 : 0;",
    "*value = static_key_enabled(&ksu_su_compat_enabled) ? 1 : 0;")
src = src.replace(
    "ksu_su_compat_enabled = enable;",
    "do { if (enable) static_branch_enable(&ksu_su_compat_enabled); else static_branch_disable(&ksu_su_compat_enabled); } while (0);")
src = src.replace(
    "argv = (const char __user *const *)argv_user;",
    "struct user_arg_ptr uap = *(struct user_arg_ptr *)argv_user; argv = (const char __user *const *)&uap;")
open(p, "w").write(src)

# ── sucompat.h: extern bool → extern static_key ──
h = os.path.join(base, "sucompat.h")
if os.path.isfile(h):
    ht = open(h).read()
    ht2 = ht.replace(
        "extern bool ksu_su_compat_enabled;",
        "extern struct static_key_true ksu_su_compat_enabled;")
    if ht2 != ht:
        open(h, "w").write(ht2)
        print("patched:", h)

# ── syscall_event_bridge.c: bool 读取 → static_key 读取 ──
b = find_file([
    "../KernelSU/hook/syscall_event_bridge.c",
    "hook/syscall_event_bridge.c",
    "../common/drivers/kernelsu/hook/syscall_event_bridge.c",
], "syscall_event_bridge.c")
if b:
    bt = open(b).read()
    bt2 = bt.replace(
        "!ksu_su_compat_enabled",
        "!static_key_enabled(&ksu_su_compat_enabled)")
    bt2 = bt2.replace(
        "ksu_su_compat_enabled)",
        "static_key_enabled(&ksu_su_compat_enabled))")
    if bt2 != bt:
        open(b, "w").write(bt2)
        print("patched:", b)

# ── 校验 ──
ok = True
for f in [p, b]:
    if f and os.path.isfile(f):
        t = open(f).read()
        if "bool ksu_su_compat_enabled" in t or "extern bool ksu_su_compat_enabled" in t:
            print("FATAL: bool ksu_su_compat_enabled residue in", f)
            ok = False
if ok:
    print("verification passed")
else:
    sys.exit(1)
