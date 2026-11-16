"""Build local inert TPXs; old contracts are frozen from the 3.10.1 source baseline."""
import json
from pathlib import Path

import yaml

from app.runtime.plugin_artifact import build_tpx


def release(root, name, *, old=False):
    here = Path(__file__).parent
    if old:
        row = json.loads((here / "fixtures/config_contracts_host_3_10_1.json").read_bytes())[name]
    else:
        src = here.parent / "features" / name
        row = {"manifest": yaml.safe_load((src / "manifest.yaml").read_bytes()),
               "defaults": yaml.safe_load((src / "config.default.yaml").read_bytes()),
               "schema": json.loads((src / "config.schema.json").read_bytes())}
    m = row["manifest"]
    m["source"] = {"repository": "local/telepiplex", "branch": "main",
                   "commit": ("a" if old else "b") * 40}
    folder = root / ("old" if old else "new") / name
    (folder / "wheelhouse").mkdir(parents=True)
    (folder / "plugin.whl").write_bytes(b"inert child")
    (folder / "wheelhouse/sdk.whl").write_bytes(b"inert sdk")
    (folder / "manifest.yaml").write_text(yaml.safe_dump(m))
    (folder / "config.default.yaml").write_text(yaml.safe_dump(row["defaults"], sort_keys=False))
    (folder / "config.schema.json").write_text(json.dumps(row["schema"]))
    return build_tpx(folder, folder.parent / f"{name}-{m['version']}.tpx")
