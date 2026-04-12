---
id: gotcha-libvirt-provider-v09-migration
type: gotcha
title: "Gotcha: dmacvicar/libvirt provider v0.9.x API is completely different from v0.7/0.8"
date: 2026-04-09
tags: [L1-data-infrastructure, discovery]
related:
  - journey/04-vm-provisioning
  - gotchas/grub-acpi-apic
one_line: "dmacvicar/libvirt v0.9.7 is not v0.7 or v0.8 — every resource attribute changed. The correct approach is 'tofu providers schema -json' against the installed version, not copying examples from old docs."
---

# Gotcha: dmacvicar/libvirt provider v0.9.x API is completely different from v0.7/0.8

## Symptom
Every resource in a config written for the old provider version fails:
```
An argument named "source" is not expected here.
An argument named "format" is not expected here.
An argument named "base_volume_id" is not expected here.
The argument "type" is required, but no definition was found.
Blocks of type "disk" are not expected here.
```

## Root cause
The `dmacvicar/libvirt` provider v0.9.x was a major rewrite that maps
the HCL schema 1:1 to the libvirt XML structure, rather than providing
the higher-level abstractions of v0.7/v0.8. Almost every resource has
a different API.

## Migration table

| v0.7/0.8 | v0.9.x |
|---|---|
| `libvirt_volume.source = "<path>"` | `libvirt_volume.create = { content = { url = "<path>" } }` |
| `libvirt_volume.format = "qcow2"` | `libvirt_volume.target = { format = { type = "qcow2" } }` |
| `libvirt_volume.base_volume_id = <id>` | `libvirt_volume.backing_store = { path = <path>, format = { type = "qcow2" } }` |
| `libvirt_volume.size = <bytes>` | `libvirt_volume.capacity = <bytes>` |
| `libvirt_cloudinit_disk.pool = "<pool>"` | Removed. Use `path` or upload to a volume separately. |
| `libvirt_cloudinit_disk.meta_data` (optional) | `meta_data` is now **REQUIRED** |
| `libvirt_domain.cloudinit = <id>` | Removed. Upload ISO into a `libvirt_volume`, attach as cdrom `disk` device. |
| `libvirt_domain.memory = 4096` (MiB implied) | `memory = 4096` + `memory_unit = "MiB"` (defaults to KiB!) |
| `libvirt_domain { disk { ... } }` | `devices = { disks = [{ ... }] }` |
| `libvirt_domain { network_interface { ... } }` | `devices = { interfaces = [{ type = "network", source = { network = { network = "default" } }, model = { type = "virtio" } }] }` |
| `libvirt_domain { console { ... } }` | `devices = { consoles = [{ ... }] }` |
| `libvirt_domain { graphics { ... } }` | `devices = { graphics = [{ vnc = { auto_port = true, listen = "..." } }] }` |
| (inferred from os type) | `type = "kvm"` is now **REQUIRED** |
| `network_interface[0].addresses[0]` output | Removed. Use `virsh domifaddr` or `libvirt_domain_interface_addresses` data source. |
| `boot_devices = ["hd"]` | `boot_devices = [{ dev = "hd" }]` (list of objects, not strings) |

## How we figured it out

1. `tofu providers schema -json` dumps the full schema from the installed provider
2. Extracted attribute names and types with a Python script
3. For complex nested types (devices, os), extracted the full JSON structure
4. Cross-referenced with the provider's GitHub repo examples
5. Iterated: validate → fix → validate until clean

## Critical: features and CPU mode

The v0.9.x provider does NOT set `features { acpi; apic }` or
`cpu { mode = "host-passthrough" }` by default. Without these, modern
Linux guests hang during GRUB boot. See `grub-acpi-apic.md`.

## How to prevent
- Don't assume any attribute names from the old provider docs
- Always run `tofu validate` before `tofu apply`
- Use `tofu providers schema -json` as the authoritative reference
- Start with a minimal working config and add features incrementally

## Environment
- dmacvicar/libvirt provider v0.9.7
- OpenTofu v1.11.6
