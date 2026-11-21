#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_bridge_kowsu_v6.py - KowSU(master) x susfs4oki(oki-android15-6.6) v6 post-processing.

Run AFTER 10_enable_susfs_for_ksu.patch. GNU patch -F3 silently drops failing
hunks (sucompat.c @@-21/@@-54, supercall.c @@-113 on current master), leaving
old pt_regs-signature handlers that break the link. This script:
  F1 Kbuild         retire pt_regs hook subsystem objects (10_enable design)
  F2 init.c         strip hook-manager/lsm calls
  F3 bridge.c       static_key reads (SakiSU-style) + old-sig calls -> syscall table
  F4 fs/Makefile    -DKSU_NO_PREINIT for exec.o when CONFIG_HEADERS_INSTALL=n
  F5 sucompat.c     replace with canonical susfs post-state (CANON_SU) when
                    patch residue detected (idempotent)
  F6 supercall.c    replace with canonical susfs post-state (CANON_SC) when
                    residue detected (idempotent)
  V*                residue verification; any hit -> exit 1
"""
import re
import sys
from pathlib import Path

MARK = "fix_bridge_kowsu_v6"


def die(msg):
    print("FATAL: " + msg)
    sys.exit(1)


def find_ksu_dir():
    cands = [Path("drivers/kernelsu"), Path("common/drivers/kernelsu"),
             Path("../common/drivers/kernelsu"), Path("../drivers/kernelsu"),
             Path("KernelSU/kernel"), Path("../KernelSU/kernel"),
             Path("drivers/kernelsu/KernelSU-master/kernel"),
             Path("drivers/kernelsu/KernelSU/kernel"),
             Path("common/drivers/kernelsu/KernelSU-master/kernel"),
             Path("common/drivers/kernelsu/KernelSU/kernel")]
    for c in cands:
        try:
            if (c / "core" / "init.c").is_file() and (c / "feature" / "sucompat.c").is_file():
                return c.resolve()
        except OSError:
            pass
    for pat in ("*/drivers/kernelsu/KernelSU*/kernel", "*/KernelSU*/kernel"):
        for g in Path.cwd().glob(pat):
            if (g / "core" / "init.c").is_file():
                return g.resolve()
    return None


def read(p):
    return p.read_text(encoding="utf-8")


def write(p, s):
    p.write_text(s, encoding="utf-8", newline="\n")


# ---------------- F1: Kbuild ----------------
OBJ_RE = re.compile(r"^\s*kernelsu-objs\s*\+=\s*hook/(lsm_hook|syscall_event_bridge|syscall_hook_manager|tp_marker|(?:arm64|x86_64|riscv64)/(?:patch_memory|syscall_hook))\.o\s*$")
COND_RE = re.compile(r"^\s*(?:else\s+)?(?:ifeq|ifneq)\s*\(\s*\$\(CONFIG_(?:ARM64|X86_64|RISCV)\)\s*,\s*y\s*\)\s*$")


def fix_kbuild(text):
    lines = [l for l in text.split("\n") if not OBJ_RE.match(l)]
    for i, l in enumerate(lines):
        if "CONFIG_ARM64" in l and COND_RE.match(l):
            depth = 1
            j = i + 1
            ok = True
            while j < len(lines):
                s = lines[j]
                if COND_RE.match(s) and not s.strip().startswith("else"):
                    depth += 1
                elif re.match(r"^\s*endif\b", s):
                    depth -= 1
                    if depth == 0:
                        break
                elif s.strip() and not s.strip().startswith("else"):
                    ok = False
                    break
                j += 1
            if ok and j < len(lines):
                return "\n".join(lines[:i] + lines[j + 1:])
    return "\n".join(lines)


# ---------------- F2: init.c ----------------
def fix_init(text):
    return re.sub(r"[ \t]*(?:ksu_syscall_hook_manager_init|ksu_syscall_hook_manager_exit|ksu_lsm_hook_exit)\(\);\n", "", text)


# ---------------- F3: bridge.c ----------------
def fix_bridge(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    while i < len(lines):
        l = lines[i]
        ls = l.strip()
        if re.match(r"^if \(static_branch_unlikely\(&ksud_execve_key\)\) \{$", ls):
            d = 1
            j = i + 1
            while j < len(lines) and d > 0:
                s = lines[j].strip()
                d += s.count("{") - s.count("}")
                j += 1
            i = j
            continue
        if "ksu_clear_task_tracepoint_flag_if_needed(current)" in l:
            i += 1
            continue
        out.append(l)
        i += 1
    text = "\n".join(out)
    text = re.sub(r"^(\s*)if \(!ksu_su_compat_enabled\)\s*$", r"\1if (!static_branch_unlikely(&ksu_su_compat_enabled))", text, flags=re.M)
    text = re.sub(r"^(\s*)\} else if \(ksu_su_compat_enabled\) \{\s*$", r"\1} else if (static_branch_likely(&ksu_su_compat_enabled)) {", text, flags=re.M)
    text = re.sub(r"return\s+ksu_handle_stat_sucompat\([^;]*\);", "return ksu_syscall_table[orig_nr](regs);", text)
    text = re.sub(r"return\s+ksu_handle_faccessat_sucompat\([^;]*\);", "return ksu_syscall_table[orig_nr](regs);", text)
    text = re.sub(r"ret\s*=\s*execveat\s*\?\s*ksu_handle_execveat_sucompat\([^;]*?\)\s*:\s*\n\s*ksu_handle_execve_sucompat\([^;]*?\);", "ret = ksu_syscall_table[orig_nr](regs);", text)
    return text


# ---------------- F4: fs/Makefile ----------------
def fix_fs_makefile(root):
    mk = root / "fs" / "Makefile"
    if not mk.is_file():
        return None
    block = (
        "\n# " + MARK + ": align kernel-side preinit wiring with CONFIG_HEADERS_INSTALL\n"
        "ifeq ($(CONFIG_HEADERS_INSTALL),y)\n"
        "# UAPI headers installed: KowSU preinit runner is active, wiring stays\n"
        "else\n"
        "CFLAGS_exec.o += -DKSU_NO_PREINIT\n"
        "endif\n")
    text = read(mk)
    if MARK in text:
        return "NO-OP"
    write(mk, text + block)
    return "CHANGED"


CANON_SU = r'''#include <linux/compiler_types.h>
#include <linux/preempt.h>
#include <linux/printk.h>
#include <linux/mm.h>
#include <linux/pgtable.h>
#include <linux/uaccess.h>
#include <asm/current.h>
#include <linux/cred.h>
#include <linux/fs.h>
#include <linux/types.h>
#include <linux/version.h>
#include <linux/sched/task_stack.h>
#include <linux/ptrace.h>

#include "arch.h"
#include "policy/allowlist.h"
#include <linux/susfs_def.h>
#include <linux/namei.h>
#include <linux/minmax.h>
#include <linux/fs_struct.h>
#include "selinux/selinux.h"
#include "objsec.h"
#include "policy/feature.h"
#include "feature/adb_root.h"
#include "klog.h" // IWYU pragma: keep
#include "feature/sucompat.h"
#include "policy/app_profile.h"
#include "uapi/sulog.h"
#include "supercall/supercall.h"
#include "sulog/event.h"
#include "ksu.h"
#include "util.h"

#define SU_PATH "/system/bin/su"
#define SH_PATH "/system/bin/sh"
static const char sh_path[] = "/system/bin/sh";
static const char su_path[] = "/system/bin/su";
static const char ksud_path[] = KSUD_PATH;

DEFINE_STATIC_KEY_TRUE(ksu_su_compat_enabled);

static int su_compat_feature_get(u64 *value)
{
    if (static_key_enabled(&ksu_su_compat_enabled))
        *value = 1;
    else
        *value = 0;
    return 0;
}

static int su_compat_feature_set(u64 value)
{
    bool enable = value != 0;
    if (enable)
        static_branch_enable(&ksu_su_compat_enabled);
    else
        static_branch_disable(&ksu_su_compat_enabled);
    pr_info("su_compat: set to %d\n", enable);
    return 0;
}

static const struct ksu_feature_handler su_compat_handler = {
    .feature_id = KSU_FEATURE_SU_COMPAT,
    .name = "su_compat",
    .get_handler = su_compat_feature_get,
    .set_handler = su_compat_feature_set,
};

static char __user *sh_user_path(void)
{
    static const char sh_path[] = "/system/bin/sh";

    return userspace_stack_buffer(sh_path, sizeof(sh_path));
}

static char __user *ksud_user_path(void)
{
    static const char ksud_path[] = KSUD_PATH;

    return userspace_stack_buffer(ksud_path, sizeof(ksud_path));
}

/*
 * return 0 -> No further checks should be required afterwards
 * return non-zero -> Further checks should be continued afterwards
 */
int ksu_handle_execveat_init(struct filename *filename, struct user_arg_ptr *argv_user, struct user_arg_ptr *envp_user) {
    int ret;

    if (current->pid == 1)
        return -EINVAL;

    if (!is_init(get_current_cred()))
        return -EINVAL;

    if (unlikely(!strcmp(filename->name, KSUD_PATH))) {
        const char __user *argv_user_ptr = get_user_arg_ptr(*argv_user, 0);
        struct ksu_sulog_pending_event *pending_sucompat = NULL;

        pr_info("hook_manager: escape to root for init executing ksud: %d\n", current->pid);
        pending_sucompat = ksu_sulog_capture_sucompat(filename->name, argv_user, GFP_KERNEL);
        ret = escape_to_root_for_init();
        if (ret) {
            pr_err("escape_to_root_for_init() failed: %d\n", ret);
            return ret;
        }
        if (!argv_user_ptr || IS_ERR(argv_user_ptr)) {
            pr_err("!argv_user_ptr || IS_ERR(argv_user_ptr)\n");
            return 0;
        }
        ksu_sulog_emit_pending(pending_sucompat, ret, GFP_KERNEL);
        return 0;
    }

    if (likely(!strstr(filename->name, "/app_process") && !strstr(filename->name, "/adbd") && !strstr(filename->name, "/stub_zygote"))) {
        pr_info("susfs: mark no sucompat checks for pid: '%d', exec: '%s'\n", current->pid, filename->name);
        susfs_set_current_proc_no_su();
        // - marking proc umounted here is useless because only zygote spawned processes will umount
        //   the sus mounts, tho other susfs features still rely on proc_umounted check, but
        //   it is fine to not spoof for init spawned processes.
        return 0;
    }

#ifdef CONFIG_COMPAT
    if (unlikely(envp_user->is_compat))
        ret = ksu_adb_root_handle_execveat(filename->name, (void ***)&envp_user->ptr.compat);
    else
        ret = ksu_adb_root_handle_execveat(filename->name, (void ***)&envp_user->ptr.native);
#else
        ret = ksu_adb_root_handle_execveat(filename->name, (void ***)&envp_user->ptr.native);
#endif

    if (ret)
        pr_err("adb root failed: %d\n", ret);

    return ret;
}

// the call from execve_handler_pre won't provided correct value for __never_use_argument, use them after fix execve_handler_pre, keeping them for consistence for manually patched code
int ksu_handle_execveat_sucompat(int *fd, struct filename **filename_ptr,
                 void *argv_user, void *envp_user,
                 int *flags)
{
    struct filename *filename;
    struct ksu_sulog_pending_event *pending_sucompat = NULL;
    int ret;

    if (unlikely(!filename_ptr))
        return -EINVAL;

    filename = *filename_ptr;
    if (IS_ERR(filename))
        return -EINVAL;

    if (!ksu_handle_execveat_init(filename, (struct user_arg_ptr*)argv_user, (struct user_arg_ptr*)envp_user))
        return -EINVAL;

    if (likely(!__ksu_is_allow_uid_for_current(current_uid().val)))
        return -EINVAL;

    if (likely(memcmp(filename->name, su_path, sizeof(su_path))))
        return -EINVAL;

    if (current_chrooted())
    {
        pr_err("ksu_handle_execveat_sucompat: su found but NOT allowed! Because current process is running in chrooted environment\n");
        return -EINVAL;
    }

    ret = escape_with_root_profile();

    pr_info("ksu_handle_execveat_sucompat: su->ksud!\n");
    memcpy((void *)filename->name, ksud_path, sizeof(ksud_path));

    pending_sucompat = ksu_sulog_capture_sucompat(filename->name, (struct user_arg_ptr*)argv_user, GFP_KERNEL);

    const char __user *argv_user_ptr = get_user_arg_ptr(*((struct user_arg_ptr*)argv_user), 0);
    if (!argv_user_ptr || IS_ERR(argv_user_ptr)) {
        pr_err("!argv_user_ptr || IS_ERR(argv_user_ptr)\n");
        return -EINVAL;
    }

    ksu_sulog_emit_pending(pending_sucompat, ret, GFP_KERNEL);

    if (ret) {
        pr_err("escape_with_root_profile() failed: %d\n", ret);
        return -EINVAL;
    }
    return 0;
}

int ksu_handle_post_execveat_sucompat(int *fd, struct filename **filename_ptr,
                 void *argv_user, void *envp_user,
                 int *flags, int *retval)
{
    if (*retval >= 0) {
        (void)ksu_install_su_fd();
    }
    return 0;
}

extern struct static_key_true is_first_zygote;

int ksu_handle_execveat(int *fd, struct filename **filename_ptr, void *argv,
            void *envp, int *flags)
{
    if (static_branch_unlikely(&is_first_zygote))
        (void)ksu_handle_execveat_ksud(fd, filename_ptr, argv, envp, flags);

    return ksu_handle_execveat_sucompat(fd, filename_ptr, argv, envp,
                        flags);
}

int ksu_handle_faccessat(int *dfd, struct filename **filename, int *mode,
             int *__unused_flags)
{
    if (unlikely(IS_ERR(*filename) || (*filename)->name == NULL))
        return 0;

    if (likely(memcmp((*filename)->name, su_path, sizeof(su_path))))
        return 0;

    if (current_chrooted())
    {
        pr_err("ksu_handle_faccessat: su found but NOT allowed! Because current process is running in chrooted environment\n");
        return 0;
    }

    pr_info("ksu_handle_faccessat: su->sh!\n");
    memcpy((void *)((*filename)->name), sh_path, sizeof(sh_path));
    return 0;
}

int ksu_handle_stat(int *dfd, struct filename **filename, int *flags) {
    if (unlikely(IS_ERR(*filename) || (*filename)->name == NULL))
        return 0;

    if (likely(memcmp((*filename)->name, su_path, sizeof(su_path))))
        return 0;

    if (current_chrooted())
    {
        pr_err("ksu_handle_stat: su found but NOT allowed! Because current process is running in chrooted environment\n");
        return 0;
    }

    pr_info("ksu_handle_stat: su->sh!\n");
    memcpy((void *)((*filename)->name), sh_path, sizeof(sh_path));
    return 0;
}

// sucompat: permitted process can execute 'su' to gain root access.
void __init ksu_sucompat_init()
{
    if (ksu_register_feature_handler(&su_compat_handler))
        pr_err("Failed to register su_compat feature handler\n");
}

void __exit ksu_sucompat_exit()
{
    ksu_unregister_feature_handler(KSU_FEATURE_SU_COMPAT);
}
''';

CANON_SC = r'''#include <linux/anon_inodes.h>
#include <linux/err.h>
#include <linux/fdtable.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/kprobes.h>
#include <linux/pid.h>
#include <linux/slab.h>
#include <linux/syscalls.h>
#include <linux/task_work.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#include <linux/utsname.h> // utsname() and uts_sem

#include "uapi/supercall.h"
#include "supercall/internal.h"
#include "arch.h"
#include "util.h"
#include "klog.h" // IWYU pragma: keep
#include "manager/manager_identity.h"
#include "supercall/supercall.h"
#include "../tiny_sulog.c"

#define KSU_DRIVER_PERMISSION_SU_SESSION (1UL << 0)

struct ksu_driver_context {
    unsigned long permissions;
};

struct ksu_install_fd_tw {
    struct callback_head cb;
    int __user *outp;
};

static int anon_ksu_release(struct inode *inode, struct file *filp)
{
    kfree(filp->private_data);
    pr_info("ksu fd released\n");
    return 0;
}

static long anon_ksu_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
    return ksu_supercall_handle_ioctl(filp, cmd, (void __user *)arg);
}

static const struct file_operations anon_ksu_fops = {
    .owner = THIS_MODULE,
    .unlocked_ioctl = anon_ksu_ioctl,
    .compat_ioctl = anon_ksu_ioctl,
    .release = anon_ksu_release,
};

static int ksu_install_fd_with_permissions(unsigned int fd_flags, unsigned long permissions)
{
    struct ksu_driver_context *context;
    struct file *filp;
    const char *name;
    int fd;

    context = kzalloc(sizeof(*context), GFP_KERNEL);
    if (!context)
        return -ENOMEM;

        pr_err("ksu_install_fd: failed to get unused fd for %s\n", name);
    name = permissions & KSU_DRIVER_PERMISSION_SU_SESSION ? "[ksu_driver_su]" : "[ksu_driver]";

    fd = get_unused_fd_flags(fd_flags);
    if (fd < 0) {
        pr_err("ksu_install_fd: failed to get unused fd\n");
        kfree(context);
        pr_err("ksu_install_fd: failed to create anon inode file for %s\n", name);
    }

    filp = anon_inode_getfile(name, &anon_ksu_fops, context, O_RDWR);
    if (IS_ERR(filp)) {
        pr_err("ksu_install_fd: failed to create anon inode file\n");
        put_unused_fd(fd);
    pr_info("%s fd installed: %d for pid %d\n", name, fd, current->pid);
        return PTR_ERR(filp);
    }

    fd_install(fd, filp);
    pr_info("ksu fd installed: %d for pid %d\n", fd, current->pid);
    return fd;
}

int ksu_install_fd(void)
{
    return ksu_install_fd_with_permissions(O_CLOEXEC, 0);
}

int ksu_install_su_fd(void)
{
    // This descriptor must be installed after the exec into ksud.
    return ksu_install_fd_with_permissions(O_CLOEXEC, KSU_DRIVER_PERMISSION_SU_SESSION);
}

bool ksu_is_su_session_fd(const struct file *filp)
{
    const struct ksu_driver_context *context = filp->private_data;

    return context && (context->permissions & KSU_DRIVER_PERMISSION_SU_SESSION);
}

static void ksu_install_fd_tw_func(struct callback_head *cb)
{
    struct ksu_install_fd_tw *tw = container_of(cb, struct ksu_install_fd_tw, cb);
    int fd = ksu_install_fd();

    pr_info("[%d] install ksu fd: %d\n", current->pid, fd);
    if (copy_to_user(tw->outp, &fd, sizeof(fd))) {
        pr_err("install ksu fd reply err\n");
        ksu_close_fd(fd);
    }

    kfree(tw);
}

extern uint32_t ksuver_override;
extern uint32_t ksuflags_override;

// downstream: make sure to pass arg as reference, this can allow us to extend things.
static int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg)
{
    if (magic1 != KSU_INSTALL_MAGIC1)
    	return 0;

    pr_info("sys_reboot: intercepted call! magic: 0x%x id: %d\n", magic1, magic2);

    // arg4 = (unsigned long)PT_REGS_SYSCALL_PARM4(real_regs);
    // downstream: dereference arg as arg4 so we can be inline to upstream
    void __user *arg4 = (void __user *)*arg;

    // Check if this is a request to install KSU fd
    if (magic2 == KSU_INSTALL_MAGIC2) {
        struct ksu_install_fd_tw *tw;

        tw = kzalloc(sizeof(*tw), GFP_ATOMIC);
        if (!tw)
            return 0;

        tw->outp = (int __user *)arg4;
        tw->cb.func = ksu_install_fd_tw_func;

        if (task_work_add(current, &tw->cb, TWA_RESUME)) {
            kfree(tw);
            pr_warn("install fd add task_work failed\n");
        }
    }

    // downstream: extensions go here!

    // extensions
    u64 reply = (u64)*arg;

    if (magic2 == CHANGE_MANAGER_UID) {
        // only root is allowed for this command
        if (current_uid().val != 0)
            return 0;

        pr_info("sys_reboot: ksu_set_manager_appid to: %d\n", cmd);
        ksu_set_manager_appid(cmd);

        if (cmd == ksu_get_manager_appid()) {
            if (copy_to_user((void __user *)*arg, &reply, sizeof(reply)))
            	pr_info("sys_reboot: reply fail\n");
        }

        return 0;
    }

    if (magic2 == GET_SULOG_DUMP_V2) {
        // only root is allowed for this command
        if (current_uid().val != 0)
            return 0;

        int ret = send_sulog_dump(*arg);
            if (ret)
                return 0;

        if (copy_to_user((void __user *)*arg, &reply, sizeof(reply) ))
            return 0;
    }

    if (magic2 == CHANGE_KSUVER) {
        // only root is allowed for this command
        if (current_uid().val != 0)
            return 0;

        pr_info("sys_reboot: ksu_change_ksuver to: %d\n", cmd);
        ksuver_override = cmd;

        if (copy_to_user((void __user *)*arg, &reply, sizeof(reply) ))
            return 0;
    }

    // WARNING!!! triple ptr zone! ***
    if (magic2 == CHANGE_SPOOF_UNAME) {
        // only root is allowed for this command 
        if (current_uid().val != 0)
            return 0;

        char release_buf[65];
        char version_buf[65];
        static char original_release_buf[65] = {0};
        static char original_version_buf[65] = {0};

        // basically void * void __user * void __user *arg
        void ***ppptr = (uintptr_t)arg;

        // user pointer storage
        // init this as zero this still works on 32-on-64 LE
        uint64_t u_pptr = 0;
        uint64_t u_ptr = 0;

        pr_info("sys_reboot: ppptr: 0x%lx \n", (uintptr_t)ppptr);

        // arg here is ***, dereference to pull out **
        if (copy_from_user(&u_pptr, (void __user *)*ppptr, sizeof(u_pptr)))
            return 0;

        pr_info("sys_reboot: u_pptr: 0x%lx \n", (uintptr_t)u_pptr);

        // now we got the __user **
        // we cannot dereference this as this is __user
        // we just do another copy_from_user to get it
        if (copy_from_user(&u_ptr, (void __user *)u_pptr, sizeof(u_ptr)))
            return 0;

        pr_info("sys_reboot: u_ptr: 0x%lx \n", (uintptr_t)u_ptr);

        // for release
        if (strncpy_from_user(release_buf, (char __user *)u_ptr, sizeof(release_buf)) < 0)
            return 0;
        release_buf[sizeof(release_buf) - 1] = '\0'; 

        // for version
        if (strncpy_from_user(version_buf, (char __user *)(u_ptr + strlen(release_buf) + 1), sizeof(version_buf)) < 0)
            return 0;
        version_buf[sizeof(version_buf) - 1] = '\0'; 

        if (original_release_buf[0] == '\0') {
            struct new_utsname *u_curr = utsname();
            // we save current version as the original before modifying
            strncpy(original_release_buf, u_curr->release, sizeof(original_release_buf));
            strncpy(original_version_buf, u_curr->version, sizeof(original_version_buf));
            pr_info("sys_reboot: original uname saved: %s %s\n", original_release_buf, original_version_buf);
        }

        // so user can reset
        if (!strcmp(release_buf, "default")) {
             memcpy(release_buf, original_release_buf, sizeof(release_buf));
        }

        if (!strcmp(version_buf, "default")) {
            memcpy(version_buf, original_version_buf, sizeof(version_buf));
        }

        pr_info("sys_reboot: spoofing kernel to: %s - %s\n", release_buf, version_buf);

        struct new_utsname *u = utsname();

        down_write(&uts_sem);
        strncpy(u->release, release_buf, sizeof(u->release));
        strncpy(u->version, version_buf, sizeof(u->version));
        up_write(&uts_sem);

        // we write our confirmation on **
        if (copy_to_user((void __user *)*arg, &reply, sizeof(reply)))
            return 0;
    }

    if (magic2 == CHANGE_KSUFLAGS) {
        // only root is allowed for this command
        if (current_uid().val != 0)
            return 0;

        pr_info("sys_reboot: ksu_change_ksuflags to: %d\n", cmd);
        ksuflags_override = cmd;

        if (copy_to_user((void __user *)*arg, &reply, sizeof(reply) ))
            return 0;
    }

    return 0;
}

int ksu_supercall_reboot_handler(void __user **arg)
{
    struct ksu_install_fd_tw *tw;

    tw = kzalloc(sizeof(*tw), GFP_KERNEL);
    if (!tw)
        return 0;

    tw->outp = (int __user *)(*arg);
    tw->cb.func = ksu_install_fd_tw_func;

    if (task_work_add(current, &tw->cb, TWA_RESUME)) {
        kfree(tw);
        pr_warn("install fd add task_work failed\n");
    }

    return 0;
}

void __init ksu_supercalls_init(void)
{
    ksu_supercall_dump_commands();
}

void __exit ksu_supercalls_exit(void)
{
    ksu_supercall_cleanup_state();
}
''';

def su_residue(t):
    return ("bool ksu_su_compat_enabled" in t or "long ksu_handle_faccessat_sucompat(" in t
            or "ksu_handle_execve_sucompat_common(" in t or "ksu_is_user_string_su" in t
            or "static bool is_ksud_exists" in t
            or "int ksu_handle_execveat(int *fd" not in t
            or "DEFINE_STATIC_KEY_TRUE(ksu_su_compat_enabled);" not in t)


def sc_residue(t):
    return ("reboot_handler_pre" in t or "register_kprobe" in t
            or "int ksu_supercall_reboot_handler(void __user **arg)" not in t)


def main():
    ksu = find_ksu_dir()
    if ksu is None:
        die("cannot locate kernelsu driver dir (looked for core/init.c + feature/sucompat.c)")
    print("kernelsu dir:", ksu)
    root = ksu.parent.parent
    try:
        if not ((root / "Makefile").is_file() and "KERNELRELEASE" in read(root / "Makefile")[:4000]):
            root = None
    except OSError:
        root = None
    if root is None:
        print("NOTE: kernel root not found beside driver dir; F4/V8/V9 skipped")

    log = []

    p = ksu / "Kbuild"
    t0 = read(p)
    t1 = fix_kbuild(t0)
    log.append(("F1 Kbuild hook objects retired", t1 != t0))
    if t1 != t0:
        write(p, t1)

    p = ksu / "core" / "init.c"
    t0 = read(p)
    t1 = fix_init(t0)
    log.append(("F2 init.c hook-manager/lsm calls stripped", t1 != t0))
    if t1 != t0:
        write(p, t1)

    p = ksu / "hook" / "syscall_event_bridge.c"
    if p.is_file():
        t0 = read(p)
        t1 = fix_bridge(t0)
        log.append(("F3 bridge.c static_key + old-sig passthrough", t1 != t0))
        if t1 != t0:
            write(p, t1)

    p = ksu / "feature" / "sucompat.c"
    t0 = read(p)
    if su_residue(t0):
        write(p, CANON_SU)
        log.append(("F5 sucompat.c -> canonical susfs post-state", True))
    else:
        log.append(("F5 sucompat.c already canonical", False))

    p = ksu / "supercall" / "supercall.c"
    t0 = read(p)
    if sc_residue(t0):
        write(p, CANON_SC)
        log.append(("F6 supercall.c -> canonical susfs post-state", True))
    else:
        log.append(("F6 supercall.c already canonical", False))

    if root is not None:
        r = fix_fs_makefile(root)
        if r is not None:
            log.append(("F4 fs/Makefile KSU_NO_PREINIT guard", r == "CHANGED"))

    # ---------------- verification ----------------
    fails = []

    def forbid(tag, text, patterns):
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                line = text[:m.start()].count(chr(10)) + 1
                fails.append("%s:%d residue %s" % (tag, line, pat))

    def require(tag, text, needles):
        for n in needles:
            if n not in text:
                fails.append("%s missing %r" % (tag, n))

    kb = read(ksu / "Kbuild")
    forbid("Kbuild", kb, [r"^\s*kernelsu-objs\s*\+=\s*hook/(lsm_hook|syscall_event_bridge|syscall_hook_manager|tp_marker)\.o",
                          r"^\s*kernelsu-objs\s*\+=\s*hook/(?:arm64|x86_64|riscv64)/"])
    if "hook/setuid_hook.o" not in kb:
        fails.append("Kbuild: hook/setuid_hook.o unexpectedly absent (must stay)")

    init = read(ksu / "core" / "init.c")
    forbid("init.c", init, [r"\bksu_syscall_hook_manager_init\b", r"\bksu_syscall_hook_manager_exit\b", r"\bksu_lsm_hook_exit\b"])

    br = ksu / "hook" / "syscall_event_bridge.c"
    if br.is_file():
        bt = read(br)
        forbid("bridge.c", bt, [r"\bksu_handle_stat_sucompat\b", r"\bksu_handle_faccessat_sucompat\b",
                                r"\bksu_handle_execve_sucompat\b", r"ksu_handle_execveat_sucompat\(filename_user",
                                r"&static_key_enabled\(", r"ksu_execveat_hook_ksud\(regs\)", r"ksu_execve_hook_ksud\(regs\)"])
        for m in re.finditer(r"ksu_su_compat_enabled", bt):
            pre = bt[max(0, m.start() - 30):m.start()]
            if "static_branch_unlikely(&" not in pre and "static_branch_likely(&" not in pre:
                fails.append("bridge.c:%d bare struct read of ksu_su_compat_enabled" % (bt[:m.start()].count(chr(10)) + 1))

    su = read(ksu / "feature" / "sucompat.c")
    require("sucompat.c", su, ["DEFINE_STATIC_KEY_TRUE(ksu_su_compat_enabled)",
                               "int ksu_handle_stat(int *dfd",
                               "int ksu_handle_faccessat(int *dfd",
                               "int ksu_handle_execveat(int *fd",
                               "static char __user *sh_user_path(void)",
                               "int ksu_handle_execveat_init(struct filename *filename"])
    forbid("sucompat.c", su, [r"long ksu_handle_(stat|faccessat|execve|execveat)_sucompat\(",
                              r"bool ksu_su_compat_enabled",
                              r"static __always_inline bool ksu_is_user_string_su"])

    sh = read(ksu / "feature" / "sucompat.h")
    require("sucompat.h", sh, ["extern struct static_key_true ksu_su_compat_enabled"])
    forbid("sucompat.h", sh, [r"extern bool ksu_su_compat_enabled", r"long ksu_handle_\w*_sucompat\("])

    ki = read(ksu / "runtime" / "ksud_integration.c")
    require("ksud_integration.c", ki, ["int ksu_handle_execveat_ksud(int *fd, struct filename **filename_ptr,",
                                       "DEFINE_STATIC_KEY_TRUE(is_first_zygote)"])
    forbid("ksud_integration.c", ki, [r"\bksu_execve_hook_ksud\b", r"\bksu_execveat_hook_ksud\b"])

    pc = read(ksu / "preinit" / "preinit.c")
    require("preinit.c", pc, ["int ksu_setup_preinit_file(struct filename **filename_ptr"])

    for c in sorted(ksu.rglob("*.c")):
        rel = c.relative_to(ksu).as_posix()
        if rel.startswith("hook/"):
            continue
        t = read(c)
        forbid(rel, t, [r"\bksu_handle_stat_sucompat\b", r"\bksu_handle_faccessat_sucompat\b",
                        r"\bksu_handle_execve_sucompat\b"])

    if root is not None:
        ex = root / "fs" / "exec.c"
        if ex.is_file():
            ext = read(ex)
            require("fs/exec.c", ext, ["ksu_handle_execveat_sucompat"])
            forbid("fs/exec.c", ext, [r"ksu_handle_execveat_sucompat\(\s*filename_user"])
        st = root / "fs" / "stat.c"
        if st.is_file():
            require("fs/stat.c", read(st), ["ksu_handle_stat(&dfd"])
        op = root / "fs" / "open.c"
        if op.is_file():
            require("fs/open.c", read(op), ["ksu_handle_faccessat(&dfd"])
        fmk = root / "fs" / "Makefile"
        headers_n = True
        dc = root / "arch" / "arm64" / "configs" / "gki_defconfig"
        if dc.is_file():
            headers_n = "CONFIG_HEADERS_INSTALL=y" not in read(dc)
        if headers_n:
            if fmk.is_file() and MARK not in read(fmk):
                fails.append("fs/Makefile missing KSU_NO_PREINIT guard while CONFIG_HEADERS_INSTALL=n")
            print("PREINIT: kernel-side disabled (CONFIG_HEADERS_INSTALL=n); flip defconfig to =y to enable runner")
        else:
            print("PREINIT: active (CONFIG_HEADERS_INSTALL=y); watch build log for RUNNER lines")

    print()
    for name, ok in log:
        print("%-52s %s" % (name, "CHANGED" if ok else "NO-OP"))
    print()
    if fails:
        print("=== RESIDUE DETECTED (%d) ===" % len(fails))
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("verification passed: bridge/Kbuild/sucompat/ksud/preinit/fs wiring consistent")


main()
