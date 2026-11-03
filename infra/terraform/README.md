# Terraform deployment modules

[local-lab](local-lab/main.tf) manages real disposable QEMU guests with the built-in
`terraform_data` provider and a private local backend. See the
[local lab guide](../../docs/LOCAL-LAB.md) for exact commands, scope and cleanup.
No cloud provider resources or remote state backend are implemented. VM disks,
state, credentials and real host configuration remain outside Git.
