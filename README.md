# OOR-sm8750-MTKcopy 欧加真 SM8750 通用 GKI 内核（BakaSU 定制版）

本仓库是 [cctv18/oppo_oplus_realme_sm8750](https://github.com/cctv18/oppo_oplus_realme_sm8750) 的 fork，
面向欧加真（OPPO / 一加 / 真我）骁龙 SM8750 设备，基于 BakaSU（KernelSU 下游）构建
带 susfs 隐藏的 6.6.118 GKI 内核 AnyKernel3 卡刷包。

## 与上游的差异（本 fork 的改动）

本 fork 在上游基础上做了一系列修复与定制，全部改动记录在 commit 历史里：

### 构建链路修复
| 修复 | 说明 |
|---|---|
| `zram_patch/001-lz4.patch` | 原 lz4 1.10.0 补丁因 lz4hc_compress.c 删除 hunk 行数不符整体应用失败，按 6.6.118 实际源码重生成 |
| `zram_patch/002-zstd.patch` | 移除 portability_macros.h 段（导致 zstd_internal.h highbit 重定义编译错），保留 zstd 1.5.7 其余部分 |
| zram.ko 打包 | 编译 `drivers/block/zram/zram.ko` 并打进 AK3 包，vermagic 与本内核完全匹配 |
| anykernel.sh 注入 | 刷入时自动把 zram_adaptive 模块里的旧版 zram.ko 替换为本内核配套版（旧版备份为 zram.ko.bak.5.15）|

### 稳定性锁定（防检测）
| 锁定 | 原因 |
|---|---|
| BakaSU 锁定 commit `d5e9e025`（KSU 35252） | 上游后续 10 个 commit 引入的用户态变更会触发 hunter 的 KERNELSU_EXCLUSIVE_CONTEXT 检测点 |
| susfs4oki 锁定 commit `32ff29a`（susfs v2.3.0） | v2.4.0 的 selinux_hide 重构与 BakaSU 组合会暴露同一检测点 |

> 检测点机制说明：hunter 检测的不是 ksu_file 类型本身，而是 BakaSU 系的
> ksu_lsm_hook 运行时补丁框架留下的专属符号。KowSU/官方 KSU 线（标准 security_hook
> 注册）无此痕迹——如需彻底无检测点需迁移 KSU 分支。

### 功能开关
| 特性 | 状态 |
|---|---|
| susfs 全家桶（含 SUS_MAP） | ✅ |
| lz4 1.10.0 / lz4kd / lz4k / zstd 1.5.7 / 842 | ✅ |
| ADIOS IO 调度器（默认） | ✅ |
| BBG 基带保护 | ✅ |
| ipset / BPF 流解析 / 网络增强 | ✅（better_net=true 时） |
| FQ-CoDel/FQ qdisc + TCP 调优 | ✅ |
| 原生 KSM 内存去重 | ✅（新增，供观察合并收益） |
| 风驰 HMBIRD 调度 | ✅ 内建（CONFIG_HMBIRD_SCHED=y，由 ColorOS 系统侧控制 scx_enable）|
| Unicode 零宽字符任意文件读取修复 | ✅（other_patch/unicode_bypass_fix.patch）|
| KPM / BBR 默认 / Re-Kernel / Droidspaces | ❌ 未启用（workflow 可选）|

## 技术来源记录

所有合入技术的来源（作者/仓库/用途）见各 commit message 与
[上游特性合入计划](docs/UPSTREAM.md)。致谢：

- [Baka-SU/BakaSU](https://github.com/Baka-SU/BakaSU) — KSU 驱动上游
- [cctv18/susfs4oki](https://github.com/cctv18/susfs4oki) — susfs 补丁
- [ShirkNeko/SukiSU_patch](https://github.com/ShirkNeko/SukiSU_patch) — lz4kd 补丁
- [skk0042/Unicode_bypass_Fix](https://github.com/skk0042/Unicode_bypass_Fix)（上游补丁作者 Gabriel Krisman Bertazi，修正 钱钱@realaviraxp）— 零宽字符修复
- [cctv18/Baseband-guard](https://github.com/cctv18/Baseband-guard) — BBG
- 上游 fork 来源 [cctv18/oppo_oplus_realme_sm8750](https://github.com/cctv18/oppo_oplus_realme_sm8750)

## 使用

Actions 页手动触发 `fastbuild_6.6.118.yml`，常用参数：
- `ksu_type=bakasu`、`susfs_enable=true`、`lz4_enable=true`、`lz4kd_enable=true`
- `better_net=true`（ipset/IPv6 NAT 等网络增强，**必须开**否则 ip6table_nat 缺失）
- `adios_enable=true`、`baseband_guard=true`

产物：AnyKernel3 卡刷包（Recovery / HorizonKernelFlasher / KSU 管理器均可刷入）。
刷机前务必备份 boot 分区。

## 说明文档

- `docs/UPSTREAM.md` — 上游特性合入计划与评估（UKSM/le9uo/kcompressd/BBRv3）
- 构建日志与质检记录在 Actions 运行详情

## 免责声明

刷写自定义内核有风险，可能变砖或导致 Play Integrity 校验失败。刷入前务必备份，
因使用本内核造成的损失作者概不负责。
