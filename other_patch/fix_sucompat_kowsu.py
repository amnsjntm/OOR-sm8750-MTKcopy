#!/usr/bin/env python3
# KowSU + susfs4oki 类型冲突后处理（路径自动定位版）
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
    "do { if (enable) static_branch_enable(&ksu_su_compat_enabled); else static_branch_disable(&ksu_su_compat_enabled); } while (0)")
src = src.replace(
    "argv = (const char __user *const *)argv_user;",
    "struct user_arg_ptr uap = *(struct user_arg_ptr *)argv_user; argv = (const char __user *const *)&uap;")

changed = src != orig
open(p, "w").write(src)
print("sucompat.c patched:", "CHANGED" if changed else "NO-OP")
if "bool ksu_su_compat_enabled" in src:
    print("FATAL: bool ksu_su_compat_enabled still present")
    sys.exit(1)
print("verification passed")
