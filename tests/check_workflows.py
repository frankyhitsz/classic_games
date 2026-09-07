"""Parse workflow configuration before a release can be pushed."""

from pathlib import Path

import yaml


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    if not workflows:
        raise ValueError("no workflow files found")
    for path in workflows:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(workflow.get("jobs"), dict):
            raise ValueError(f"{path.name}: missing jobs")
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "run" in step and not isinstance(step["run"], str):
                    raise ValueError(f"{path.name}: run must be a shell string")
    print(f"workflow-config: {len(workflows)} valid YAML workflow(s)")


if __name__ == "__main__":
    main()
