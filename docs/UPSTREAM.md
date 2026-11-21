# 上游特性合入计划与技术来源

# 上游特性合入执行计划（2026-11-21）

## 用户指令
A1/A2/A3 直接做，B5 做，其余从上游仓库复用适配。zram 回退 =m 并把修好的 ko 放回 AK3。

## A 档任务（成品直接可用）

### A1 crystal_hybridswap WebUI
- 状态：待资产下载（github release 资产网络间歇）
- 步骤：下载 crystal_hybridswap_webui-v0.7.0.zip → 解包审查（是否依赖内核 crystal 数据面）→ 作为独立模块交付，不混入 AK3
- 注意：内核侧无 crystal 数据面时 WebUI 是空壳；交付物需注明

### A2 风驰 sysctl 预设
- 状态：待资产下载（从 C AK3 成品内提取 hmbird_sched 初始化序列）
- 步骤：解包 C_ak3.zip → 找 anykernel.sh/配置段的 /proc/hmbird_sched/* 写入序列 → 对比我们设备上已有节点（30个接口已在）→ 取差异做成可选初始化脚本

### A3 le9uo/kcompressd/UKSM 参数
- 现状核实（6.6.118 树）：
  - UKSM：**源码不存在**（mm/ 只有原生 ksm.c，无 uksm）→ 需从 sroeschus/uksm 拿 6.6 补丁合入（上游确认存在）
  - le9uo：源码不存在（workingset.c 是原生）→ 需从 firelzrd/le9uo 拿补丁（上游确认存在，"PATCH mm: Protect the working set..."）
  - kcompressd：firelzrd/kcompressd-unofficial 存在（fork 自原版）→ 源码可拿
- 步骤：三补丁逐个拉取 → 6.6.118 适配（可能有小冲突）→ Kconfig 开关 → CI 验证

## B 档任务

### B5 网络栈调优
- BBRv3：CachyOS 无 6.6 分支（最新 6.1 系），google group 确认 BBRv3 回移 6.6 是多补丁复杂工作 → **降级方案**：先做 FQ-CoDel 默认 + ECN + TCP 路径参数（纯 defconfig/sysctl 层），BBRv3 单独立项评估
- 步骤：defconfig 抄 C 的网络配置（FQ-CoDel、ECN、TCP 参数）→ CI 验证

## zram 修复（进行中）
- workflow 已回退 CONFIG_ZRAM=m + make modules + zram.ko 打包进 AK3（commit 32902db）
- CI run 44867780520 编译中
- 产物到手后：AK3 内含本内核配套 zram.ko → 刷入后 zram_adaptive 模块的 insmod 将使用配套 ko（不再跨版本）

## 从上游直接复用（合入方式记录到 技术来源记录.md）
- sroeschus/uksm（UKSM 6.6 补丁，MIT/GPL 上游）
- firelzrd/le9uo（mm 工作集保护补丁）
- firelzrd/kcompressd-unofficial（异步 swap 压缩）
- CachyOS/linux（BBRv3，仅 6.12+，6.6 回移另行评估）


---

# 技术来源记录

# 内核技术来源记录（ABC 包技术合入溯源）

> 原则：所有合入本仓库的技术/补丁/配置均记录来源，尊重原作者，避免白嫖。

## A 包（schqiushui/SpiderDroid 系）
来源：AK3-6.6.118-g3503be649c0b-Oplus-sun-gki-35795(UAPI6)-2026102.zip 与
     AK3-6.6.118-gbcc961a211fc-Oplus-sun-gki-35833(UAPI6)-2026112.zip（作者发布包）
已合入：
| 技术 | 来源 | 说明 |
|---|---|---|
| ak3-core.sh 20260809 版（vendor_ramdisk 支持） | A 包 tools/ | 替换 B 包旧版，修复 $BIN/mk 笔误 |
| magiskboot / magiskpolicy 新版二进制 | A 包 tools/ | 与 ak3-core 配套 |
| KPM 音量键选择流程（patch_android） | A 包 anykernel.sh | 未合入（B 内核用内置 KernelPatch，不适用）|
| patch_android 二进制 | A 包 tools/ | 未合入（同上）|

## B 包（amnsjntm/OOR-sm8750-MTKcopy，本仓库上游）
来源：BakaSU.6.6.118-abogki500782043-lotus-4k.35252.zip（Baka-SU/BakaSU main 派生）
本仓库大部分基线来自此包：Image、AK3 骨架、zram.zip 模块、BBG、ADIOS、ipset 配置等。

## 本仓库新增修复（amnsjntm fork 自己的提交）
| 修复 | 提交 | 说明 |
|---|---|---|
| 001-lz4.patch 修正（lz4hc 删除 hunk 行数） | fix: repair build workflow... | 原补丁少算 2 行导致整体失败 |
| 002-zstd.patch 修正（portability hunk3 重生成+移除该段） | 同上+fix: drop portability hunk... | 修 zstd_internal.h highbit 重复定义编译错 |
| IP6_NF_NAT 显值隐藏理解（config.patch 机制） | 调研结论 | =y 被隐藏为 =n 但功能在，勿改 =m |
| BakaSU 锁定 d5e9e025（35252） | fix: lock BakaSU... | 上游 10 commit 引入 SELinux 检测暴露 |
| susfs4oki 锁定 32ff29a（v2.3.0） | fix: lock susfs4oki... | v2.4.0 selinux_hide 重构触发 hunter KERNELSU_EXCLUSIVE_CONTEXT |
| Unicode 零宽字符修复 | feat: add Unicode zero-width... | 来自 skk0042/Unicode_bypass_Fix（上游 commit 5495b94 by Gabriel Krisman Bertazi，钱钱@realaviraxp 修正版）|
| zram.ko 打包 | feat/fix: bundle zram.ko... | 修复 zram_adaptive 模块 5.15 ko 跨版本 insmod |

## 外部参考（未直接合码，仅思路）
| 来源 | 用途 |
|---|---|
| whitewhale0612/android_kernel_common_oneplus_sm8750-C16 (C 包) | LZ4KD 读回稳定性问题佐证；Crystal HybridSwap/LZ4KDS 源码闭源（Close but Free Release），未合码 |
| OnePlusOSS/android_kernel_oneplus_sm8750 issue #24 | 证实官方树 HMBIRD hook 缺失问题（GPL 合规）|
| luyancib-org/lunarkernel_sched_extention (LSE) | 结论：本树已内建完整 hmbird，LSE 无需移植 |
| Baka-SU/BakaSU | KSU 驱动上游（版本=commit 数+30700）|
| cctv18/susfs4oki | susfs 补丁上游 |

## 风驰调度结论
CONFIG_HMBIRD_SCHED=y 三包（A/原始B/本仓库）均已启用且设备实测运行
（/proc/hmbird_sched 30 接口、DT type=HMBIRD_OGKI、oplus_bsp_sched_ext+walt gov），
运行开关 scx_enable 由 ColorOS 系统侧控制——不手动开启。
