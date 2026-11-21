#!/usr/bin/env python3
# KowSU + susfs4oki 类型冲突后处理 v3（头文件+源文件+do-while分号）
import sys, os, glob

candidates = [
    "feature/sucompat.c",
    "../KernelSU/feature/sucompat.c",
    "drivers/kernelsu/feature/sucompat.c",
    "../common/drivers/kernelsu/feature/sucompat.c",
]
p = None
for c in candidates:
    if os.path.isfile(c):
        p = c
        break
if not p:
    hits = glob.glob("**/kernelsu/feature/sucompat.c", recursive=True)
    if hits:
        p = hits[0]
    else:
        print("FATAL: sucompat.c not found in", os.getcwd())
        sys.exit(1)

base = os.path.dirname(p)  # feature/
print("patching:", p)
src = open(p).read()
orig = src

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

# 头文件：extern bool 声明改 static_key
for hdr in [os.path.join(base, "sucompat.h"), os.path.join(base, "..", "include", "ksu.h")]:
    hdr = os.path.normpath(hdr)
    if os.path.isfile(hdr):
        h = open(hdr).read()
        h2 = h.replace(
            "extern bool ksu_su_compat_enabled;",
            "extern struct static_key_true ksu_su_compat_enabled;")
        if h2 != h:
            open(hdr, "w").write(h2)
            print("patched:", hdr)

changed = src != orig
print("sucompat.c patched:", "CHANGED" if changed else "NO-OP")
if "extern bool ksu_su_compat_enabled" in src or "bool ksu_su_compat_enabled __read_mostly" in src:
    print("FATAL: bool ksu_su_compat_enabled still present")
    sys.exit(1)
print("verification passed")
