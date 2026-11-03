terraform {
  backend "local" {}
  required_version = ">= 1.4, < 2.0"
}
variable "project_root" { type = string }
variable "vm_name" {
  type    = string
  default = "primary"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}$", var.vm_name))
    error_message = "Use a bounded local VM name."
  }
}
resource "terraform_data" "guest" {
  input = {
    python = "${var.project_root}/.venv/bin/python"
    script = "${var.project_root}/scripts/local-lab.py"
    name   = var.vm_name
  }
  provisioner "local-exec" {
    command = "\"$LAB_PYTHON\" \"$LAB_SCRIPT\" vm-create --name \"$LAB_VM_NAME\""
    environment = {
      LAB_PYTHON  = self.input.python
      LAB_SCRIPT  = self.input.script
      LAB_VM_NAME = self.input.name
    }
  }
  provisioner "local-exec" {
    when    = destroy
    command = "\"$LAB_PYTHON\" \"$LAB_SCRIPT\" vm-destroy --name \"$LAB_VM_NAME\""
    environment = {
      LAB_PYTHON  = self.input.python
      LAB_SCRIPT  = self.input.script
      LAB_VM_NAME = self.input.name
    }
  }
}
output "guest_name" { value = terraform_data.guest.input.name }
