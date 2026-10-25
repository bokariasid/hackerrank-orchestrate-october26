import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from model import LocalModel


class ModelTests(unittest.TestCase):
    def test_missing_credential_fails_before_network(self):
        prior = os.environ.pop("MLX_VLM_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as directory:
                model = LocalModel(Path(directory))
                with self.assertRaisesRegex(RuntimeError, "MLX_VLM_API_KEY"):
                    model.extract("Return an object", ["label"])
        finally:
            if prior is not None:
                os.environ["MLX_VLM_API_KEY"] = prior

    def test_cached_response_needs_no_credential_or_call(self):
        with tempfile.TemporaryDirectory() as directory:
            model = LocalModel(Path(directory))
            key = model.cache_key("Return an object", ["label"], None)
            (Path(directory) / (key + ".json")).write_text('{"label":"ok"}')
            self.assertEqual(model.extract("Return an object", ["label"]), {"label": "ok"})
            self.assertEqual(model.usage["calls"], 0)


if __name__ == "__main__":
    unittest.main()
