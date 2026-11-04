#!/usr/bin/env python3
"""Operate only the project-owned local registry, VM and recovery lab."""

import argparse

from local_lab.bootstrap import init

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "init",
            "vm-create",
            "vm-destroy",
            "publish",
            "registry",
            "verify",
            "provision",
            "deploy",
            "check",
            "recover",
            "vm-apply",
            "vm-remove",
            "stop",
            "backup",
            "diagnose",
            "monitoring-check",
            "monitoring-outage",
            "runtime-audit",
            "database-migrate",
            "models-enable",
            "models-check",
            "models-evaluate",
            "publish-qualified",
            "workflow-run",
            "backup-run",
            "restore-run",
            "schedule-install",
            "improvements-check",
        ],
    )
    parser.add_argument("--name", default="primary")
    args = parser.parse_args()
    if args.action == "init":
        init()

    elif args.action == "vm-create":
        from local_lab.vm import create

        create(args.name)
    elif args.action == "vm-destroy":
        from local_lab.vm import destroy

        destroy(args.name)

    elif args.action == "publish":
        from local_lab.registry import publish

        publish()

    elif args.action == "registry":
        from local_lab.fallback import init as registry_init

        registry_init()
    elif args.action == "verify":
        from local_lab.registry import verify

        verify()
        print("Local release signature verified.")

    elif args.action == "provision":
        from local_lab.deploy import ansible

        ansible(args.name, "provision")
    elif args.action == "deploy":
        from local_lab.deploy import apply

        apply(args.name)

    elif args.action == "check":
        from local_lab.acceptance import check

        check(args.name)

    elif args.action == "recover":
        from local_lab.recovery import drill

        drill()

    elif args.action in {"vm-apply", "vm-remove"}:
        from local_lab.recovery import terraform

        terraform(args.name, "apply" if args.action == "vm-apply" else "destroy")
    elif args.action == "stop":
        from local_lab.common import compose

        compose("stop")

    elif args.action == "backup":
        from local_lab.recovery import backup

        backup(args.name)

    elif args.action == "diagnose":
        from local_lab.diagnostics import diagnose

        diagnose(args.name)

    elif args.action == "monitoring-check":
        from local_lab.monitoring_check import check

        check(args.name)

    elif args.action == "monitoring-outage":
        from local_lab.monitoring_fault import check

        check(args.name)

    elif args.action == "runtime-audit":
        from local_lab.runtime_security import audit

        raise SystemExit(0 if audit() else 1)

    elif args.action in {"workflow-run", "backup-run", "restore-run"}:
        from local_lab.automation import execute

        execute(args.action.removesuffix("-run"), args.name)
    elif args.action == "schedule-install":
        from local_lab.automation import install

        install(args.name)
    elif args.action == "improvements-check":
        from local_lab.improvements_check import check

        check(args.name)

    elif args.action == "publish-qualified":
        from local_lab.registry import publish

        publish(qualified=True)

    elif args.action == "database-migrate":
        from local_lab.database_migration import apply

        apply(args.name)
    elif args.action == "models-enable":
        from local_lab.model_profile import enable

        enable(args.name)
    elif args.action == "models-check":
        from local_lab.model_check import check

        check(args.name)

    elif args.action == "models-evaluate":
        from local_lab.model_profile import evaluate

        evaluate(args.name)
