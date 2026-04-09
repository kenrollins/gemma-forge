# ADR-0004: OpenTofu (not Terraform) for libvirt VM provisioning

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** Ken Rollins

## Context

GemmaForge needs to provision one or more KVM/libvirt VMs on the
XR7620 host as targets for the Ralph-loop demos. The first such VM is
a Rocky Linux 9 host running an Nginx + Postgres "mission app"
(see ADR-0005). Future skills may add additional VMs (e.g., an attacker
host, a secondary service host).

The provisioning layer must be:

1. **Declarative and version-controllable** — IaC is the dominant
   Federal pattern. Imperative shell scripts are a hard sell to a
   Federal evaluator skimming the repo.
2. **Multi-VM friendly** — adding a second or third VM should be a
   small HCL diff, not a structural rewrite.
3. **Air-gappable** — the entire `apply` cycle must work with zero
   external network access. No SaaS backend, no Terraform Cloud, no
   vendor registry call at runtime.
4. **License-clean for Federal redistribution** — Federal legal teams
   are increasingly cautious about HashiCorp's Business Source License
   (BSL), adopted in August 2023, and prefer Apache-2-licensed
   alternatives where they exist.
5. **Backed by an actively maintained libvirt provider** so we are not
   tied to a stale plugin.

## Decision

We use **[OpenTofu](https://opentofu.org)**, the Linux Foundation fork
of Terraform, with the
[`dmacvicar/libvirt`](https://registry.terraform.io/providers/dmacvicar/libvirt/latest/docs)
provider.

State is stored in a **local backend** under
`/data/vm/gemma-forge/state/` (outside the repo, per the host-layout
convention in ADR-0012). No remote backend, no SaaS coupling.

Customers cloning the repo run `tofu init && tofu apply` from
`infra/vm/tofu/`, with `infra/vm/config.env` providing host-specific
overrides such as `VM_ROOT`.

## Alternatives considered

- **HashiCorp Terraform** — Functionally equivalent for our needs and
  the more familiar brand. Rejected because the Business Source License
  (BSL) it adopted in August 2023 is now a documented friction point
  for several Federal legal teams when redistributing reference
  architectures. OpenTofu is API-compatible (the same `.tf` files
  apply unchanged in either tool), Apache-2.0 licensed, and governed by
  the Linux Foundation — strictly the more Federal-friendly choice
  with zero functional downside for our use case.

- **Vagrant + libvirt provider** — Mature, multi-VM friendly, well
  known to dev teams. Less "Federal reference architecture" feeling
  than IaC; reads as a developer tool rather than an
  infrastructure-management tool. Also subject to the same HashiCorp
  BSL concern as Terraform.

- **`virt-install` + bash scripts** — Zero extra dependencies, dead
  simple, totally air-gap-clean. But there's no declarative state,
  no diff/plan, and adding a second VM is "write more bash" rather
  than "add a 10-line resource block." We will ship a
  `vm-quickstart.sh` shell-script alternative as a documented
  fallback for customers who don't want to install OpenTofu, but
  the primary path is OpenTofu.

- **Ansible with the `community.libvirt` collection** — Procedural
  rather than declarative; no plan/diff equivalent to `tofu plan`.
  Strong fit if Ansible is already in a customer's stack, but
  introducing it just for VM provisioning would expand the
  dependency surface significantly. We may add Ansible playbooks
  *inside* the target VM later for guest-side configuration, which
  is a different concern.

- **Pulumi (Python)** — Programmatic IaC in Python. Attractive
  because the rest of the harness is Python, but pulls in a heavier
  runtime and a less Federal-recognized brand. Not worth the deviation
  from the dominant Fed IaC idiom.

## Consequences

### Positive

- Declarative, plan/apply-style IaC tells the right story to Federal
  evaluators looking for a reference architecture.
- License-clean: Apache-2.0, Linux Foundation governance, no BSL
  redistribution concerns.
- Multi-VM scaling is trivial — adding a VM is one resource block in
  `infra/vm/tofu/vms.tf`.
- Local state backend keeps the entire workflow air-gap-clean.
- The `dmacvicar/libvirt` provider is actively maintained and is the
  de facto choice for libvirt-targeting IaC.
- Fully reversible: any future move to plain Terraform requires
  literally zero file changes — `terraform init && terraform apply`
  works against the same `.tf` files.

### Negative / accepted trade-offs

- One additional binary dependency (`tofu`) on the host beyond the base
  libvirt toolchain. We mitigate by also shipping `vm-quickstart.sh`,
  a `virt-install` wrapper, so customers who want zero extra
  dependencies have an escape hatch.
- The `dmacvicar/libvirt` provider is community-maintained, not
  vendor-supported. We accept this; the provider has a long track
  record and the underlying libvirt API is stable.
- OpenTofu's brand recognition inside Federal procurement is still
  catching up to Terraform's. We address this in the README by
  explicitly noting the Linux Foundation governance and the
  drop-in compatibility.

## References

- [OpenTofu](https://opentofu.org)
- [dmacvicar/libvirt provider](https://registry.terraform.io/providers/dmacvicar/libvirt/latest/docs)
- [Linux Foundation OpenTofu announcement](https://www.linuxfoundation.org/press/announcing-opentofu)
- ADR-0005: Rocky Linux 9 as the demo target OS
- ADR-0012: `/data/vm/gemma-forge/` host layout convention
