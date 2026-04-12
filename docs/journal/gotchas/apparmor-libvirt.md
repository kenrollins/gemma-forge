---
id: gotcha-apparmor-libvirt
type: gotcha
title: "Gotcha: AppArmor blocks libvirt QEMU on custom storage pool paths"
date: 2026-04-09
tags: [L1-data-infrastructure, discovery]
related:
  - journey/04-vm-provisioning
one_line: "AppArmor blocks QEMU from opening qcow2 images on custom pool paths unless virt-aa-helper has sys_admin and sys_resource capabilities, or a local AppArmor override is in place."
---

# Gotcha: AppArmor blocks libvirt QEMU on custom storage pool paths

## Symptom
`virsh start` or `tofu apply` fails with:
```
Could not open '/data/vm/gemma-forge/pool/rocky9-base.qcow2': Permission denied
```
Even though the file is owned by `libvirt-qemu:kvm` with `rw-------` permissions.

## Root cause
Two overlapping AppArmor issues on Ubuntu 24.04:

1. **`virt-aa-helper` lacks `sys_admin` and `sys_resource` capabilities.**
   This tool generates per-VM AppArmor profiles that allow QEMU to
   access specific disk files. Without these capabilities, it generates
   incomplete profiles → QEMU can't read the disks.

2. **The default libvirt-qemu AppArmor abstraction doesn't include
   custom pool paths.** Standard paths (`/var/lib/libvirt/images/`)
   are allowed, but `/data/vm/gemma-forge/pool/` is not.

## Fix

```bash
# Fix 1: virt-aa-helper capabilities
echo 'capability sys_admin,
capability sys_resource,' | sudo tee /etc/apparmor.d/local/usr.lib.libvirt.virt-aa-helper

# Fix 2: Custom pool path access for QEMU
echo '/data/vm/gemma-forge/pool/** rwk,' | sudo tee /etc/apparmor.d/local/abstractions/libvirt-qemu

# Reload the affected profile
sudo apparmor_parser -r /etc/apparmor.d/usr.lib.libvirt.virt-aa-helper

# Restart libvirtd to regenerate per-VM profiles
sudo systemctl restart libvirtd
```

## How to prevent
Document in `docs/host-setup.md`. Any host using a non-standard
libvirt storage pool path on Ubuntu needs these AppArmor overrides.

## Environment
- Ubuntu 24.04.3 LTS
- libvirt 10.0.0
- AppArmor enforcing mode
