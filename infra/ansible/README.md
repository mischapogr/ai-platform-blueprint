# Ansible deployment modules

[local-lab](local-lab/playbook.yaml) configures only the disposable guest: Docker,
private registry trust, verified signed images, additive SQL migrations, TLS,
authentication and monitoring. See the [local lab guide](../../docs/LOCAL-LAB.md).
Inventory and generated secrets are private under `.local` and `.secrets`.
This playbook is not a production/cloud host hardening profile.
