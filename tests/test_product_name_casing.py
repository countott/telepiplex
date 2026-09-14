"""Check display branding separately from technical identities.

Read current source entry points only. Historical documents and generated
artifacts are intentionally outside this policy check.
"""

import ast
from pathlib import Path
import re
import tokenize
import tomllib
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ("download", "search", "rename", "sync", "caption")
PROJECTS = ("sdk", "examples/echo_feature", *(f"features/{m}" for m in FEATURES))


def _project(directory):
    return tomllib.loads((ROOT / directory / "pyproject.toml").read_text())["project"]


class ProductNameCasingTest(unittest.TestCase):
    def test_public_display_names_use_brand_casing(self):
        from app.runtime.command_catalog import build_start_help

        help_text = build_start_help(None, "v1.2.3-host")
        self.assertIn("<b>Telepiplex v1.2.3-host</b>", help_text)
        self.assertIn("<b>Telepiplex</b>", help_text)
        self.assertNotIn("<b>telepiplex", help_text)
        for name in ("README.md", "README_EN.md"):
            with self.subTest(file=name):
                self.assertEqual((ROOT / name).read_text().splitlines()[0], "# Telepiplex")
        for directory in PROJECTS:
            description = _project(directory).get("description", "")
            with self.subTest(project=directory):
                for match in re.finditer("telepiplex", description, re.IGNORECASE):
                    self.assertEqual(match.group(), "Telepiplex")

    def test_package_and_module_identities_remain_lowercase(self):
        for directory in PROJECTS:
            with self.subTest(project=directory):
                name = _project(directory)["name"]
                self.assertTrue(name.startswith("telepiplex-"))
                self.assertEqual(name, name.lower())
        for module in FEATURES:
            manifest = yaml.safe_load((ROOT / "features" / module / "manifest.yaml").read_text())
            with self.subTest(module=module):
                self.assertEqual(manifest["plugin_id"], module)
                self.assertEqual(manifest["entry_point"], f"telepiplex_{module}.runtime:main")

    def test_python_identifiers_keep_code_or_constant_casing(self):
        patterns = (
            "app/**/*.py", "sdk/src/**/*.py", "features/*/src/**/*.py",
            "tools/**/*.py", "examples/*/src/**/*.py",
        )
        violations = []
        for pattern in patterns:
            for path in ROOT.glob(pattern):
                with tokenize.open(path) as source:
                    for token in tokenize.generate_tokens(source.readline):
                        if token.type != tokenize.NAME or "telepiplex" not in token.string.lower():
                            continue
                        if not (token.string.islower() or token.string.isupper()):
                            violations.append(f"{path.relative_to(ROOT)}:{token.start[0]}:{token.string}")
        self.assertEqual(violations, [], "Brand casing must not rename Python identifiers")

    def test_workflow_display_names_do_not_rename_jobs_or_tags(self):
        for name in ("release.yml", "release-feature.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
            with self.subTest(workflow=name):
                self.assertIn("Telepiplex", workflow["name"])
                for job_id, job in workflow["jobs"].items():
                    self.assertEqual(job_id, job_id.lower())
                    for step in job["steps"]:
                        for match in re.finditer("telepiplex", step.get("name", ""), re.IGNORECASE):
                            self.assertEqual(match.group(), "Telepiplex")
                triggers = workflow.get("on", workflow.get(True))
                for tag in triggers["push"]["tags"]:
                    self.assertEqual(tag, tag.lower())

    def test_protocol_names_keep_their_existing_identity(self):
        paths = (
            "app/runtime/poster_grid.py",
            "features/download/src/telepiplex_download/client.py",
            *(f"features/search/src/telepiplex_search/adapters/{n}.py"
              for n in ("douban", "wikipedia", "wikidata")),
        )
        for name in paths:
            tree = ast.parse((ROOT / name).read_text())
            agents = [node.value for node in ast.walk(tree)
                      if isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and re.match(r"telepiplex(?:-Feature)?/", node.value, re.IGNORECASE)]
            with self.subTest(file=name):
                self.assertTrue(agents)
                self.assertTrue(all(value.startswith(("telepiplex/", "telepiplex-Feature/"))
                                    for value in agents))
        tree = ast.parse((ROOT / "features/sync/src/telepiplex_sync/mcp_server.py").read_text())
        servers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "FastMCP"]
        self.assertEqual(len(servers), 1)
        self.assertEqual(ast.literal_eval(servers[0].args[0]), "telepiplex Plex")

    def test_readme_version_tables_match_current_source(self):
        source = (ROOT / "app/115bot.py").read_text()
        host = re.search(r'version = "v(\d+\.\d+\.\d+)-host"', source).group(1)
        expected = {"Host": host, "SDK": _project("sdk")["version"]}
        expected.update({f"`{m}`": _project(f"features/{m}")["version"] for m in FEATURES})
        for name in ("README.md", "README_EN.md"):
            rows = dict(re.findall(r"^\| ([^|]+?) \| `([^`]+)` \|$", (ROOT / name).read_text(), re.MULTILINE))
            for component, version in expected.items():
                with self.subTest(file=name, component=component):
                    self.assertEqual(rows.get(component), version)


if __name__ == "__main__":
    unittest.main()
