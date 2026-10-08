"""Extract the three target Mermaid diagrams from the technical report."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT = ROOT / "docs" / "01_Technical_Architecture.md"
OUTPUT = ROOT / "docs" / "diagrams"


def main():
    text = DOCUMENT.read_text(encoding="utf-8")
    for section, name in (
        ("5.2", "architecture-a-target"),
        ("6.2", "architecture-b-target"),
        ("7.2", "architecture-c-target"),
    ):
        pattern = rf"### {re.escape(section)} Target logical architecture[^\n]*\n.*?```mermaid\n(.*?)\n```"
        matches = re.findall(pattern, text, flags=re.DOTALL)
        if len(matches) != 1:
            raise ValueError(f"Expected one target diagram for section {section}")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        target = OUTPUT / f"{name}.mmd"
        target.write_text(matches[0] + "\n", encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
