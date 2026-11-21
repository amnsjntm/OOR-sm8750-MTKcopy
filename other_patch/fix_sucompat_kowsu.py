#!/usr/bin/env python3
# KowSU + susfs4oki 类型冲突后处理
# 在 10_enable_susfs_for_ksu.patch 应用后执行
import sys

p = "feature/sucompat.c"  # 相对 drivers/kernelsu/
src = open(p).read()
orig = src

# 1. bool 声明 → static_key 定义（若 10_enable 已改则跳过）
src = src.replace(
    "bool ksu_su_compat_enabled __read_mostly = true;",
    "DEFINE_STATIC_KEY_TRUE(ksu_su_compat_enabled);")

# 2. feature_get 读取点
src = src.replace(
    "*value = ksu_su_compat_enabled ? 1 : 0;",
    "*value = static_key_enabled(&ksu_su_compat_enabled) ? 1 : 0;")

# 3. feature_set 赋值点
src = src.replace(
    "ksu_su_compat_enabled = enable;",
    "do { if (enable) static_branch_enable(&ksu_su_compat_enabled); else static_branch_disable(&ksu_su_compat_enabled); } while (0)")

# 4. user_arg_ptr 强转修正
src = src.replace(
    "argv = (const char __user *const *)argv_user;",
    "struct user_arg_ptr uap = *(struct user_arg_ptr *)argv_user; argv = (const char __user *const *)&uap;")

changed = src != orig
open(p, "w").write(src)
print("sucompat.c patched:", "CHANGED" if changed else "NO-OP")

# 校验：不允许残留 bool 类型的 ksu_su_compat_enabled
if "bool ksu_su_compat_enabled" in src:
    print("FATAL: bool ksu_su_compat_enabled still present")
    sys.exit(1)
print("verification passed")
