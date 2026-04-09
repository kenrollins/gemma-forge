# ADR-0005: Rocky Linux 9 as the demo target OS (stand-in for RHEL 9)

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** Ken Rollins

## Context

The first GemmaForge skill is autonomous DISA STIG remediation against
a representative Federal target host. Federal production is
overwhelmingly **Red Hat Enterprise Linux 9** in the unclassified-and-up
tiers we're targeting. The honest, customer-credible choice for the
target OS would therefore be RHEL 9.

However, the reference XR7620 build does not have a Red Hat
subscription, and we explicitly do not want this open-source reference
build to require one — Federal customers cloning the repo to evaluate
it should not have to negotiate licensing before they can run
`vm-up.sh`.

We need a target OS that:

1. Is **binary-compatible with RHEL 9** so the demo's STIG content,
   tooling, and remediations transfer to a customer's RHEL fleet
   on day one with no changes.
2. Has **legitimate Federal precedent** — we don't want to invent a
   Frankenstein "kinda like RHEL" stand-in.
3. Is **freely redistributable** with no entitlement gating.
4. Has a **maintained cloud image** (qcow2) suitable for libvirt +
   cloud-init provisioning.
5. Supports the **DISA RHEL 9 STIG** content unmodified
   (see ADR-0006).

## Decision

We use **Rocky Linux 9** as the demo target OS, provisioned from the
official Rocky 9 GenericCloud qcow2 image. The DISA RHEL 9 STIG content
is applied to it directly via OpenSCAP without modification.

The README and the demo runbook are explicit and honest about this:
*"Your production fleet is RHEL. We built this reference against the
official DISA RHEL 9 STIG on a binary-compatible Rocky 9 host so the
playbook drops into your RHEL fleet on day one with zero changes."*

## Alternatives considered

- **RHEL 9 directly** — The maximally aligned choice. Rejected because
  the reference build host has no Red Hat subscription, and requiring
  one would be a significant friction point for Federal customers
  trying to evaluate the repo before procurement.

- **AlmaLinux 9** — Equally valid binary-compatible RHEL clone, very
  similar Federal precedent, also has a maintained cloud image. The
  choice between Rocky and Alma is largely cosmetic. We picked Rocky
  because (a) it has slightly broader Federal/lab adoption in our
  experience, (b) the Rocky cloud-image release cadence is reliable,
  and (c) the official mirror layout is straightforward for air-gapped
  customers to mirror internally. AlmaLinux remains a fully supported
  drop-in alternative — anyone wanting to swap distros need only point
  the OpenTofu config at the AlmaLinux 9 GenericCloud image.

- **Oracle Linux 9** — Also RHEL-compatible, also has free entitlements,
  but pulls in Oracle as a vendor relationship and brand association
  that some Federal customers actively avoid.

- **CentOS Stream 9** — Upstream of RHEL, not downstream. Less common
  in Federal production environments, and the "rolling toward the
  next RHEL minor" model is the wrong shape for a STIG-baseline demo
  where reproducibility matters.

- **Ubuntu 22.04 / 24.04 with the corresponding DISA STIG** — Ubuntu
  has its own DISA STIG, but the demo's first skill targets RHEL 9
  STIG content specifically and Federal customers asking us about
  edge AI on the XR7620 are predominantly RHEL shops. Ubuntu Pro on
  Ubuntu hosts is a credible future skill, but is not the first one.

## Consequences

### Positive

- No Red Hat subscription required for the reference build or for
  customer evaluation.
- DISA RHEL 9 STIG content applies bit-for-bit; the demo story is
  "we're remediating against the official DISA RHEL 9 STIG, on a
  binary-compatible host" — defensible and honest.
- Free, mirrorable cloud image friendly to air-gapped lab setups.
- Customers can swap to RHEL 9 in their own environment by changing
  one image URL in `infra/vm/tofu/variables.tf` — every other artifact
  in the repo applies unchanged.

### Negative / accepted trade-offs

- The on-screen `cat /etc/os-release` during the live demo will show
  "Rocky Linux" rather than "Red Hat Enterprise Linux." We address
  this head-on in the demo runbook script: explain the binary
  compatibility upfront, before the audience notices and wonders.
- Rocky Linux's release cadence and bug-fix latency are slightly
  behind RHEL's because Rocky tracks RHEL downstream. This is not
  a concern for a demo, and customers running the same content on
  RHEL get the upstream timing.
- Some Federal customers will still ask "but does it work on RHEL?"
  We pre-empt the question in the README's Key Architectural
  Decisions section.

## References

- [Rocky Linux project](https://rockylinux.org/)
- [Rocky Linux cloud images](https://download.rockylinux.org/pub/rocky/9/images/x86_64/)
- ADR-0006: DISA STIG profile (not CIS)
- ADR-0004: OpenTofu (not Terraform) for libvirt VM provisioning
