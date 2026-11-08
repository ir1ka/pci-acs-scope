# PCI ACS Scope 0.5.0（打包修订 6）

[English](README.md) | **简体中文**

**发行版验证范围：目前仅在 Debian 13 上测试过。其他发行版（包括 Ubuntu）尚未验证。

独立管理选定 PCIe port 子树的 ACS 策略。支持多个 root/upstream/downstream port，默认排除所选 port 本身，只作用于其后代。

## 快速使用

源码支持两个互斥安装包，安装后均默认不启用策略。首次公开发行仅提供 DKMS 包；native 包可自行构建。

| 安装包 | 实现 | 开机方式 |
| --- | --- | --- |
| `pci-acs-scope-dkms` | 自有内核模块，DKMS 随内核更新编译 | 显式 opt-in 的 initramfs udev 前入口 |
| `pci-acs-scope-native` | 内核自带 `disable_acs_redir` / `config_acs` | 独立 GRUB 配置，不加载自有模块 |

```bash
sudo apt install ./pci-acs-scope-dkms_0.5.0-6_all.deb

# 显式启用，下次启动生效；不立即改写设备
sudo pci-acs-boot enable --port 0000:00:03.1
# 关闭后续启动配置
sudo pci-acs-boot disable
```

首次公开发行只提供 DKMS 包；源码仍保留显式目标 ABI 的预编译构建接口，供需要者自行构建。

## 范围和策略

`--port A --include --port B --port C --include` 表示包含 A、C 自身，而排除 B 自身。多个边界重叠时仍按各边界的 include 选项决定。支持逗号/空格列表的 `--ports` 兼容接口。

默认 `--policy redirect` 对应内核 `disable_acs_redir` 的 RR/CR/EC 控制；显式 `--policy off` 清除标准 ACS 位 0–6。原生后端的 `off` 需要 `config_acs`。没有相关 ACS capability 的功能不加入参数，已关闭但支持相应控制的功能仍加入，因为内核启动时可能重新设置。

原生路径以 root BDF 锚定，后续各级只使用 device.function，例如 `0000:00:03.1/00.0/08.0/00.0/00.0`，避免下游 bus 重编号影响。路径是精确设备匹配，不是子树通配；新增桥或实际连接变化后执行 `sudo pci-acs-boot refresh`。生成的 `/etc/default/grub.d/60-pci-acs-scope.cfg` 对完整参数加引号，保留分号到 grub.cfg，避免被解释成 GRUB 命令分隔符。

## 安装检查和升级

native 包在 preinst 内嵌自足的检测器，不能依赖尚未解包的文件。它逐一检查 `/boot` 中已安装内核的实际 ELF，分别报告 `disable_acs_redir`、`config_acs` 和路径选择器证据；所有目标内核至少支持一种 redirect 实现才允许安装。检查失败拒绝安装，不安装模块替代。Python 与所需外部解压工具通过 Pre-Depends 保证在 preinst 前可用。版本号用于识别目标 ABI，不以版本号猜测发行版回移能力。

启用时再次按策略核对能力，并要求可管理的 GRUB。两种包互斥且包含冲突文件声明。更换实现先移除旧包的显式启动配置，再由用户对新包重新 enable。同一种包正常升级会保留 opt-in 并刷新自己的配置。模块包支持 initramfs-tools；native 包不添加 initramfs hook。kernel postinst/postrm 只刷新本项目配置。

## 模块包单次执行与恢复

```bash
sudo pci-acs-scope disable --port 0000:00:03.1 --save /var/tmp/acs-before-new.json
sudo pci-acs-scope restore
```

仅模块包支持即时 disable/restore/reapply。保存原值后应用策略，失败回滚；仍有待恢复记录时持有模块引用，普通 rmmod 被拒绝，须先 restore。恢复失败保留内核快照。只对原 pci_dev 恢复，移除/替换设备通过显式 refresh 清理原记录，不写替代设备。强制卸载不受引用保护。

所有包均支持只读 `status`、`audit`、`native-plan`；`pci-acs-boot audit` 核对已启用的开机策略。可选 `audit-enable/audit-disable` 控制定时审计，审计只报告漂移，不自动写 PCI 配置。设备重置、挂起恢复、热插拔之后应重新审计；运行时 reapply 必须先使子树空闲。

## 构建和验证

```bash
make check
make deb               # 同时生成两个互斥包；选择一个安装
```

构建不安装、不加载主机模块。自有模块使用原版内核 headers，通过 DKMS 构建，无需重编内核。内核参数在 PCI 初始化阶段生效；后加载模块不会重建已有 IOMMU group。不能将关闭 ACS 等同于已证明 P2P 流量路径。


关闭 ACS 重定向会改变 PCIe 访问隔离，不应在不受信任的设备/租户之间使用。它不是强制启用 CUDA P2P 的开关，设备驱动仍须支持相应路径。

## 卸载

```sh
sudo pci-acs-boot disable
# 模块包若执行过临时策略，应先 restore
sudo pci-acs-scope restore
sudo apt remove pci-acs-scope-dkms
# native 用户移除 pci-acs-scope-native，不执行运行时 restore
```

native 参数的移除在后续正常启动生效；本工具不自动重启。构建依赖为 Python 3、Git、make、编译器、debhelper、dh-sequence-dkms；模块构建另需精确目标 headers。Secure Boot 使用目标主机信任的 DKMS 密钥。

## 交流与维护

维护者：IriKa Qiu（[ir1ka](https://github.com/ir1ka)）。通过 [Issues](https://github.com/ir1ka/pci-acs-scope/issues) 报告问题与讨论，通过 [Pull Requests](https://github.com/ir1ka/pci-acs-scope/pulls) 提交改进。安全问题请先请求私密渠道，不要在公开 issue 中附上漏洞细节或密钥。

本项目采用 GPL v2；保留所有第三方原始许可证和署名。
