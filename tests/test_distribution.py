"""Public release excludes host-specific source paths and operational configuration."""
from pathlib import Path
import unittest

class DistributionTests(unittest.TestCase):
    def test_no_hardcoded_existing_service_paths(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('README.md', 'deploy/setup.py'):
            text = (root / name).read_text()
            self.assertNotIn('/opt/projects/', text, name)
            self.assertNotIn('/root/.config/', text, name)
