"""Run the deterministic EmbodiedOps offline evaluation fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from signalforge.embodied.business_metrics import run_embodied_business_eval
from signalforge.embodied.eval import run_embodied_eval


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--business-fixture", type=Path)
    parser.add_argument("--business-output", type=Path)
    args = parser.parse_args()
    if (args.business_fixture is None) != (args.business_output is None):
        parser.error("--business-fixture and --business-output must be provided together")
    report = run_embodied_eval(fixture_path=args.fixture, output_path=args.output)
    if args.business_fixture is None:
        print(report.model_dump_json(indent=2))
        return
    business_report = run_embodied_business_eval(
        fixture_path=args.business_fixture,
        output_path=args.business_output,
    )
    print(
        json.dumps(
            {
                "diagnosis_evaluation": report.model_dump(mode="json"),
                "business_evaluation": business_report,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
