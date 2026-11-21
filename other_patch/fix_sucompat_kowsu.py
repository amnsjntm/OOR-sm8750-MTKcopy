#!/usr/bin/env python3
# KowSU + susfs4oki 类型冲突后处理 v5（bridge按行精确处理）
import sys, os, glob, re

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

# ── sucompat.h ──
h = os.path.join(base, "sucompat.h")
if os.path.isfile(h):
    ht = open(h).read()
    ht2 = ht.replace(
        "extern bool ksu_su_compat_enabled;",
        "extern struct static_key_true ksu_su_compat_enabled;")
    if ht2 != ht:
        open(h, "w").write(ht2)
        print("patched:", h)

# ── syscall_event_bridge.c：逐行处理（避免子串重复替换）──
b = find_file([
    "../KernelSU/hook/syscall_event_bridge.c",
    "hook/syscall_event_bridge.c",
    "../common/drivers/kernelsu/hook/syscall_event_bridge.c",
], "syscall_event_bridge.c")
if b:
    bl = open(b).read().split("\n")
    out = []
    for line in bl:
        # bool 读取（53/61行）：if (!ksu_su_compat_enabled)
        line = re.sub(r"if \(!ksu_su_compat_enabled\)", "if (!static_key_enabled(&ksu_su_compat_enabled))", line)
        # else if 读取（111行）：else if (ksu_su_compat_enabled)
        line = re.sub(r"else if \(ksu_su_compat_enabled\)", "else if (static_key_enabled(&ksu_su_compat_enabled))", line)
        out.append(line)
    open(b, "w").write("\n".join(out))
    print("patched:", b)

# ── 校验 ──
ok = True
for f in [p, b]:
    if f and os.path.isfile(f):
        t = open(f).read()
        # 找裸的 bool 引用（不含 static_key_enabled 包装）
        for mm in re.finditer(r"ksu_su_compat_enabled", t):
            ctx = t[max(0, mm.start()-40):mm.end()+40]
            if "static_key_enabled" not in ctx and "DEFINE_STATIC_KEY_TRUE" not in ctx and "extern struct" not in ctx and "static_branch" not in ctx:
                print("FATAL: unconverted ksu_su_compat_enabled in", f, ":", ctx.replace("\n", " "))
                ok = False
if ok:
    print("verification passed")
else:
    sys.exit(1)
