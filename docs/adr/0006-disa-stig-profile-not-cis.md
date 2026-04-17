# ADR-0006: DISA STIG profile (not CIS) for the first remediation skill

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** Ken Rollins

## Context

The first gemma-forge skill is autonomous compliance remediation on a
Rocky Linux 9 target. Two well-known security baselines apply to RHEL 9
(and to RHEL-compatible distributions like Rocky 9):

1. **DISA STIG** (Defense Information Systems Agency Security Technical
   Implementation Guide) — the U.S. Department of Defense's hardening
   baseline. Authoritative for DoD, intelligence community, and many
   civilian Federal agencies that follow DoD guidance.
2. **CIS Benchmark** (Center for Internet Security) — a widely adopted
   industry baseline used in commercial and some Federal environments.

The OpenSCAP `scap-security-guide` package ships *both* profiles as
part of the same `ssg-rhel9-ds.xml` data stream, so technically the
demo could pick either by changing the profile ID at scan time.

gemma-forge is a Federal-leaning reference build, and the audience for
the demo is Federal agencies — primarily DoD-adjacent — evaluating
sovereign-edge AI for use on hosts they will eventually have to STIG.
The choice of baseline is therefore not a technical preference, it's
an audience-fit decision.

## Decision

The first skill targets the **DISA STIG profile** explicitly, not CIS.
The OpenSCAP scan invocation pins the profile ID:

```
xccdf_org.ssgproject.content_profile_stig
```

Future skills may add CIS as a separately-selectable profile (one of
the explicit reasons the skills system in ADR-0011 is folder-per-skill
manifest-driven), but the headline demo runs against the DISA STIG.

## Alternatives considered

- **CIS Level 2** — Strong commercial baseline, broadly adopted in
  industry. Less specific to the Federal audience we're addressing
  with this build. Would be the right primary choice if gemma-forge
  were positioned as a commercial-edge AI demo. We will likely add
  it as a second STIG-style skill (`skills/cis-rhel9/`) once the
  skills system is extracted in Phase 4 — that addition is a
  manifest + prompt-edit exercise, not a code change.

- **PCI-DSS / HIPAA / FedRAMP profile** — Each is a credible compliance
  target with its own audience, but each is also higher-effort to
  represent faithfully (PCI involves cardholder-data flow, HIPAA
  involves PHI scoping). DISA STIG is the right starting point because
  it's host-scoped, well-bounded, and has authoritative SCAP content
  shipped with the OS.

- **A custom hardening profile invented for this demo** — Rejected
  outright. Demonstrating remediation against an authoritative,
  publicly-defined baseline is the entire credibility argument. A
  custom profile would let the demo "succeed" against criteria
  nobody recognizes, which is the opposite of what we want.

- **Generic OpenSCAP scan with no profile pinned** — Would default to
  whatever the SSG ships as the default and produce ambiguous demo
  output. We pin the profile explicitly so the audit trail and the
  demo runbook always reference the same baseline.

## Consequences

### Positive

- Lines up directly with the DoD/intel-community Federal audience the
  XR7620 is sold into.
- The profile ID is **explicit and grep-able** in code and in trace
  output, so the audit trail can always answer "what baseline did
  this run remediate against?" with a single, authoritative string.
- Sets up the skills system to add CIS, FIPS-mode-verification, and
  other profiles as additional skills under
  `skills/<profile>-rhel9/` without code changes.
- The DISA STIG content lives in the standard `scap-security-guide`
  package, which is freely redistributable and air-gap-mirrorable.

### Negative / accepted trade-offs

- DISA STIG has more failing rules out of the box than CIS Level 1 on
  a fresh Rocky 9 install. The demo run will need to either remediate
  the full set (long, but impressive) or scope to a subset for the
  live performance (short, but requires explicit scoping in the demo
  runbook). We will pick a representative subset for the live demo
  and run the full set in pre-recorded form for the README.
- Some commercial-only Federal customers will ask about CIS first.
  We pre-empt this in the README and offer the path forward: "CIS is
  a sibling skill — same harness, different profile ID."

## References

- [DISA STIG library — RHEL 9](https://public.cyber.mil/stigs/downloads/)
- [scap-security-guide on GitHub](https://github.com/ComplianceAsCode/content)
- [OpenSCAP](https://www.open-scap.org/)
- ADR-0005: Rocky Linux 9 as the demo target OS
- ADR-0011 (planned): Skills folder-manifest with optional plugin escape hatch
