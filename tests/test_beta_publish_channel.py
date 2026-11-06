from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BetaPublishTests(unittest.TestCase):
    def test_publish_and_ci_explicitly_support_beta_without_latest_fallback(self):
        publish = (ROOT/'.github/workflows/npm-publish.yml').read_text(encoding='utf8')
        ci = (ROOT/'.github/workflows/ci.yml').read_text(encoding='utf8')
        self.assertIn('branches: [main, nightly, beta]', publish)
        self.assertIn('branches: [main, nightly, beta]', ci)
        self.assertIn('nightly|beta)', publish)
        self.assertIn('main)', publish)
        self.assertIn('Unsupported publish ref', publish)
        self.assertIn('new="${base}-${kind}.${stamp}"', publish)
        self.assertIn('"${version}" "${tag}"', publish)
        self.assertIn('npm dist-tag add "${pkg}@${previous}" "${tag}"', publish)
        self.assertNotIn('npm dist-tag add "${pkg}@${previous}" nightly', publish)
