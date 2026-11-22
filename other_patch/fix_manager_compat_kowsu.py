#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_manager_compat_kowsu.py v7.2 — port BakaSU multi-manager recognition + extra ioctls
into KowSU(master) driver. Run AFTER 10_enable patch + fix_bridge_kowsu_v6.py.
v7.2: task_work.h in compat header (CI 44989320080 fix); dispatch handlers anchored
before the mapping-table comment (was: injected inside the array initializer);
uapi adds the 6 KSU_IOCTL_* consts KowSU lacks; extern decl for
ksu_handle_get_managers_cmd; compat helpers are static inline (multi-TU safe).
Idempotent; residue-verified; exit 1 on any inconsistency.
"""
import sys, re
from pathlib import Path

def die(m):
    print("FATAL: " + m); sys.exit(1)

def find_ksu_dir():
    for c in [Path("drivers/kernelsu"), Path("common/drivers/kernelsu"),
              Path("../common/drivers/kernelsu"), Path("../drivers/kernelsu"),
              Path("KernelSU/kernel"), Path("../KernelSU/kernel"),
              Path("drivers/kernelsu/KernelSU/kernel"),
              Path("common/drivers/kernelsu/KernelSU/kernel")]:
        try:
            if (c / "core" / "init.c").is_file() and (c / "feature" / "sucompat.c").is_file():
                return c.resolve()
        except OSError:
            pass
    return None

def read(p): return p.read_text(encoding="utf-8")
def write(p, s): p.write_text(s, encoding="utf-8", newline="\n")

ksu = find_ksu_dir()
if ksu is None:
    die("cannot locate kernelsu driver dir")
print("kernelsu dir:", ksu)
uapi_dir = None
for cand in [ksu.parent / "uapi", ksu / "include" / "uapi", ksu.parent.parent / "uapi", ksu.parent.parent.parent / "uapi"]:
    try:
        if (cand / "supercall.h").is_file():
            uapi_dir = cand
            break
    except OSError:
        pass
if uapi_dir is None:
    die("uapi/supercall.h not found near kernelsu dir")

log = []

# 1. manager_sign.h [new]
p = ksu / "manager" / "manager_sign.h"
newfile = not p.is_file()
if newfile:
    write(p, r'''#ifndef MANAGER_SIGN_H
#define MANAGER_SIGN_H

// tiann/KernelSU
#define EXPECTED_SIZE_OFFICIAL 0x033b
#define EXPECTED_HASH_OFFICIAL "c371061b19d8c7d7d6133c6a9bafe198fa944e50c1b31c9d8daa8d7f1fc2d2d6"

// Baka-SU/BakaSU
#define EXPECTED_SIZE_BAKASU 0x377
#define EXPECTED_HASH_BAKASU "d3469712b6214462764a1d8d3e5cbe1d6819a0b629791b9f4101867821f1df64"

// KOWX712/KernelSU
#define EXPECTED_SIZE_KOWX712 0x375
#define EXPECTED_HASH_KOWX712 "484fcba6e6c43b1fb09700633bf2fb4758f13cb0b2f4457b80d075084b26c588"

typedef struct {
    unsigned size;
    const char *sha256;
} apk_sign_key_t;

#endif /* MANAGER_SIGN_H */
''')
log.append(("W manager/manager_sign.h", newfile))

# 2. compat/kernel_compat.h [v2 — helpers included]
p = ksu / "compat" / "kernel_compat.h"
oldc = read(p) if p.is_file() else ""
newfile = "ksu_kernel_read_compat" not in oldc
if newfile:
    p.parent.mkdir(parents=True, exist_ok=True)
    write(p, r'''#ifndef __KSU_H_KERNEL_COMPAT_MINI
#define __KSU_H_KERNEL_COMPAT_MINI
/* compat shim for ported manager files (KowSU provides ksu_filp_open_nonotify
 * in include/util.h; helpers ported from Baka-SU/BakaSU kernel_compat) */
#include <linux/version.h>
#include <linux/types.h>
#include <linux/fs.h>
#include <linux/err.h>
#include <linux/sched.h>
#include <linux/pid.h>
#include <linux/slab.h>
#include <linux/task_work.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 11, 0)
#include <linux/sched/task.h> /* put_task_struct */
#endif
#include <linux/uaccess.h>
#include <klog.h>
#include "util.h" /* ksu_filp_open_nonotify */

#define KSU_MAX_PACKAGE_NAME 256

#if LINUX_VERSION_CODE < KERNEL_VERSION(3, 14, 0)
#define ksu_get_uid_t(x) (*(unsigned int *)&(x))
#else
#define ksu_get_uid_t(x) ((x).val)
#endif

/* ---- task_work helpers (BakaSU) ---- */
struct ksu_task_work_struct {
    struct callback_head cb;
    void (*callback)(void *);
    void *data;
};

static void ksu_task_work_dispatcher(struct callback_head *cb)
{
    struct ksu_task_work_struct *tw = container_of(cb, struct ksu_task_work_struct, cb);
    tw->callback(tw->data);
    kfree(tw);
}

#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 7, 0)
#ifndef TWA_RESUME
#define TWA_RESUME true
#endif
#endif

static inline void ksu_run_in_init_if_possible(void (*callback)(void *), void *data)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 5, 0)
    struct task_struct *tsk;

    tsk = get_pid_task(find_vpid(1), PIDTYPE_PID);
    if (!tsk) {
        pr_err("ksu run in init find init task err\n");
        return;
    }

    struct ksu_task_work_struct *tw = kzalloc(sizeof(struct ksu_task_work_struct), GFP_ATOMIC);
    if (!tw) {
        pr_err("ksu run in init alloc tw err\n");
        put_task_struct(tsk);
        return;
    }

    tw->callback = callback;
    tw->data = data;
    tw->cb.func = ksu_task_work_dispatcher;

    if (task_work_add(tsk, &tw->cb, TWA_RESUME)) {
        kfree(tw);
        pr_warn("ksu run in init add task_work failed\n");
    }

    put_task_struct(tsk);
#else
    callback(data);
#endif
}

static inline ssize_t ksu_kernel_read_compat(struct file *p, void *buf, size_t count, loff_t *pos)  /* static inline: header-defined, multi-TU */
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0) || defined(KSU_OPTIONAL_KERNEL_READ)
    return kernel_read(p, buf, count, pos);
#else
    mm_segment_t old_fs;
    old_fs = get_fs();
    set_fs(get_ds());
    ssize_t result = vfs_read(p, (void __user *)buf, count, pos);
    set_fs(old_fs);
    return result;
#endif
}

int get_pkg_from_apk_path(char *pkg, const char *path);

#endif /* __KSU_H_KERNEL_COMPAT_MINI */

''')
log.append(("W compat/kernel_compat.h (mini)", newfile))

# 3. dynamic_manager.{h,c} [new]
p = ksu / "feature" / "dynamic_manager.h"
newfile = not p.is_file()
if newfile:
    write(p, r'''#ifndef __KSU_H_DYNAMIC_MANAGER
#define __KSU_H_DYNAMIC_MANAGER

#include <linux/types.h>
#include "ksu.h"
#include "manager/manager_sign.h"
#include "uapi/supercall.h"

struct dynamic_manager_config {
    unsigned size;
    char hash[65];
    int is_set;
};

struct manager_info {
    uid_t uid;
    int signature_index;
    bool is_active;
};

// Dynamic sign operations
int ksu_handle_dynamic_manager(struct ksu_dynamic_manager_cmd *cmd);
bool ksu_load_dynamic_manager(void);
bool ksu_is_dynamic_manager_enabled(void);
apk_sign_key_t ksu_get_dynamic_manager_sign(void);

#endif''')
log.append(("W feature/dynamic_manager.h", newfile))
p = ksu / "feature" / "dynamic_manager.c"
newfile = not p.is_file()
if newfile:
    write(p, r'''#include <linux/err.h>
#include <linux/fs.h>
#include <linux/gfp.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/version.h>
#include <linux/sched.h>
#include <linux/pid.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 11, 0)
#include <linux/sched/task.h>
#endif
#ifdef CONFIG_KSU_DEBUG
#include <linux/moduleparam.h>
#endif
#include <crypto/hash.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 11, 0)
#include <crypto/sha2.h>
#else
#include <crypto/sha.h>
#endif

#include "manager/throne_tracker.h"
#include "compat/kernel_compat.h"
#include "dynamic_manager.h"
#include "klog.h" // IWYU pragma: keep
#include "manager/manager_identity.h"
#include "ksu.h"

// Dynamic sign configuration
static struct dynamic_manager_config dynamic_manager = {
    .size = 0x300,
    .hash = "0000000000000000000000000000000000000000000000000000000000000000",
    .is_set = 0
};

bool ksu_is_dynamic_manager_enabled(void)
{
    return dynamic_manager.is_set;
}

apk_sign_key_t ksu_get_dynamic_manager_sign(void)
{
    apk_sign_key_t sign_key = { .size = dynamic_manager.size, .sha256 = dynamic_manager.hash };

    return sign_key;
}

int ksu_handle_dynamic_manager(struct ksu_dynamic_manager_cmd *cmd)
{
    unsigned long flags;
    int ret = 0;
    int i;

    if (!cmd) {
        return -EINVAL;
    }

    switch (cmd->operation) {
    case DYNAMIC_MANAGER_OP_SET_SYNCHRONOUS:
    case DYNAMIC_MANAGER_OP_SET:
        if (cmd->size < 0x100 || cmd->size > 0x1000) {
            pr_err("invalid size: 0x%x\n", cmd->size);
            return -EINVAL;
        }

        // Validate hash format
        for (i = 0; i < 64; i++) {
            char c = cmd->hash[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
                pr_err("invalid hash character at position %d: %c\n", i, c);
                return -EINVAL;
            }
        }

        if (dynamic_manager.is_set) {
            ksu_unregister_manager_by_signature_index(KSU_SIGNATURE_INDEX_DYNAMIC_MANAGER);
        }

        dynamic_manager.size = cmd->size;
        // userspace always put an char[64] to our
        // we just use memcpy to copy memory, and flag [64] to \0 by ourselves
        memcpy(dynamic_manager.hash, cmd->hash, 64);
        dynamic_manager.hash[64] = '\0';

        dynamic_manager.is_set = 1;

        if (cmd->operation == DYNAMIC_MANAGER_OP_SET_SYNCHRONOUS)
            track_throne(TRACK_THRONE_FORCE_SYNCHRONOUS);
        else
            track_throne(0);
        pr_info("dynamic manager updated: size=0x%x, hash=%.16s\n", cmd->size, cmd->hash);
        break;

    case DYNAMIC_MANAGER_OP_GET:
        if (dynamic_manager.is_set) {
            cmd->size = dynamic_manager.size;

            // only copy [64] is enough, userspace will handle that
            memcpy(cmd->hash, dynamic_manager.hash, 64);
            ret = 0;
        } else {
            ret = -ENODATA;
        }
        break;
    case DYNAMIC_MANAGER_OP_WIPE:
        dynamic_manager.is_set = 0;
        ret = 0;
        ksu_unregister_manager_by_signature_index(KSU_SIGNATURE_INDEX_DYNAMIC_MANAGER);
        pr_info("dynamic manager kernel settings reseted");
        break;

    default:
        pr_err("Invalid dynamic manager operation: %d\n", cmd->operation);
        return -EINVAL;
    }

    return ret;
}
''')
log.append(("W feature/dynamic_manager.c", newfile))

# 4. manager_identity.h [rewrite]
p = ksu / "manager" / "manager_identity.h"
old = read(p) if p.is_file() else ""
newfile = "ksu_register_manager" not in old
write(p, r'''#ifndef __KSU_H_MANAGER_IDENTITY
#define __KSU_H_MANAGER_IDENTITY

#include <linux/cred.h>
#include <linux/types.h>

#include "compat/kernel_compat.h"

#define KSU_SIGNATURE_INDEX_DYNAMIC_MANAGER 255
#define KSU_SIGNATURE_INDEX_KSU_DEBUG 254
#define KSU_SIGNATURE_INDEX_KSU_TOOLKIT 253

#ifdef CONFIG_KSU_DISABLE_MANAGER
static inline void ksu_mark_manager(u32 uid)
{
}

static inline bool is_manager(void)
{
    return ksu_get_uid_t(current_uid()) == 0;
}

static inline bool ksu_is_manager_appid(u16 appid)
{
    return appid == 0;
}

static inline bool ksu_is_manager_uid(u32 uid)
{
    return uid == 0;
}

static inline bool ksu_register_manager(u32 uid, u8 signature_index)
{
    return true;
}

static inline bool ksu_unregister_manager(u32 uid)
{
    return true;
}

static inline void ksu_unregister_manager_by_signature_index(u8 signature_index)
{
}

static inline int ksu_get_manager_signature_index_by_appid(u16 appid)
{
    return -EOPNOTSUPP;
}

static inline bool ksu_has_manager(void)
{
    return true;
}
#else
#define PER_USER_RANGE 100000
#define KSU_INVALID_APPID -1
extern u16 ksu_last_manager_appid;

static inline void ksu_mark_manager(u32 uid)
{
    ksu_last_manager_appid = uid % PER_USER_RANGE;
}

extern bool is_manager(void);
bool ksu_is_manager_appid(u16 appid);
extern bool ksu_is_manager_uid(u32 uid);
extern void ksu_register_manager(u32 uid, u8 signature_index);
extern void ksu_unregister_manager(u32 uid);
extern void ksu_unregister_manager_by_signature_index(u8 signature_index);
extern void ksu_unregister_all_manager(void);
extern int ksu_get_manager_signature_index_by_appid(u16 appid);
extern bool ksu_has_manager(void);

/* ---- KowSU compat shims (callers kept unchanged) ---- */
static inline bool ksu_is_manager_appid_valid(void)
{
    return ksu_has_manager();
}

static inline bool is_uid_manager(uid_t uid)
{
    return ksu_is_manager_uid(uid);
}

static inline uid_t ksu_get_manager_appid(void)
{
    return (uid_t)ksu_last_manager_appid;
}

static inline void ksu_set_manager_appid(uid_t appid)
{
    /* legacy single-manager setter: register with debug signature index */
    ksu_register_manager(appid, KSU_SIGNATURE_INDEX_KSU_DEBUG);
}

static inline void ksu_invalidate_manager_uid(void)
{
    ksu_unregister_all_manager();
}
#endif

#endif /* __KSU_H_MANAGER_IDENTITY */
''')
log.append(("R manager/manager_identity.h", newfile))

# 5. throne_tracker.c [replace + manager list]
p = ksu / "manager" / "throne_tracker.c"
old = read(p) if p.is_file() else ""
newfile = "ksu_manager_appid_list" not in old
write(p, r'''#include "ksu.h"
#include "linux/cred.h"
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/list.h>
#include <linux/slab.h>
#include <linux/bitmap.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/version.h>
#include <linux/stat.h>
#include <linux/namei.h>
#include <linux/kernel.h> // for container_of in UL

#include "policy/allowlist.h"
#include "manager/apk_sign.h"
#include "klog.h" // IWYU pragma: keep
#include "manager/manager_identity.h"
#include "manager/throne_tracker.h"
#include "compat/kernel_compat.h"
#include "feature/dynamic_manager.h"

#define SYSTEM_PACKAGES_LIST_PATH "/data/system/packages.list"
#define SYSTEM_PACKAGES_LIST_TMP_PATH "/data/system/packages.list.tmp"

struct uid_data {
    struct list_head list;
    u32 uid;
    char package[KSU_MAX_PACKAGE_NAME];
};

static void crown_manager(const char *apk, struct list_head *uid_data, u8 signature_index)
{
    char pkg[KSU_MAX_PACKAGE_NAME];
    struct uid_data *np;
    if (get_pkg_from_apk_path(pkg, apk) < 0) {
        pr_err("Failed to get package name from apk path: %s\n", apk);
        return;
    }

    pr_info("manager pkg: %s\n", pkg);

    list_for_each_entry (np, uid_data, list) {
        if (strncmp(np->package, pkg, KSU_MAX_PACKAGE_NAME) == 0) {
            pr_info("Crowning manager: %s uid=%d, signature_index=%d\n", pkg, np->uid, signature_index);

            ksu_register_manager(np->uid, signature_index);
            break;
        }
    }
}

#define DATA_PATH_LEN 384 // 384 is enough for /data/app/<package>/base.apk

struct data_path {
    char dirpath[DATA_PATH_LEN];
    int depth;
    struct list_head list;
};

struct apk_path_hash {
    unsigned int hash;
    bool exists;
    struct list_head list;
};

struct my_dir_context {
    struct dir_context ctx;
    struct list_head *data_path_list;
    char *parent_dir;
    void *private_data;
    int depth;
};
// https://docs.kernel.org/filesystems/porting.html
// filldir_t (readdir callbacks) calling conventions have changed. Instead of returning 0 or -E... it returns bool now. false means "no more" (as -E... used to) and true - "keep going" (as 0 in old calling conventions). Rationale: callers never looked at specific -E... values anyway. -> iterate_shared() instances require no changes at all, all filldir_t ones in the tree converted.
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 1, 0)
#define FILLDIR_RETURN_TYPE bool
#define FILLDIR_ACTOR_CONTINUE true
#define FILLDIR_ACTOR_STOP false
#else
#define FILLDIR_RETURN_TYPE int
#define FILLDIR_ACTOR_CONTINUE 0
#define FILLDIR_ACTOR_STOP -EINVAL
#endif

#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 19, 0)
#define MY_ACTOR_CTX_ARG struct dir_context *ctx
#else
#define MY_ACTOR_CTX_ARG void *ctx_void
#endif

FILLDIR_RETURN_TYPE my_actor(MY_ACTOR_CTX_ARG, const char *name, int namelen, loff_t off, u64 ino, unsigned int d_type)
{
#if LINUX_VERSION_CODE < KERNEL_VERSION(3, 19, 0)
    // then pull it out of the void
    struct dir_context *ctx = (struct dir_context *)ctx_void;
#endif
    struct my_dir_context *my_ctx = container_of(ctx, struct my_dir_context, ctx);
    char dirpath[DATA_PATH_LEN];

    // we put the apk path we collected here
    char *candidate_path = (char *)my_ctx->private_data;

#ifdef CONFIG_KSU_DEBUG
    pr_info("Testing path: %s/%.*s", my_ctx->parent_dir, namelen, name);
#endif

    if (!my_ctx) {
        pr_err("Invalid context\n");
        return FILLDIR_ACTOR_STOP;
    }

    if (!strncmp(name, "..", namelen) || !strncmp(name, ".", namelen))
        return FILLDIR_ACTOR_CONTINUE; // Skip "." and ".."

    if (d_type == DT_DIR && namelen >= 8 && !strncmp(name, "vmdl", 4) && !strncmp(name + namelen - 4, ".tmp", 4)) {
        pr_info("Skipping directory: %.*s\n", namelen, name);
        return FILLDIR_ACTOR_CONTINUE; // Skip staging package
    }

    if (snprintf(dirpath, DATA_PATH_LEN, "%s/%.*s", my_ctx->parent_dir, namelen, name) >= DATA_PATH_LEN) {
        pr_err("Path too long: %s/%.*s\n", my_ctx->parent_dir, namelen, name);
        return FILLDIR_ACTOR_CONTINUE;
    }

    if (d_type == DT_DIR && my_ctx->depth > 0) {
        struct data_path *data = kzalloc(sizeof(struct data_path), GFP_KERNEL);

        if (!data) {
            pr_err("Failed to allocate memory for %s\n", dirpath);
            return FILLDIR_ACTOR_CONTINUE;
        }

        strncpy(data->dirpath, dirpath, DATA_PATH_LEN - 1);
        data->depth = my_ctx->depth - 1;
        list_add_tail(&data->list, my_ctx->data_path_list);
        return FILLDIR_ACTOR_CONTINUE;
    }

    // now put this on candidate_path
    if (d_type == DT_REG && namelen == 8 && !memcmp(name, "base.apk", 8)) {
        snprintf(candidate_path, DATA_PATH_LEN, "%s/%.*s", my_ctx->parent_dir, namelen, name);
    }

    return FILLDIR_ACTOR_CONTINUE;
}

// compat: https://elixir.bootlin.com/linux/v3.9/source/include/linux/fs.h#L771
#if LINUX_VERSION_CODE >= KERNEL_VERSION(3, 9, 0)
#define S_MAGIC_COMPAT(x) ((x)->f_inode->i_sb->s_magic)
#else
#define S_MAGIC_COMPAT(x) ((x)->f_path.dentry->d_inode->i_sb->s_magic)
#endif

void search_manager(const char *path, int depth, struct list_head *uid_data)
{
    int i;
    unsigned long data_app_magic = 0;
    struct apk_path_hash *pos, *n;
    struct list_head data_path_list;
    struct data_path data = {};

    INIT_LIST_HEAD(&data_path_list);

    // First depth
    strncpy(data.dirpath, path, DATA_PATH_LEN - 1);
    data.depth = depth;
    list_add_tail(&data.list, &data_path_list);

    // we put the apk path we collected here
    char candidate_path[DATA_PATH_LEN];

    for (i = depth; i >= 0; i--) {
        struct data_path *pos, *n;

        list_for_each_entry_safe (pos, n, &data_path_list, list) {
            struct my_dir_context ctx = {
                .ctx.actor = my_actor,
                .data_path_list = &data_path_list,
                .parent_dir = pos->dirpath,
                .private_data = candidate_path,
                .depth = pos->depth,
            };
            struct file *file;
            u8 signature_index = 0;

            // make sure to clean buffer on every iteration
            memset(candidate_path, 0, DATA_PATH_LEN);

            file = ksu_filp_open_nonotify(pos->dirpath, O_RDONLY | O_NOFOLLOW);
            if (IS_ERR(file)) {
                pr_err("Failed to open directory: %s, err: %ld\n", pos->dirpath, PTR_ERR(file));
                goto skip_iterate;
            }

            // grab magic on first folder, which is /data/app
            if (!data_app_magic) {
                if (S_MAGIC_COMPAT(file)) {
                    data_app_magic = S_MAGIC_COMPAT(file);
                    pr_info("%s: dir: %s got magic! 0x%lx\n", __func__, pos->dirpath, data_app_magic);
                } else {
                    filp_close(file, NULL);
                    goto skip_iterate;
                }
            }

            if (S_MAGIC_COMPAT(file) != data_app_magic) {
                pr_info("%s: skip: %s magic: 0x%lx expected: 0x%lx\n", __func__, pos->dirpath, S_MAGIC_COMPAT(file),
                        data_app_magic);
                filp_close(file, NULL);
                goto skip_iterate;
            }

            iterate_dir(file, &ctx.ctx);
            filp_close(file, NULL);

            // ^ oh so thats the issue!
            // we were calling is_manager_apk inside iterate_dir
            // now we defer file opens after iterate_dir
            // this way we dont open apks while inside that
            if (!strstarts(candidate_path, "/data/ap"))
                goto skip_iterate;

            bool is_manager = is_manager_apk(candidate_path, &signature_index);
            pr_info("Found new base.apk at path: %s, is_manager: %d\n", candidate_path, is_manager);

            if (likely(!is_manager))
                goto skip_iterate;

            crown_manager(candidate_path, uid_data, signature_index);

        skip_iterate:
            list_del(&pos->list);
            if (pos != &data)
                kfree(pos);
        }
    }
}

static bool is_uid_exist(uid_t uid, char *package, void *data)
{
    struct list_head *list = (struct list_head *)data;
    struct uid_data *np;

    bool exist = false;
    list_for_each_entry (np, list, list) {
        if (np->uid == uid % PER_USER_RANGE && strncmp(np->package, package, KSU_MAX_PACKAGE_NAME) == 0) {
            exist = true;
            break;
        }
    }
    return exist;
}

struct track_throne_struct {
    unsigned int flags;
};

void do_track_throne(void *data)
{
    struct track_throne_struct *tts = (struct track_throne_struct *)data;
    unsigned int flags = tts->flags;
    kfree(tts);

    struct list_head uid_list;
    struct uid_data *np, *n;
    struct file *fp;
    char chr = 0;
    loff_t pos = 0;
    loff_t line_start = 0;
    char buf[KSU_MAX_PACKAGE_NAME];

    // init uid list head
    INIT_LIST_HEAD(&uid_list);

    const struct cred *old_cred = override_creds(ksu_cred);

    if (flags & TRACK_THRONE_FROM_RENAMEAT) {
        fp = filp_open(SYSTEM_PACKAGES_LIST_TMP_PATH, O_RDONLY, 0);
        if (IS_ERR(fp)) {
            pr_err("%s: open " SYSTEM_PACKAGES_LIST_TMP_PATH " failed: %ld\n", __func__, PTR_ERR(fp));
            goto out;
        }
    } else {
        fp = filp_open(SYSTEM_PACKAGES_LIST_PATH, O_RDONLY, 0);
        if (IS_ERR(fp)) {
            pr_err("%s: open " SYSTEM_PACKAGES_LIST_PATH " failed: %ld\n", __func__, PTR_ERR(fp));
            goto out;
        }
    }

    for (;;) {
        struct uid_data *data = NULL;
        ssize_t count = ksu_kernel_read_compat(fp, &chr, sizeof(chr), &pos);
        const char *delim = " ";
        char *package = NULL;
        char *tmp = NULL;
        char *uid = NULL;
        u32 res;

        if (count != sizeof(chr))
            break;
        if (chr != '\n')
            continue;

        count = ksu_kernel_read_compat(fp, buf, sizeof(buf) - 1, &line_start);
        if (count <= 0) {
            break;
        }
        buf[count] = '\0';

        data = kzalloc(sizeof(struct uid_data), GFP_KERNEL);
        if (!data) {
            filp_close(fp, 0);
            goto out;
        }

        tmp = buf;

        package = strsep(&tmp, delim);
        uid = strsep(&tmp, delim);
        if (!uid || !package) {
            pr_err("update_uid: package or uid is NULL!\n");
            kfree(data);
            break;
        }

        if (kstrtou32(uid, 10, &res)) {
            pr_err("update_uid: uid parse err\n");
            kfree(data);
            break;
        }
        data->uid = res;
        strncpy(data->package, package, KSU_MAX_PACKAGE_NAME);
        list_add_tail(&data->list, &uid_list);

        // reset line start
        line_start = pos;
    }

    filp_close(fp, 0);

    if (flags & TRACK_THRONE_PRUNE_ONLY)
        goto prune;

    // unregister all managers first
    ksu_unregister_all_manager();

    pr_info("Searching for manager(s)...\n");
    search_manager("/data/app", 2, &uid_list);
    pr_info("Manager search finished\n");

prune:
    // then prune the allowlist
    ksu_prune_allowlist(is_uid_exist, &uid_list);
out:
    // free uid_list
    list_for_each_entry_safe (np, n, &uid_list, list) {
        list_del(&np->list);
        kfree(np);
    }
    revert_creds(old_cred);
}

void track_throne(unsigned int flags)
{
    struct track_throne_struct *tts = kzalloc(sizeof(struct track_throne_struct), GFP_KERNEL);
    tts->flags = flags;

    if (flags & TRACK_THRONE_FORCE_SYNCHRONOUS) {
        // usecase:
        // renameat & userspace call

        do_track_throne(tts);
    } else {
        ksu_run_in_init_if_possible(do_track_throne, tts);
    }
}

// for 6.8- kernel, we can use LSM hook in manual hook
// 6.8+, we use pkg_observer
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 8, 0) && !defined(CONFIG_KSU_TRACEPOINT_HOOK)
void ksu_handle_rename(struct dentry *old_dentry, struct dentry *new_dentry)
{
    // skip kernel threads
    if (!current->mm) {
        return;
    }

    // skip non system uid
    if (ksu_get_uid_t(current_uid()) != 1000) {
        return;
    }

    if (!old_dentry || !new_dentry) {
        return;
    }

    // /data/system/packages.list.tmp -> /data/system/packages.list
    if (strcmp(new_dentry->d_iname, "packages.list")) {
        return;
    }

    char path[128];
    char *buf = dentry_path_raw(new_dentry, path, sizeof(path));
    if (IS_ERR(buf)) {
        pr_err("dentry_path_raw failed.\n");
        return;
    }

    if (!strstr(buf, "/system/packages.list")) {
        return;
    }

    pr_info("renameat: %s -> %s, new path: %s\n", old_dentry->d_iname, new_dentry->d_iname, buf);

    // after renameat hook, packages.list.tmp -> packages.list
    // don't async for it, or it will always have an race
    // for example,
    // we put track_throne task to init
    // and user install an new app before task_work executed
    // ^ race here
    track_throne(TRACK_THRONE_FROM_RENAMEAT | TRACK_THRONE_FORCE_SYNCHRONOUS);
}
#endif

void __init ksu_throne_tracker_init(void)
{
    // nothing to do
}

void __exit ksu_throne_tracker_exit(void)
{
    // nothing to do
}
''' + "\n" + r'''
/* ---- manager list (ported from Baka-SU/BakaSU kernel/manager/manager.c) ---- */
u16 ksu_last_manager_appid = KSU_INVALID_APPID;
static LIST_HEAD(ksu_manager_appid_list);
static DEFINE_SPINLOCK(ksu_manager_list_write_lock);

struct ksu_manager_node {
    u8 signature_index;
    u16 appid;
    struct list_head list;
    struct rcu_head rcu;
};

void ksu_register_manager(u32 uid, u8 signature_index)
{
    struct ksu_manager_node *node;
    u16 appid;

    if (ksu_is_manager_uid(uid))
        return;

    node = kzalloc(sizeof(*node), GFP_ATOMIC);
    if (unlikely(!node))
        return;

    appid = uid % PER_USER_RANGE;

    node->appid = appid;
    node->signature_index = signature_index;

    spin_lock(&ksu_manager_list_write_lock);

    if (ksu_is_manager_uid(uid)) {
        spin_unlock(&ksu_manager_list_write_lock);
        kfree(node);
        return;
    }

    list_add_tail_rcu(&node->list, &ksu_manager_appid_list);

    spin_unlock(&ksu_manager_list_write_lock);

    if (ksu_last_manager_appid == KSU_INVALID_APPID)
        ksu_last_manager_appid = appid;
    return;
}

void ksu_unregister_manager(u32 uid)
{
    struct ksu_manager_node *node, *pos, *tmp;
    bool mark_another_manager = false;

    if (!ksu_is_manager_uid(uid))
        return;

    u16 appid = uid % PER_USER_RANGE;

    if (ksu_last_manager_appid == appid)
        mark_another_manager = true;

    spin_lock(&ksu_manager_list_write_lock);

    list_for_each_entry_safe (pos, tmp, &ksu_manager_appid_list, list) {
        if (pos->appid == appid) {
            list_del_rcu(&pos->list);
            spin_unlock(&ksu_manager_list_write_lock);
            kfree_rcu(pos, rcu);
            return;
        }

        if (mark_another_manager) {
            ksu_last_manager_appid = appid;
            mark_another_manager = false;
        }
    }

    spin_unlock(&ksu_manager_list_write_lock);

    if (mark_another_manager)
        ksu_last_manager_appid = KSU_INVALID_APPID;
    return;
}

void ksu_unregister_manager_by_signature_index(u8 signature_index)
{
    struct ksu_manager_node *node, *pos, *tmp;
    bool mark_another_manager = false;
    u16 last_each_alive_appid = KSU_INVALID_APPID;

    spin_lock(&ksu_manager_list_write_lock);

    list_for_each_entry_safe (pos, tmp, &ksu_manager_appid_list, list) {
        if (pos->signature_index == signature_index) {
            if (pos->appid == ksu_last_manager_appid) {
                mark_another_manager = true;
            }

            list_del_rcu(&pos->list);
            kfree_rcu(pos, rcu);
        } else {
            last_each_alive_appid = pos->appid;
        }
    }

    spin_unlock(&ksu_manager_list_write_lock);

    if (mark_another_manager)
        ksu_last_manager_appid = last_each_alive_appid;
}

void ksu_unregister_all_manager(void)
{
    struct ksu_manager_node *node, *pos, *tmp;

    spin_lock(&ksu_manager_list_write_lock);

    list_for_each_entry_safe (pos, tmp, &ksu_manager_appid_list, list) {
        list_del_rcu(&pos->list);
        kfree_rcu(pos, rcu);
    }

    spin_unlock(&ksu_manager_list_write_lock);
}

bool ksu_is_manager_uid(u32 uid)
{
    u16 appid = uid % PER_USER_RANGE;

    return ksu_is_manager_appid(appid);
}

bool is_manager(void)
{
    return ksu_is_manager_uid(ksu_get_uid_t(current_uid()));
}

bool ksu_has_manager(void)
{
    bool empty;

    rcu_read_lock();
    empty = list_empty(&ksu_manager_appid_list);
    rcu_read_unlock();

    return !empty;
}

bool ksu_is_manager_appid(u16 appid)
{
    bool found = false;
    struct ksu_manager_node *pos;

    rcu_read_lock();
    list_for_each_entry_rcu (pos, &ksu_manager_appid_list, list) {
        if (pos->appid == appid) {
            found = true;
            break;
        }
    }
    rcu_read_unlock();

    return found;
}

/* KowSU compat: single-appid getter used by supercall debug param */
uid_t ksu_get_manager_appid_compat(void)
{
    if (ksu_has_manager())
        return (uid_t)ksu_last_manager_appid;
    return (uid_t)KSU_INVALID_APPID;
}

int ksu_handle_get_managers_cmd(struct ksu_get_managers_cmd __user *arg, struct ksu_get_managers_cmd *cmd)
{
    struct ksu_manager_node *pos;
    struct ksu_manager_entry *entries;
    u16 max_allowed = cmd->count;
    int count = 0;
    int copy_count;

    entries = kcalloc(max_allowed, sizeof(*entries), GFP_KERNEL);
    if (!entries && max_allowed)
        return -ENOMEM;

    rcu_read_lock();
    list_for_each_entry_rcu (pos, &ksu_manager_appid_list, list) {
        if (count < max_allowed) {
            entries[count].uid = pos->appid;
            entries[count].signature_index = pos->signature_index;
        }

        count++;
    }
    rcu_read_unlock();

    cmd->total_count = count;

    copy_count = min_t(int, count, max_allowed);

    if (copy_count && copy_to_user((char __user *)arg + sizeof(*cmd), entries, copy_count * sizeof(*entries))) {
        kfree(entries);
        return -EFAULT;
    }

    kfree(entries);
    return 0;
}

int ksu_get_manager_signature_index_by_appid(u16 appid)
{
    struct ksu_manager_node *pos;

    rcu_read_lock();
    list_for_each_entry_rcu (pos, &ksu_manager_appid_list, list) {
        if (pos->appid == appid) {
            rcu_read_unlock();
            return pos->signature_index;
        }
    }
    rcu_read_unlock();
    return -ENODATA;
}
''')
log.append(("R manager/throne_tracker.c (+manager list)", newfile))

# 5b. throne_tracker.h [replace — flag defines + track_throne(unsigned)]
p = ksu / "manager" / "throne_tracker.h"
oldh = read(p) if p.is_file() else ""
newh = "TRACK_THRONE_FORCE_SYNCHRONOUS" not in oldh
write(p, r'''#ifndef __KSU_H_THRONE_TRACKER
#define __KSU_H_THRONE_TRACKER

#define TRACK_THRONE_PRUNE_ONLY (1 << 0)
#define TRACK_THRONE_FROM_RENAMEAT (1 << 1)
#define TRACK_THRONE_FORCE_SYNCHRONOUS (1 << 2)

#ifdef CONFIG_KSU_DISABLE_MANAGER
static inline void ksu_throne_tracker_init(void)
{
}

static inline void ksu_throne_tracker_exit(void)
{
}

static inline void track_throne(unsigned int flags)
{
}
#else
void ksu_throne_tracker_init(void);
void ksu_throne_tracker_exit(void);
void track_throne(unsigned int flags);
#endif

#endif
''')
log.append(("R manager/throne_tracker.h", newh))


# 6. pkg_observer.c [replace]
p = ksu / "manager" / "pkg_observer.c"
old = read(p) if p.is_file() else ""
newfile = "TRACK_THRONE_FORCE_SYNCHRONOUS" not in old
write(p, r'''// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/namei.h>
#include <linux/fsnotify_backend.h>
#include <linux/sched.h>
#include <linux/slab.h>
#include <linux/rculist.h>
#include <linux/task_work.h>
#include <linux/version.h>
#include "klog.h" // IWYU pragma: keep
#include "manager/throne_tracker.h"

#define MASK_SYSTEM (FS_CREATE | FS_MOVE | FS_EVENT_ON_CHILD)

struct watch_dir {
    const char *path;
    u32 mask;
    struct path kpath;
    struct inode *inode;
    struct fsnotify_mark *mark;
};

static struct fsnotify_group *g;

static void ksu_track_throne_tw_func(struct callback_head *cb)
{
    kfree(cb);
    // Runs after exit_fs() if the task is exiting; path lookups would oops
    if (current->flags & PF_EXITING)
        return;
    track_throne(TRACK_THRONE_FORCE_SYNCHRONOUS);
}

// Never scan from the fsnotify handler: fsnotify_mark_srcu and the rename's dir
// locks would be held for the whole scan. Defer with task_work, not a kthread
// or kworker: the scan must finish before the rename returns, or the manager
// can be opened before it is crowned, and it must run in the renamer's
// (Android's) namespaces, which differ from init_task's on WSA/Waydroid.
static void ksu_defer_track_throne(void)
{
    struct callback_head *cb;

    if (!(current->flags & PF_KTHREAD)) {
        cb = kzalloc(sizeof(*cb), GFP_KERNEL);
        if (cb) {
            cb->func = ksu_track_throne_tw_func;
            if (!task_work_add(current, cb, TWA_RESUME))
                return;
            kfree(cb);
        }
    }
    pr_warn("defer track_throne failed, run it inline\n");
    track_throne(TRACK_THRONE_FORCE_SYNCHRONOUS);
}

static int ksu_handle_inode_event(struct fsnotify_mark *mark, u32 mask, struct inode *inode, struct inode *dir,
                                  const struct qstr *file_name, u32 cookie)
{
    if (!file_name)
        return 0;
    if (mask & FS_ISDIR)
        return 0;
    if (file_name->len == 13 && !memcmp(file_name->name, "packages.list", 13)) {
        pr_info("packages.list detected: %d\n", mask);
        ksu_defer_track_throne();
    }
    return 0;
}

static const struct fsnotify_ops ksu_ops = {
    .handle_inode_event = ksu_handle_inode_event,
};

static int add_mark_on_inode(struct inode *inode, u32 mask, struct fsnotify_mark **out)
{
    struct fsnotify_mark *m;

    m = kzalloc(sizeof(*m), GFP_KERNEL);
    if (!m)
        return -ENOMEM;

    fsnotify_init_mark(m, g);
    m->mask = mask;

    if (fsnotify_add_inode_mark(m, inode, 0)) {
        fsnotify_put_mark(m);
        return -EINVAL;
    }
    *out = m;
    return 0;
}

static int watch_one_dir(struct watch_dir *wd)
{
    int ret = kern_path(wd->path, LOOKUP_FOLLOW, &wd->kpath);
    if (ret) {
        pr_info("path not ready: %s (%d)\n", wd->path, ret);
        return ret;
    }
    wd->inode = d_inode(wd->kpath.dentry);
    ihold(wd->inode);

    ret = add_mark_on_inode(wd->inode, wd->mask, &wd->mark);
    if (ret) {
        pr_err("Add mark failed for %s (%d)\n", wd->path, ret);
        path_put(&wd->kpath);
        iput(wd->inode);
        wd->inode = NULL;
        return ret;
    }
    pr_info("watching %s\n", wd->path);
    return 0;
}

static void unwatch_one_dir(struct watch_dir *wd)
{
    if (wd->mark) {
        fsnotify_destroy_mark(wd->mark, g);
        fsnotify_put_mark(wd->mark);
        wd->mark = NULL;
    }
    if (wd->inode) {
        iput(wd->inode);
        wd->inode = NULL;
    }
    if (wd->kpath.dentry) {
        path_put(&wd->kpath);
        memset(&wd->kpath, 0, sizeof(wd->kpath));
    }
}

static struct watch_dir g_watch = { .path = "/data/system", .mask = MASK_SYSTEM };

int ksu_observer_init(void)
{
    int ret = 0;

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 0, 0)
    g = fsnotify_alloc_group(&ksu_ops, 0);
#else
    g = fsnotify_alloc_group(&ksu_ops);
#endif
    if (IS_ERR(g))
        return PTR_ERR(g);

    ret = watch_one_dir(&g_watch);
    pr_info("%s done.\n", __func__);
    return 0;
}

void __exit ksu_observer_exit(void)
{
    unwatch_one_dir(&g_watch);
    fsnotify_put_group(g);
    pr_info("%s: done.\n", __func__);
}
''')
# susfs renewal re-injection (10_enable equivalent, survives base replacement)
pkg_now = read(p)
_pkg_changed = False
if "susfs_parse_priority_app_strings" not in pkg_now:
    pkg_now = pkg_now.replace("#include <linux/version.h>", "#include <linux/version.h>\n#include <linux/susfs.h>", 1)
    pkg_now = pkg_now.replace(
        "    track_throne(TRACK_THRONE_FORCE_SYNCHRONOUS);\n}",
        "    track_throne(TRACK_THRONE_FORCE_SYNCHRONOUS);\n"
        "#ifdef CONFIG_KSU_SUSFS_PRIORITY_UID_FILTER\n"
        "    // Renew the priority uid bitmap filter\n"
        "    susfs_parse_priority_app_strings();\n"
        "#endif // #ifdef CONFIG_KSU_SUSFS_PRIORITY_UID_FILTER\n"
        "#ifdef CONFIG_KSU_SUSFS_SUS_PATH\n"
        "    // Renew the sus path uid bitmap filter\n"
        "    susfs_parse_sus_path_app_strings();\n"
        "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_PATH\n"
        "\n"
        "}", 1)
    _pkg_changed = True
if _pkg_changed:
    write(p, pkg_now)
log.append(("I pkg_observer.c susfs renewals", _pkg_changed))


# 7. apk_sign.c multi-sig [canonical full-file replacement]
p = ksu / "manager" / "apk_sign.c"
t = read(p) if p.is_file() else ""
if "apk_sign_keys[]" not in t or "check_v2_signature(char *path, u8 *signature_index)" not in t:
    write(p, r'''#include "util.h"
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/gfp.h>
#include <linux/kernel.h>
#include <linux/limits.h>
#include <linux/slab.h>
#include <linux/version.h>
#ifdef CONFIG_KSU_DEBUG
#include <linux/moduleparam.h>
#endif
#include <crypto/hash.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 11, 0)
#include <crypto/sha2.h>
#else
#include <crypto/sha.h>
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 4, 0)
#include <linux/hex.h>
#endif

#include "manager/apk_sign.h"
#include "manager/manager_sign.h"
#include "uapi/app_profile.h"
#include "klog.h" // IWYU pragma: keep

struct sdesc {
    struct shash_desc shash;
    char ctx[];
};

static struct sdesc *init_sdesc(struct crypto_shash *alg)
{
    struct sdesc *sdesc;
    int size;

    size = sizeof(struct shash_desc) + crypto_shash_descsize(alg);
    sdesc = kzalloc(size, GFP_KERNEL);
    if (!sdesc)
        return ERR_PTR(-ENOMEM);
    sdesc->shash.tfm = alg;
    return sdesc;
}

static int calc_hash(struct crypto_shash *alg, const unsigned char *data, unsigned int datalen, unsigned char *digest)
{
    struct sdesc *sdesc;
    int ret;

    sdesc = init_sdesc(alg);
    if (IS_ERR(sdesc)) {
        pr_info("can't alloc sdesc\n");
        return PTR_ERR(sdesc);
    }

    ret = crypto_shash_digest(&sdesc->shash, data, datalen, digest);
    kfree(sdesc);
    return ret;
}

static int ksu_sha256(const unsigned char *data, unsigned int datalen, unsigned char *digest)
{
    struct crypto_shash *alg;
    char *hash_alg_name = "sha256";
    int ret;

    alg = crypto_alloc_shash(hash_alg_name, 0, 0);
    if (IS_ERR(alg)) {
        pr_info("can't alloc alg %s\n", hash_alg_name);
        return PTR_ERR(alg);
    }
    ret = calc_hash(alg, data, datalen, digest);
    crypto_free_shash(alg);
    return ret;
}

static bool read_exact(struct file *fp, void *buffer, size_t size, loff_t *pos, loff_t end)
{
    if (*pos < 0 || *pos > end || size > (size_t)(end - *pos))
        return false;

    return kernel_read(fp, buffer, size, pos) == (ssize_t)size;
}

static bool read_length_prefixed_end(struct file *fp, loff_t *pos, loff_t container_end, loff_t *value_end)
{
    u32 length;

    if (!read_exact(fp, &length, sizeof(length), pos, container_end))
        return false;
    if (length > INT_MAX || length > (u64)(container_end - *pos))
        return false;

    *value_end = *pos + length;
    return true;
}

static bool check_block(struct file *fp, loff_t *pos, loff_t block_end, u8 *signature_index)
{
    static apk_sign_key_t apk_sign_keys[] = {
        { EXPECTED_SIZE_BAKASU, EXPECTED_HASH_BAKASU }, /* Baka-SU/BakaSU */
        { EXPECTED_SIZE_OFFICIAL, EXPECTED_HASH_OFFICIAL }, /* tiann/KernelSU */
        { EXPECTED_SIZE_KOWX712, EXPECTED_HASH_KOWX712 }, /* KOWX712/KernelSU */
    };
    int sign_i;
    bool key_matched = false;
    (void)key_matched;
    loff_t signers_end, signer_end, signed_data_end, digests_end, certificates_end;
    u32 certificate_size;

    // v2 block: signers sequence -> first signer -> signed data -> digests
    if (!read_length_prefixed_end(fp, pos, block_end, &signers_end) ||
        !read_length_prefixed_end(fp, pos, signers_end, &signer_end) ||
        !read_length_prefixed_end(fp, pos, signer_end, &signed_data_end) ||
        !read_length_prefixed_end(fp, pos, signed_data_end, &digests_end))
        return false;

    *pos = digests_end;
    if (!read_length_prefixed_end(fp, pos, signed_data_end, &certificates_end) ||
        !read_exact(fp, &certificate_size, sizeof(certificate_size), pos, certificates_end))
        return false;

    if (certificate_size > INT_MAX || certificate_size > (u64)(certificates_end - *pos))
        return false;

#define CERT_MAX_LENGTH 1024
    if (certificate_size > CERT_MAX_LENGTH) {
        pr_info("cert length overlimit\n");
        return false;
    }

    char cert[CERT_MAX_LENGTH];
    if (!read_exact(fp, cert, certificate_size, pos, certificates_end))
        return false;

    unsigned char digest[SHA256_DIGEST_SIZE];
    if (ksu_sha256(cert, certificate_size, digest)) {
        pr_info("sha256 error\n");
        return false;
    }

    char hash_str[SHA256_DIGEST_SIZE * 2 + 1];
    hash_str[SHA256_DIGEST_SIZE * 2] = '\0';

    bin2hex(hash_str, digest, SHA256_DIGEST_SIZE);
    for (sign_i = 0; sign_i < (int)(sizeof(apk_sign_keys) / sizeof(apk_sign_keys[0])); sign_i++) {
        if (certificate_size == apk_sign_keys[sign_i].size &&
            strcmp(hash_str, apk_sign_keys[sign_i].sha256) == 0) {
            if (signature_index)
                *signature_index = (u8)sign_i;
            filp_close(fp, 0);
            return true;
        }
    }
    return false;
}

static __always_inline bool check_v2_signature(char *path, u8 *signature_index)
{
    unsigned char buffer[0x10] = { 0 };
    u32 cd_offset, cd_size;
    u32 zip64_locator_magic;
    u64 size_of_block, size_of_block_at_head;

    loff_t pos, pairs_end, file_size, eocd_offset;

    bool v2_signing_valid = false;
    int v2_signing_blocks = 0;

    int i;
    struct file *fp = ksu_filp_open_nonotify(path, O_RDONLY | O_NOATIME);
    if (IS_ERR(fp)) {
        pr_err("open %s error.\n", path);
        return false;
    }

    file_size = generic_file_llseek(fp, 0, SEEK_END);
    if (file_size < 0)
        goto clean;

    // https://en.wikipedia.org/wiki/Zip_(file_format)#End_of_central_directory_record_(EOCD)
    for (i = 0;; ++i) {
        unsigned short comment_size;
        u32 magic;
        pos = file_size - i - 2;
        if (!read_exact(fp, &comment_size, sizeof(comment_size), &pos, file_size))
            goto clean;
        if (comment_size == i) {
            pos -= 22;
            if (!read_exact(fp, &magic, sizeof(magic), &pos, file_size))
                goto clean;
            if (magic == 0x06054b50) {
                eocd_offset = pos - sizeof(magic);
                break;
            }
        }
        if (i == 0xffff) {
            pr_info("error: cannot find eocd\n");
            goto clean;
        }
    }

    // reject ZIP64 before looking for a signing block
    if (eocd_offset >= 20) {
        pos = eocd_offset - 20;
        if (!read_exact(fp, &zip64_locator_magic, sizeof(zip64_locator_magic), &pos, file_size))
            goto clean;
        if (zip64_locator_magic == 0x07064b50)
            goto clean;
    }

    pos = eocd_offset + 12;
    // size of central directory
    if (!read_exact(fp, &cd_size, sizeof(cd_size), &pos, file_size))
        goto clean;
    // offset of central directory
    if (!read_exact(fp, &cd_offset, sizeof(cd_offset), &pos, file_size))
        goto clean;
    if ((u64)cd_offset > (u64)eocd_offset || (u64)cd_size != (u64)eocd_offset - cd_offset)
        goto clean;
    if (cd_offset < 0x20)
        goto clean;

    pairs_end = (loff_t)cd_offset - 0x18;
    pos = pairs_end;

    if (!read_exact(fp, &size_of_block, sizeof(size_of_block), &pos, cd_offset))
        goto clean;
    if (!read_exact(fp, buffer, sizeof(buffer), &pos, cd_offset))
        goto clean;
    if (memcmp((char *)buffer, "APK Sig Block 42", sizeof(buffer)))
        goto clean;

    if (size_of_block < 0x18 || size_of_block > INT_MAX - 0x8 || size_of_block > (u64)cd_offset - 0x8)
        goto clean;

    pos = (loff_t)cd_offset - (loff_t)size_of_block - 0x8;
    if (!read_exact(fp, &size_of_block_at_head, sizeof(size_of_block_at_head), &pos, pairs_end))
        goto clean;
    if (size_of_block_at_head != size_of_block)
        goto clean;

    // Scan every length-prefixed pair, matching AOSP's signing block parser
    // Each valid pair consumes an 8-byte length plus at least a 4-byte ID, so
    // malformed entries fail below instead of spinning in place.
    while (pos < pairs_end) {
        uint32_t id;
        u64 size_of_pair;
        loff_t pair_end;

        if (!read_exact(fp, &size_of_pair, sizeof(size_of_pair), &pos, pairs_end))
            goto invalid;
        if (size_of_pair < sizeof(id) || size_of_pair > INT_MAX || size_of_pair > (u64)(pairs_end - pos))
            goto invalid;

        pair_end = pos + (loff_t)size_of_pair;
        if (!read_exact(fp, &id, sizeof(id), &pos, pair_end))
            goto invalid;

        if (id == 0x7109871au) {
            v2_signing_blocks++;
            v2_signing_valid = check_block(fp, &pos, pair_end, signature_index);
        } else if (id != 0x42726577u) { // APK verity padding
            // https://cs.android.com/android/platform/superproject/+/android-latest-release:tools/apksig/src/main/java/com/android/apksig/internal/apk/ApkSigningBlockUtils.java;l=102;drc=ebe4dfd4fd6550c949a6c7c2427484bf5e96500b
#ifdef CONFIG_KSU_DEBUG
            pr_info("Unexpected signature block id: 0x%08x\n", id);
#endif
            goto invalid;
        }
        pos = pair_end;
    }

    if (v2_signing_blocks != 1) {
#ifdef CONFIG_KSU_DEBUG
        pr_err("Unexpected v2 signature count: %d\n", v2_signing_blocks);
#endif
        v2_signing_valid = false;
    }

    goto clean;

invalid:
    v2_signing_valid = false;
clean:
    filp_close(fp, 0);

    return v2_signing_valid;
}

#ifdef CONFIG_KSU_DEBUG

int ksu_debug_manager_appid = -1;

#include "manager/manager_identity.h"

static int set_expected_size(const char *val, const struct kernel_param *kp)
{
    int rv = param_set_uint(val, kp);
    ksu_set_manager_appid(ksu_debug_manager_appid);
    pr_info("ksu_manager_appid set to %d\n", ksu_debug_manager_appid);
    return rv;
}

static struct kernel_param_ops expected_size_ops = {
    .set = set_expected_size,
    .get = param_get_uint,
};

module_param_cb(ksu_debug_manager_appid, &expected_size_ops, &ksu_debug_manager_appid, S_IRUSR | S_IWUSR);

#endif

int get_pkg_from_apk_path(char *pkg, const char *path)
{
    int len = strlen(path);
    if (len >= KSU_MAX_PACKAGE_NAME || len < 1)
        return -1;

    const char *last_slash = NULL;
    const char *second_last_slash = NULL;

    int i;
    for (i = len - 1; i >= 0; i--) {
        if (path[i] == '/') {
            if (!last_slash) {
                last_slash = &path[i];
            } else {
                second_last_slash = &path[i];
                break;
            }
        }
    }

    if (!last_slash || !second_last_slash)
        return -1;

    const char *last_hyphen = strchr(second_last_slash, '-');
    if (!last_hyphen || last_hyphen > last_slash)
        return -1;

    int pkg_len = last_hyphen - second_last_slash - 1;
    if (pkg_len >= KSU_MAX_PACKAGE_NAME || pkg_len <= 0)
        return -1;

    // Copying the package name
    strncpy(pkg, second_last_slash + 1, pkg_len);
    pkg[pkg_len] = '\0';

    return 0;
}

// /data/app/XXXXX/<PACKAGE_NAME>-YYY, which contains base.apk
int get_pkg_from_apk_dir_path(char *pkg, const char *path)
{
    int len = strlen(path);
    if (len >= KSU_MAX_PACKAGE_NAME || len < 1)
        return -1;

    const char *last_slash = strrchr(path, '/');
    if (!last_slash)
        return -1;

    const char *last_hyphen = strchr(last_slash, '-');
    if (!last_hyphen)
        return -1;

    int pkg_len = last_hyphen - last_slash - 1;
    if (pkg_len >= KSU_MAX_PACKAGE_NAME || pkg_len <= 0)
        return -1;

    // Copying the package name
    memcpy(pkg, last_slash + 1, pkg_len);
    pkg[pkg_len] = '\0';

    return 0;
}

bool is_manager_apk(char *path, u8 *signature_index)
{
    return check_v2_signature(path, signature_index);
}
''')
    log.append(("R manager/apk_sign.c -> canonical multi-sig", True))
else:
    log.append(("R manager/apk_sign.c -> canonical multi-sig", False))

p = ksu / "manager" / "apk_sign.h"
t = read(p)
if "signature_index" not in t:
    t = t.replace("bool is_manager_apk(char *path);", "bool is_manager_apk(char *path, u8 *signature_index);")
    write(p, t)
    log.append(("P manager/apk_sign.h", True))

# 8. uapi/supercall.h additions [patch]
p = uapi_dir / "supercall.h"
t = read(p)
if "ksu_get_managers_cmd" not in t:
    # insert BEFORE the closing #endif so a second #include skips it via the guard
    ei = t.rfind("#endif")
    if ei < 0:
        die("uapi/supercall.h: no #endif found")
    t = t[:ei] + r'''
/* ---- manager compat additions (ported from Baka-SU/BakaSU) ---- */
#define KSU_IOCTL_MANAGE_TRY_UMOUNT KSU_IOCTL_ADD_TRY_UMOUNT /* alias: same slot K,18 */
#define KSU_IOCTL_GET_FULL_VERSION _IOC(_IOC_READ, 'K', 100, 0)
#define KSU_IOCTL_HOOK_TYPE _IOC(_IOC_READ, 'K', 101, 0)
#define KSU_IOCTL_DYNAMIC_MANAGER _IOC(_IOC_READ | _IOC_WRITE, 'K', 103, 0)
#define KSU_IOCTL_GET_MANAGERS _IOC(_IOC_READ | _IOC_WRITE, 'K', 105, 0)
#define KSU_IOCTL_GET_KERNEL_PATCH_IMPLEMENT _IOC(_IOC_READ, 'K', 106, 0)
#define KSU_FULL_VERSION_STRING 255
#define DYNAMIC_MANAGER_OP_SET 0
#define DYNAMIC_MANAGER_OP_GET 1
#define DYNAMIC_MANAGER_OP_WIPE 2
#define DYNAMIC_MANAGER_OP_SET_SYNCHRONOUS 3

struct ksu_manager_entry {
    __u32 uid;
    __u8 signature_index;
} __attribute__((packed));

struct ksu_get_managers_cmd {
    __u16 count;
    __u16 total_count;
    struct ksu_manager_entry managers[];
} __attribute__((packed));

struct ksu_get_full_version_cmd {
    char version_full[KSU_FULL_VERSION_STRING];
};

struct ksu_hook_type_cmd {
    char hook_type[32];
};

struct ksu_dynamic_manager_cmd {
    __u8 operation;
    unsigned int size;
    __u8 hash[64];
};

struct ksu_get_kernel_patch_implement {
    __u8 type;
};

#define KERNEL_PATCH_NOT_FOUND 0
#define KERNEL_PATCH_ORIGINAL 1
#define KERNEL_PATCH_KPN 2
#define KERNEL_PATCH_SUKISU 3
''' + t[ei:]
    write(p, t)
    log.append(("P uapi/supercall.h (+6 structs/consts)", True))
else:
    log.append(("P uapi/supercall.h (+6 structs/consts)", False))

# 9. supercall/dispatch.c handlers + rows [patch]
p = ksu / "supercall" / "dispatch.c"
t = read(p)
changed = False
if "do_get_full_version" not in t:
    m = re.search(r"// IOCTL handlers mapping table", t)
    if not m:
        die("dispatch.c: anchor '// IOCTL handlers mapping table' not found")
    i = m.start()
    t = t[:i] + r'''
/* ---- manager compat handlers (ported from Baka-SU/BakaSU) ---- */
u8 kernel_patch_type = KERNEL_PATCH_NOT_FOUND;

static int do_get_full_version(void __user *arg)
{
    struct ksu_get_full_version_cmd cmd = { 0 };
#ifndef KSU_VERSION_FULL
#define KSU_VERSION_FULL_FALLBACK
    const char *ver = "v3.x-KowSU-multi@amnsjntm";
#else
    const char *ver = KSU_VERSION_FULL;
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 13, 0)
    strscpy(cmd.version_full, ver, sizeof(cmd.version_full));
#else
    strlcpy(cmd.version_full, ver, sizeof(cmd.version_full));
#endif
#ifdef KSU_VERSION_FULL_FALLBACK
#undef KSU_VERSION_FULL_FALLBACK
#endif
    if (copy_to_user(arg, &cmd, sizeof(cmd))) {
        pr_err("get_full_version: copy_to_user failed\n");
        return -EFAULT;
    }
    return 0;
}

static int do_get_hook_type(void __user *arg)
{
    struct ksu_hook_type_cmd cmd = { 0 };
#if defined(CONFIG_KSU_SUSFS)
    const char *type = "Inline (susfs)";
#elif defined(CONFIG_KSU_TRACEPOINT_HOOK)
    const char *type = "Tracepoint Syscall Redirect";
#else
    const char *type = "Security Hooks";
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 13, 0)
    strscpy(cmd.hook_type, type, sizeof(cmd.hook_type));
#else
    strlcpy(cmd.hook_type, type, sizeof(cmd.hook_type));
#endif
    if (copy_to_user(arg, &cmd, sizeof(cmd))) {
        pr_err("get_hook_type: copy_to_user failed\n");
        return -EFAULT;
    }
    return 0;
}

static int do_dynamic_manager(void __user *arg)
{
#ifdef CONFIG_KSU_DISABLE_MANAGER
    return -EOPNOTSUPP;
#else
    struct ksu_dynamic_manager_cmd cmd;

    if (copy_from_user(&cmd, arg, sizeof(cmd))) {
        pr_err("dynamic_manager: copy_from_user failed\n");
        return -EFAULT;
    }

    int ret = ksu_handle_dynamic_manager(&cmd);
    if (ret)
        return ret;

    if (copy_to_user(arg, &cmd, sizeof(cmd))) {
        return -EFAULT;
    }
    return 0;
#endif
}

extern int ksu_handle_get_managers_cmd(struct ksu_get_managers_cmd __user *arg, struct ksu_get_managers_cmd *cmd); /* defined in manager/throne_tracker.c */

static int do_get_managers(void __user *arg)
{
    struct ksu_get_managers_cmd cmd;

    if (copy_from_user(&cmd, arg, sizeof(struct ksu_get_managers_cmd))) {
        return -EFAULT;
    }

#ifndef CONFIG_KSU_DISABLE_MANAGER
    int ret = ksu_handle_get_managers_cmd(arg, &cmd);
    if (ret) {
        return ret;
    }
#else
    cmd.count = 0;
    cmd.total_count = 0;
#endif

    if (copy_to_user(arg, &cmd, sizeof(struct ksu_get_managers_cmd))) {
        return -EFAULT;
    }

    return 0;
}

static int do_get_kernel_patch_implement(void __user *arg)
{
    struct ksu_get_kernel_patch_implement cmd = { 0 };
#ifdef CONFIG_ARM64
    cmd.type = kernel_patch_type;
#else
    cmd.type = KERNEL_PATCH_NOT_FOUND;
#endif
    if (copy_to_user(arg, &cmd, sizeof(cmd))) {
        pr_err("get_kernel_patch_implement: copy_to_user failed\n");
        return -EFAULT;
    }
    return 0;
}
''' + "\n\n" + t[i:]
    changed = True
if "KSU_IOCTL_GET_FULL_VERSION," not in t:
    m = re.search(r"\{\s*\n\s*\.cmd = KSU_IOCTL_GRANT_ROOT", t)
    if not m:
        die("dispatch.c: GRANT_ROOT row anchor not found")
    i = m.start()
    t = t[:i] + r'''
    {
        .cmd = KSU_IOCTL_GET_FULL_VERSION,
        .name = "GET_FULL_VERSION",
        .handler = do_get_full_version,
        .perm_check = manager_or_root
    },
    {
        .cmd = KSU_IOCTL_HOOK_TYPE,
        .name = "GET_HOOK_TYPE",
        .handler = do_get_hook_type,
        .perm_check = manager_or_root
    },
    {
        .cmd = KSU_IOCTL_DYNAMIC_MANAGER,
        .name = "DYNAMIC_MANAGER",
        .handler = do_dynamic_manager,
        .perm_check = manager_or_root
    },
    {
        .cmd = KSU_IOCTL_GET_MANAGERS,
        .name = "GET_MANAGERS",
        .handler = do_get_managers,
        .perm_check = manager_or_root
    },
    {
        .cmd = KSU_IOCTL_GET_KERNEL_PATCH_IMPLEMENT,
        .name = "GET_KERNEL_PATCH_IMPLEMENT",
        .handler = do_get_kernel_patch_implement,
        .perm_check = manager_or_root
    },
    {
        .cmd = KSU_IOCTL_MANAGE_TRY_UMOUNT,
        .name = "MANAGE_TRY_UMOUNT",
        .handler = add_try_umount,
        .perm_check = manager_or_root
    },
''' + "\n" + t[i:]
    changed = True
if "feature/dynamic_manager.h" not in t:
    t = t.replace('#include "supercall/supercall.h"', '#include "supercall/supercall.h"\n#include "feature/dynamic_manager.h"\n#include "manager/manager_identity.h"', 1)
    changed = True
if changed:
    write(p, t)
log.append(("P supercall/dispatch.c (+handlers/rows)", changed))

# 10. Kbuild [patch]
p = ksu / "Kbuild"
t = read(p)
changed = False
if "dynamic_manager.o" not in t:
    t = t.replace("kernelsu-objs += feature/adb_root.o",
                  "kernelsu-objs += feature/adb_root.o\nkernelsu-objs += feature/dynamic_manager.o")
    changed = True
if "KSU_VERSION_FULL" not in t:
    t += "\n" + r'''# manager compat: full version string for GET_FULL_VERSION ioctl
KSU_DEFCONFIG := $(srctree)/arch/arm64/configs/gki_defconfig
KSU_FULL_NAME_FMT := $(shell grep -oP '(?<=CONFIG_KSU_FULL_NAME_FORMAT=")[^"]*' $(KSU_DEFCONFIG) 2>/dev/null)
ifneq ($(KSU_FULL_NAME_FMT),)
KSU_FULL_VERSION_GEN := $(subst %KSU_VERSION%,$(KSU_VERSION),$(KSU_FULL_NAME_FMT))
else
KSU_FULL_VERSION_GEN := v3.x-KowSU-$(KSU_VERSION)@amnsjntm
endif
ccflags-y += -DKSU_VERSION_FULL=\"$(KSU_FULL_VERSION_GEN)\"
'''
    changed = True
if changed:
    write(p, t)
log.append(("P Kbuild (+dynamic_manager.o, KSU_VERSION_FULL)", changed))

# ---------------- verification ----------------
fails = []
def need(tag, text, needles):
    for n in needles:
        if n not in text:
            fails.append("%s missing %r" % (tag, n))

need("manager_sign.h", read(ksu / "manager" / "manager_sign.h"),
     ["EXPECTED_SIZE_BAKASU", "EXPECTED_HASH_KOWX712", "apk_sign_key_t"])
need("manager_identity.h", read(ksu / "manager" / "manager_identity.h"),
     ["ksu_register_manager(u32 uid, u8 signature_index)", "is_uid_manager", "ksu_has_manager"])
need("throne_tracker.c", read(ksu / "manager" / "throne_tracker.c"),
     ["crown_manager", "ksu_handle_get_managers_cmd", "ksu_manager_appid_list"])
need("apk_sign.c", read(ksu / "manager" / "apk_sign.c"),
     ["manager_sign.h", "apk_sign_keys", "u8 *signature_index", "get_pkg_from_apk_path"])
need("uapi/supercall.h", read(uapi_dir / "supercall.h"),
     ["ksu_get_full_version_cmd", "ksu_hook_type_cmd", "ksu_dynamic_manager_cmd",
      "ksu_get_managers_cmd", "ksu_get_kernel_patch_implement", "DYNAMIC_MANAGER_OP_SET"])
need("dispatch.c", read(ksu / "supercall" / "dispatch.c"),
     ["do_get_full_version", "do_get_hook_type", "do_dynamic_manager", "do_get_managers",
      "do_get_kernel_patch_implement", "KSU_IOCTL_GET_FULL_VERSION", "KSU_IOCTL_GET_MANAGERS",
      "MANAGE_TRY_UMOUNT"])
need("Kbuild", read(ksu / "Kbuild"), ["dynamic_manager.o", "KSU_VERSION_FULL"])
if not (ksu / "feature" / "dynamic_manager.c").is_file():
    fails.append("feature/dynamic_manager.c missing")

apk = read(ksu / "manager" / "apk_sign.c")
if re.search(r"check_v2_signature\(path, EXPECTED_SIZE, EXPECTED_HASH\)", apk):
    fails.append("apk_sign.c residue: single-signature call")

print()
for name, ok in log:
    print("%-52s %s" % (name, "OK" if ok else "skip (already applied)"))
print()
if fails:
    print("=== RESIDUE DETECTED (%d) ===" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("verification passed: multi-manager + extra ioctls wired")
