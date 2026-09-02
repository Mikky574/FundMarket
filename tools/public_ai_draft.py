"""QQ/Codex bridge entry point: generate a public-AI draft without recording it."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.qq_control.draft_research import draft


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an unpersisted public-AI decision draft")
    parser.add_argument("--question", default="请审查当前公共 AI 模拟组合并给出下一步草案。")
    args = parser.parse_args()
    print(json.dumps(draft(args.question), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
