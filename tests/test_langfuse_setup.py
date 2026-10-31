import json
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.setup_langfuse_local import initialize


class SetupTests(unittest.TestCase):
    def test_private_credentials_are_stable_across_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'local'; initialize(state)
            before = {p.name: p.read_bytes() for p in state.iterdir()}
            initialize(state)
            self.assertEqual(before, {p.name: p.read_bytes() for p in state.iterdir()})
            for p in state.iterdir(): self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertNotEqual(json.loads(before['access.json'])['password'], 'password')

    def test_interrupted_initialization_repairs_derived_files_without_changing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'local'; initialize(state)
            original = (state/'local.env').read_bytes(); login = (state/'access.json').read_bytes()
            (state/'app.env').unlink(); (state/'access.json').unlink()
            initialize(state)
            self.assertEqual((state/'local.env').read_bytes(), original)
            self.assertEqual((state/'access.json').read_bytes(), login)


if __name__ == '__main__': unittest.main()
