import sys
from pathlib import Path

from knowledge_service.documentation import validate_repository


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    issues = validate_repository(root)
    for issue in issues:
        sys.stdout.write(f"{issue.source}: {issue.message}\n")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
