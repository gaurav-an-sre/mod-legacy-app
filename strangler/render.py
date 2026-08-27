"""Render the nginx façade configuration from routes.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader


def render_routes(routes_path: Path, output_path: Path) -> None:
    config: dict[str, Any] = yaml.safe_load(routes_path.read_text(encoding="utf-8"))
    template_dir = Path(__file__).parent / "templates"
    template = Environment(loader=FileSystemLoader(template_dir)).get_template("nginx.conf.j2")
    output_path.write_text(template.render(slices=config["slices"]), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=Path, default=Path(__file__).with_name("routes.yaml"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("nginx.conf"))
    args = parser.parse_args()
    render_routes(args.routes, args.output)


if __name__ == "__main__":
    main()
