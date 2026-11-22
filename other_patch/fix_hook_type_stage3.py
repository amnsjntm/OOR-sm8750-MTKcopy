#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_hook_type_stage3.py - minimal incremental edit: add HOOK_TYPE ioctl to dispatch.c
Run AFTER 10_enable + fix_bridge_kowsu_v6 (preserves all susfs adaptations).
Fail-fast on any anchor miss (no silent no-op).
"""
import sys
from pathlib import Path

MARK = "fix_hook_type_stage3"

def die(msg):
    print("FATAL[" + MARK + "]: " + msg)
    sys.exit(1)

def find_dispatch():
    cands = [
        Path("KernelSU/kernel/supercall/dispatch.c"),
        Path("kernel_workspace/KernelSU/kernel/supercall/dispatch.c"),
        Path("../KernelSU/kernel/supercall/dispatch.c"),
        Path("common/drivers/kernelsu/supercall/dispatch.c"),
        Path("../common/drivers/kernelsu/supercall/dispatch.c"),
        Path("common/drivers/kernelsu/kernel/supercall/dispatch.c"),
    ]
    for c in cands:
        if c.is_file():
            return c
    die("dispatch.c not found in any known location")

p = find_dispatch()
t = p.read_text(encoding="utf-8")
orig = t

HANDLER = '''// 101. HOOK_TYPE - report hook implementation to manager (BakaSU private ioctl, stage3)
static int do_get_hook_type(void __user *arg)
{
    struct ksu_hook_type_cmd cmd = { 0 };
#if defined(CONFIG_KSU_TRACEPOINT_HOOK)
    const char *type = "Tracepoint Syscall Redirect";
#elif defined(CONFIG_KSU_MANUAL_HOOK)
    const char *type = "Manual";
#else
    /* susfs 2.4.0 line: inline syscall hooks */
    const char *type = "Inline";
#endif

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 13, 0)
    strscpy(cmd.hook_type, type, sizeof(cmd.hook_type));
#else
    strlcpy(cmd.hook_type, type, sizeof(cmd.hook_type));
#endif

    if (copy_to_user(arg, &cmd, sizeof(cmd))) {
        pr_err("get_hook_type: copy_to_user failed\\n");
        return -EFAULT;
    }

    return 0;
}

'''
ENTRY = '''    {
        .cmd = KSU_IOCTL_HOOK_TYPE,
        .name = "HOOK_TYPE",
        .handler = do_get_hook_type,
        .perm_check = always_allow
    },
'''

# idempotency
if "do_get_hook_type" in t:
    print("[OK] dispatch.c already patched (idempotent skip)")
    sys.exit(0)

# A1: handler before do_report_event
anchor1 = "static int do_report_event(void __user *arg)"
if t.count(anchor1) != 1:
    die("anchor1 (do_report_event) count=" + str(t.count(anchor1)))
t = t.replace(anchor1, HANDLER + anchor1)

# A2: table entry before sentinel
anchor2 = """    {
        .cmd = 0,
        .name = NULL,
        .handler = NULL,
        .perm_check = NULL
    } // Sentinel"""
if t.count(anchor2) != 1:
    die("anchor2 (sentinel) count=" + str(t.count(anchor2)))
t = t.replace(anchor2, ENTRY + anchor2)

# verify
if t.count("do_get_hook_type") != 2:  # definition + table ref
    die("post-verify: do_get_hook_type count=" + str(t.count("do_get_hook_type")))
if t.count('.name = "HOOK_TYPE"') != 1:
    die("post-verify: HOOK_TYPE entry count")
bal = t.count("{") - t.count("}")
if bal != orig.count("{") - orig.count("}"):
    die("brace balance drift: " + str(bal))

p.write_text(t, encoding="utf-8")
print("[OK] dispatch.c: handler + table entry inserted (" + str(p) + ")")
print("[OK] do_get_hook_type x2, HOOK_TYPE entry x1, brace balance preserved")
