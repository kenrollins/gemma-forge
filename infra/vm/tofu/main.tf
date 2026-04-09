terraform {
  required_version = ">= 1.6"

  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.9"
    }
  }

  # Local state only — air-gap-clean, no remote backend.
  # State lives at /data/vm/gemma-forge/state/ per ADR-0012.
}

provider "libvirt" {
  uri = "qemu:///system"
}

# ---------------------------------------------------------------------------
# Base image volume — the Rocky 9 GenericCloud image, stored once in the
# gemma-forge libvirt pool and used as a backing store for VM disks.
# ---------------------------------------------------------------------------

resource "libvirt_volume" "rocky9_base" {
  name = "rocky9-base.qcow2"
  pool = var.libvirt_pool

  target = {
    format = {
      type = "qcow2"
    }
  }

  create = {
    content = {
      url = var.rocky9_image_path
    }
  }
}
