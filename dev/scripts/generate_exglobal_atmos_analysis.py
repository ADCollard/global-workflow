#!/usr/bin/env python3
"""
Generate a JEDI-aware exglobal_atmos_analysis.sh from:
  - top-level YAML (with optional !INC includes)
  - Jinja2 shell template

Usage:
  python dev/scripts/generate_exglobal_atmos_analysis.py \
    --config dev/parm/config/gfs/yaml/my_top.yaml \
    --template dev/scripts/templates/exglobal_atmos_analysis.sh.j2 \
    --output dev/scripts/exglobal_atmos_analysis.generated.sh \
    --check-paths
"""

from __future__ import annotations
import argparse
import copy
import pathlib
import re
import sys
from typing import Any, Dict

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

INC_TAG = "!INC"


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def expand_vars(s: str, context: Dict[str, Any]) -> str:
    # Expand ${VAR} using context keys only (safe, deterministic)
    def repl(m):
        key = m.group(1)
        return str(context.get(key, m.group(0)))
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, s)


class LoaderWithInclude(yaml.SafeLoader):
    pass


def _construct_inc(loader: LoaderWithInclude, node):
    return {INC_TAG: loader.construct_scalar(node)}


LoaderWithInclude.add_constructor(INC_TAG, _construct_inc)


def load_yaml_with_inc(path: pathlib.Path, root_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    root_context = root_context or {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f, Loader=LoaderWithInclude) or {}

    # Resolve include if present under "defaults"
    defaults = data.get("defaults")
    if isinstance(defaults, dict) and INC_TAG in defaults:
        inc_raw = defaults[INC_TAG]
        inc_expanded = expand_vars(inc_raw, root_context)
        inc_path = pathlib.Path(inc_expanded)
        if not inc_path.is_absolute():
            inc_path = (path.parent / inc_path).resolve()
        if not inc_path.exists():
            raise FileNotFoundError(f"Included defaults file not found: {inc_path}")
        with inc_path.open("r", encoding="utf-8") as f:
            inc_data = yaml.safe_load(f) or {}
        data = deep_merge(inc_data, data)

    return data


def flatten_to_env(cfg: Dict[str, Any]) -> Dict[str, Any]:
    env = {}

    # top-level groups used by your example
    for grp in ("base", "atmanl", "atmensanl", "esfc", "nsst", "prepatmiodaobs", "sfcanl"):
        section = cfg.get(grp, {})
        if isinstance(section, dict):
            env.update(section)

    # sensible defaults
    env.setdefault("DO_JEDIATMVAR", "NO")
    env.setdefault("DO_JEDIATMENS", "NO")
    env.setdefault("DO_TEST_MODE", "NO")
    env.setdefault("DO_CONVERT_IODA", "NO")
    env.setdefault("USE_IODADIR", "NO")
    env.setdefault("DONST", "NO")
    return env


def validate(env: Dict[str, Any], check_paths: bool) -> None:
    required_if_var = ["OBS_LIST_YAML", "VAR_JEDI_TEST_YAML", "FV3INC_JEDI_TEST_YAML"]
    required_if_ens = ["OBS_LIST_YAML", "LETKF_JEDI_TEST_YAML", "OBS_JEDI_TEST_YAML", "SOL_JEDI_TEST_YAML", "FV3INC_JEDI_TEST_YAML"]

    if env.get("DO_JEDIATMVAR") == "YES":
        for k in required_if_var:
            if not env.get(k):
                raise ValueError(f"Missing required key for DO_JEDIATMVAR=YES: {k}")

    if env.get("DO_JEDIATMENS") == "YES":
        for k in required_if_ens:
            if not env.get(k):
                raise ValueError(f"Missing required key for DO_JEDIATMENS=YES: {k}")

    if check_paths:
        for k, v in env.items():
            if k.endswith("_YAML") and isinstance(v, str):
                # allow unresolved ${HOMEglobal}; only check if no vars remain
                if "${" in v:
                    continue
                p = pathlib.Path(v)
                if not p.exists():
                    raise FileNotFoundError(f"{k} path does not exist: {p}")


def render(template_path: pathlib.Path, out_path: pathlib.Path, envvars: Dict[str, Any]) -> None:
    j2env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = j2env.get_template(template_path.name)
    text = tpl.render(cfg=envvars)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    out_path.chmod(0o755)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, type=pathlib.Path)
    p.add_argument("--template", required=True, type=pathlib.Path)
    p.add_argument("--output", required=True, type=pathlib.Path)
    p.add_argument("--homeglobal", default=None, help="Value used to expand ${HOMEglobal} in !INC path.")
    p.add_argument("--check-paths", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    root_ctx = {}
    if args.homeglobal:
        root_ctx["HOMEglobal"] = args.homeglobal

    cfg = load_yaml_with_inc(args.config, root_ctx)
    envvars = flatten_to_env(cfg)
    validate(envvars, args.check_paths)
    render(args.template, args.output, envvars)
    print(f"Generated: {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
