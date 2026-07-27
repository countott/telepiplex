from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PRODUCT_NAME = "Tele" + "piplex"
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".stfolder",
    ".venv",
    "__pycache__",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {
    "Dockerfile",
    "LICENSE",
}


def _repository_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRECTORY_NAMES for part in path.parts):
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in TEXT_FILENAMES:
            continue
        yield path


class ProductNameCasingTest(unittest.TestCase):
    def test_product_name_is_lowercase_in_repository_text(self):
        violations = []
        for path in _repository_text_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), 1):
                if FORBIDDEN_PRODUCT_NAME in line:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line_number}:{line.strip()}"
                    )

        self.assertEqual(
            violations,
            [],
            "product name must be lowercase `telepiplex`:\n"
            + "\n".join(violations[:100]),
        )


if __name__ == "__main__":
    unittest.main()
