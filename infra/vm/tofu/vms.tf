# ---------------------------------------------------------------------------
# Mission App VM — Rocky 9 target for STIG remediation
# ---------------------------------------------------------------------------

# Resized disk backed by the Rocky 9 base image
resource "libvirt_volume" "mission_app_disk" {
  name     = "mission-app.qcow2"
  pool     = var.libvirt_pool
  capacity = var.mission_app_disk_gb * 1024 * 1024 * 1024

  target = {
    format = {
      type = "qcow2"
    }
  }

  backing_store = {
    path = libvirt_volume.rocky9_base.path
    format = {
      type = "qcow2"
    }
  }
}

# Cloud-init disk (NoCloud datasource)
resource "libvirt_cloudinit_disk" "mission_app_init" {
  name = "mission-app-cloudinit"

  meta_data = <<-EOF
    instance-id: mission-app-001
    local-hostname: mission-app
  EOF

  user_data = templatefile("${path.module}/cloud-init/user-data.yaml", {
    ssh_pubkey = trimspace(file(var.ssh_pubkey_path))
  })
}

# Upload the cloud-init ISO into a libvirt volume (v0.9.x requirement)
resource "libvirt_volume" "mission_app_cloudinit" {
  name = "mission-app-cloudinit.iso"
  pool = var.libvirt_pool

  create = {
    content = {
      url = libvirt_cloudinit_disk.mission_app_init.path
    }
  }
}

# The VM
resource "libvirt_domain" "mission_app" {
  name        = "gemma-forge-mission-app"
  type        = "kvm"
  memory      = var.mission_app_memory_mb
  memory_unit = "MiB"
  vcpu        = var.mission_app_vcpus
  running     = true

  # OS boot configuration — matches virt-install --os-variant rocky9
  os = {
    type         = "hvm"
    type_arch    = "x86_64"
    type_machine = "q35"
    boot_devices = [{ dev = "hd" }]
  }

  # ACPI + APIC are REQUIRED for modern Linux guests. Without these,
  # GRUB hangs after "Probing EDD... ok" and never loads the kernel.
  # Learned the hard way during Phase 2 debugging.
  features = {
    acpi = true
    apic = {}
  }

  # Pass through the host CPU model for best performance.
  cpu = {
    mode = "host-passthrough"
  }

  devices = {
    disks = [
      {
        source = {
          volume = {
            pool   = libvirt_volume.mission_app_disk.pool
            volume = libvirt_volume.mission_app_disk.name
          }
        }
        target = {
          dev = "vda"
          bus = "virtio"
        }
        driver = {
          type = "qcow2"
        }
      },
      {
        device = "cdrom"
        source = {
          volume = {
            pool   = libvirt_volume.mission_app_cloudinit.pool
            volume = libvirt_volume.mission_app_cloudinit.name
          }
        }
        target = {
          dev = "sda"
          bus = "sata"
        }
      },
    ]

    interfaces = [
      {
        type = "network"
        model = {
          type = "virtio"
        }
        source = {
          network = {
            network = "default"
          }
        }
      },
    ]

    serials = [
      {
        target = {
          type = "isa-serial"
        }
      },
    ]

    consoles = [
      {
        target = {
          type = "serial"
        }
      },
    ]

    graphics = [
      {
        vnc = {
          auto_port = true
          listen    = "127.0.0.1"
        }
      },
    ]
  }
}
