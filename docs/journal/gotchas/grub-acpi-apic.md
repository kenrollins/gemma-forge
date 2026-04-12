---
id: gotcha-grub-acpi-apic
type: gotcha
title: "Gotcha: GRUB hangs without ACPI/APIC features in the libvirt domain"
date: 2026-04-09
tags: [L1-data-infrastructure, discovery]
related:
  - journey/04-vm-provisioning
  - gotchas/libvirt-provider-v09-migration
one_line: "libvirt provider v0.9.7 does not set acpi/apic features by default; a VM without them will GRUB-hang on boot. Explicit features = { acpi = true; apic = {} } is required."
---

# Gotcha: GRUB hangs without ACPI/APIC features in the libvirt domain

## Symptom
Rocky 9 VM boots to GRUB, shows:
```
Welcome to GRUB!
Probing EDD (edd=off to disable)... ok
_
```
Then hangs indefinitely. Kernel never loads. No network traffic, no
DHCP lease. VGA screenshot confirms the same screen after 5+ minutes.

## Root cause
The `dmacvicar/libvirt` OpenTofu provider v0.9.7 does NOT set ACPI or
APIC features by default. The `virt-install --os-variant rocky9` command
DOES set them (via the osinfo database). Without ACPI:

- GRUB can initialize basic hardware (EDD probe passes)
- But the kernel loader path relies on ACPI tables for device
  enumeration, power management, and interrupt routing
- The kernel either can't load or loads into a broken state

Interestingly, the VM consumes 100% CPU while hung — GRUB is
apparently spinning on something that requires ACPI but doesn't
time out.

## Fix

In the OpenTofu `libvirt_domain` resource:
```hcl
features = {
  acpi = true
  apic = {}    # apic is an object, not a bool — empty {} enables it
}

cpu = {
  mode = "host-passthrough"
}
```

## Debugging approach that found this

1. Took VGA screenshots (`virsh screenshot`) — confirmed genuine hang
2. Tested the RAW image with `virt-install --os-variant rocky9` → booted fine
3. Compared `virsh dumpxml` of the working domain vs the broken one
4. The diff was: features (acpi, apic) and CPU mode
5. Added features to the ToFu config → VM booted

## How to prevent
Any OpenTofu config using `dmacvicar/libvirt` v0.9.x for modern Linux
guests MUST include `features { acpi = true; apic = {} }`. This is
the provider's most critical gotcha because the default (no features)
produces a domain that looks correct but silently fails to boot.

## Environment
- dmacvicar/libvirt provider v0.9.7
- OpenTofu v1.11.6
- Rocky Linux 9.7 GenericCloud image
- QEMU 8.2.2 on Ubuntu 24.04
