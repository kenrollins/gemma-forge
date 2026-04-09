variable "vm_root" {
  description = "Host path for GemmaForge VM state (pool, seeds, snapshots, keys)"
  type        = string
  default     = "/data/vm/gemma-forge"
}

variable "libvirt_pool" {
  description = "Name of the libvirt storage pool for VM disks"
  type        = string
  default     = "gemma-forge"
}

variable "rocky9_image_path" {
  description = "Path to the Rocky 9 GenericCloud qcow2 image on the host"
  type        = string
  default     = "/data/vm/gemma-forge/pool/Rocky-9-GenericCloud.latest.x86_64.qcow2"
}

variable "ssh_pubkey_path" {
  description = "Path to the ed25519 public key for the adm-forge user"
  type        = string
  default     = "/data/vm/gemma-forge/keys/adm-forge.pub"
}

# ---------------------------------------------------------------------------
# Mission App VM tunables
# ---------------------------------------------------------------------------

variable "mission_app_vcpus" {
  description = "Number of vCPUs for the mission-app VM"
  type        = number
  default     = 4
}

variable "mission_app_memory_mb" {
  description = "RAM in MB for the mission-app VM"
  type        = number
  default     = 4096
}

variable "mission_app_disk_gb" {
  description = "Disk size in GB for the mission-app VM"
  type        = number
  default     = 20
}
