# VM IP is retrieved after apply via `virsh domifaddr` or the
# libvirt_domain_interface_addresses data source. For simplicity in
# the wrapper scripts, we output the VM name and let vm-up.sh
# poll `virsh domifaddr` until a lease appears.

output "vm_name" {
  description = "The libvirt domain name of the mission-app VM"
  value       = libvirt_domain.mission_app.name
}

output "ssh_key_path" {
  description = "Path to the SSH private key for adm-forge"
  value       = replace(var.ssh_pubkey_path, ".pub", "")
}
