"""Render deplowly RBAC manifests for arbitrary target namespaces.

deplowly always runs under a single ServiceAccount (created once in its own
namespace). The deployments it watches, however, often live in *other*
namespaces, and each of those needs a `Role` + `RoleBinding` granting the SA
the `deployments`/`secrets` permissions for that namespace.

This module turns a list of target namespaces into ready-to-`apply` YAML, so an
operator does not have to hand-write one RBAC file per namespace.
"""

from __future__ import annotations

import collections
import sys
from collections.abc import Iterable

import yaml

from .models import Config

# deplowly only ever needs to read deployments (image + imagePullSecrets) and
# patch them (rollout restart), plus read the secrets those deployments
# reference. These are the same rules as deploy/rbac.yaml.
_ROLE_RULES: list[dict] = [
    {
        "apiGroups": [""],
        "resources": ["deployments"],
        "verbs": ["get", "list", "patch"],
    },
    {
        "apiGroups": [""],
        "resources": ["secrets"],
        "verbs": ["get", "list"],
    },
]


def build_role(namespace: str, name: str = "deplowly") -> dict:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": name, "namespace": namespace},
        "rules": _ROLE_RULES,
    }


def build_role_binding(
    namespace: str,
    role_name: str = "deplowly",
    binding_name: str = "deplowly",
    sa_name: str = "deplowly",
    sa_namespace: str = "deplowly",
) -> dict:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": binding_name, "namespace": namespace},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": role_name,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": sa_name,
                "namespace": sa_namespace,
            }
        ],
    }


def target_namespaces(config: Config, sa_namespace: str) -> list[str]:
    """Distinct target namespaces from a config, excluding the SA's own ns.

    The SA's own namespace already has its RBAC in deploy/rbac.yaml, so we do
    not regenerate it here.
    """

    seen: collections.OrderedDict[str, None] = collections.OrderedDict()
    for target in config.targets:
        if target.namespace != sa_namespace:
            seen.setdefault(target.namespace, None)
    return list(seen)


def render_manifests(
    namespaces: Iterable[str],
    sa_name: str = "deplowly",
    sa_namespace: str = "deplowly",
    name: str = "deplowly",
) -> str:
    """Render a multi-document YAML with one Role + RoleBinding per namespace."""

    namespaces = sorted(set(namespaces))
    docs: list[dict] = []
    for ns in namespaces:
        docs.append(build_role(ns, name=name))
        docs.append(
            build_role_binding(
                ns,
                role_name=name,
                binding_name=name,
                sa_name=sa_name,
                sa_namespace=sa_namespace,
            )
        )
    return yaml.safe_dump_all(docs, sort_keys=False, explicit_start=True)


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="deplowly rbac",
        description=("生成供 kubectl apply 的 RBAC 清单（每个目标 namespace 一份 Role + RoleBinding）"),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--from-config",
        metavar="PATH",
        help="从 deplowly 配置文件读取所有要监控的 namespace（排除 SA 所在 namespace）",
    )
    src.add_argument(
        "--namespace",
        action="append",
        metavar="NS",
        dest="namespaces",
        help="直接指定目标 namespace（可重复，例如 --namespace prod --namespace staging）",
    )
    parser.add_argument("--sa-name", default="deplowly", help="被绑定的 ServiceAccount 名（默认 deplowly）")
    parser.add_argument(
        "--sa-namespace",
        default="deplowly",
        help="ServiceAccount 所在 namespace（默认 deplowly）",
    )
    parser.add_argument(
        "--name",
        default="deplowly",
        help="生成的 Role / RoleBinding 的 name（默认 deplowly）",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="输出文件路径，'-' 表示打印到 stdout（默认 -）",
    )
    args = parser.parse_args(argv)

    if args.from_config:
        from .config import ConfigError, load_config

        try:
            config = load_config(args.from_config)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 1
        namespaces = target_namespaces(config, args.sa_namespace)
        if not namespaces:
            print(
                f"config 中没有除 {args.sa_namespace} 之外的目标 namespace，无需生成 RBAC。",
                file=sys.stderr,
            )
            return 0
    else:
        namespaces = args.namespaces or []

    manifest = render_manifests(
        namespaces,
        sa_name=args.sa_name,
        sa_namespace=args.sa_namespace,
        name=args.name,
    )

    if args.output == "-":
        sys.stdout.write(manifest)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(manifest)
        print(f"已写入 {args.output}（{len(namespaces)} 个 namespace）", file=sys.stderr)
    return 0
