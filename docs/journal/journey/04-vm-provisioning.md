---
id: journey-04-vm-provisioning
type: journey
title: "Journey: VM Provisioning — OpenTofu, libvirt, and the GRUB Hang"
date: 2026-04-09
tags: [L1-data-infrastructure, decision]
related:
  - gotchas/libvirt-provider-v09-migration
  - gotchas/grub-acpi-apic
  - gotchas/apparmor-libvirt
one_line: "I chose OpenTofu for Federal-credible IaC, hit a breaking API change in the libvirt provider v0.9.7, researched the correct API instead of falling back to shell scripts, then spent an hour debugging a GRUB hang and AppArmor denials."
---

# Journey: VM Provisioning — OpenTofu, libvirt, and the GRUB Hang

## The story in one sentence
I chose OpenTofu for Federal-credible IaC, hit a breaking API change
in the libvirt provider v0.9.7, researched the correct API rather than
falling back to shell scripts, then spent an hour debugging a GRUB hang
caused by missing ACPI/APIC features and AppArmor denials.

## What I planned (ADR-0004)

OpenTofu with the `dmacvicar/libvirt` provider for VM provisioning.
Chosen over Terraform (HashiCorp BSL license concern), Vagrant (same
BSL issue, reads as a dev tool), and virt-install (no declarative IaC
story for anyone reviewing the architecture).

Target: Rocky Linux 9 GenericCloud image with cloud-init, running
Nginx + Postgres as a "mission app" the Ralph loop must keep alive.

## API migration: libvirt provider v0.7/0.8 → v0.9.7

The OpenTofu config was initially written against the OLD provider API
(v0.7-era). When `tofu apply` ran, every resource failed:
- `source` → use `create.content.url`
- `format` → use `target.format.type`
- `base_volume_id` → use `backing_store.path` + `backing_store.format.type`
- `cloudinit` attribute on domains → gone; must upload ISO as a volume
  and attach as cdrom disk
- `disk`, `network_interface`, `console`, `graphics` blocks → moved
  inside `devices` as `disks`, `interfaces`, `consoles`, `graphics` lists
- `libvirt_cloudinit_disk` now requires `meta_data` (was optional)
- `libvirt_domain` now requires `type = "kvm"` (was inferred)
- `memory` defaults to KiB; use `memory_unit = "MiB"` to keep sane values

The "fall back to virt-install" shortcut was explicitly off the table.
The governing directive: do it right, not quick. If the API and
documentation need to be updated, update them for real. This was saved
as a core memory directive.

I researched the correct v0.9.7 API by:
1. Running `tofu providers schema -json` to dump the full schema
2. Extracting nested type definitions for complex attributes
3. Searching the provider's GitHub repo for v0.9.x examples
4. Iteratively fixing each resource until `tofu validate` passed

## The GRUB hang

After fixing the ToFu configs, the VM created successfully but hung
at GRUB: "Probing EDD (edd=off to disable)... ok" → cursor → nothing.
No kernel load, no network traffic, no DHCP lease.

### Debugging sequence

1. **Took VGA screenshots via `virsh screenshot`** — confirmed the VM
   was genuinely stuck at GRUB, not just routing output to serial
2. **Checked AppArmor** — found `virt-aa-helper` was DENIED
   `sys_admin` and `sys_resource` capabilities, preventing it from
   generating proper per-VM AppArmor profiles
3. **Fixed AppArmor** — added capabilities to
   `/etc/apparmor.d/local/usr.lib.libvirt.virt-aa-helper`, added pool
   path to `/etc/apparmor.d/local/abstractions/libvirt-qemu`
4. **Fixed file ownership** — `mission-app.qcow2` was owned by
   `root:root` instead of `libvirt-qemu:kvm`
5. **Still hung at GRUB** after AppArmor and ownership fixes
6. **Isolated the problem** — booted the RAW Rocky 9 image with
   `virt-install --os-variant rocky9` → worked immediately, got DHCP
7. **Compared XML** — the working virt-install XML had:
   - `<features><acpi/><apic/></features>`
   - `<cpu mode='host-passthrough'/>`
   - The OpenTofu config had NEITHER
8. **Root cause: missing ACPI/APIC.** Without ACPI, GRUB can
   initialize the hardware probe (EDD) but the kernel loader path
   relies on ACPI tables that don't exist. The kernel never loads.
   The libvirt provider v0.9.7 does NOT set ACPI/APIC by default
   (virt-install with `--os-variant` does).

### The fix

```hcl
features = {
  acpi = true
  apic = {}
}

cpu = {
  mode = "host-passthrough"
}
```

After this fix, the VM booted, got DHCP, cloud-init ran, and the
mission app came up healthy.

## Cloud-init template escaping

The cloud-init user-data file is processed by OpenTofu's `templatefile()`
function. Bash `${variable}` syntax conflicts with HCL's `${}`
interpolation. Fix: escape bash variables as `$${variable}` in the
template. The `${ssh_pubkey}` variable (from ToFu) is NOT escaped
because it IS an HCL interpolation.

## The libvirt group issue

After `usermod -aG libvirt rollik`, the group membership didn't take
effect in the current shell session. OpenTofu couldn't connect to
the libvirt socket (`permission denied`). Fix: wrap tofu commands
in a `run_tofu()` function that uses `sg libvirt -c "tofu ..."` if
the group isn't in the current session.

## Key artifacts

- ADR-0004 — OpenTofu (not Terraform) decision
- ADR-0005 — Rocky Linux 9 as RHEL stand-in
- `infra/vm/tofu/*.tf` — the corrected v0.9.7 configs
- `infra/vm/tofu/cloud-init/user-data.yaml` — cloud-init with HCL escaping
- `infra/vm/scripts/vm-up.sh` — handles sg libvirt, tofu apply,
  SSH wait, cloud-init wait, healthcheck, baseline snapshot
- Memory: `feedback_no_shortcuts.md` — "do it right, not quick"
