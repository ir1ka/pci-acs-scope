import importlib.util,subprocess,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('prebuilt',Path(__file__).resolve().parents[1]/'tools/build_prebuilt_deb.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
class RevisionOrdering(unittest.TestCase):
 def test_binary_revision_upgrades_same_abi(self):
  new=app.binary_version('0.5.0-3','7.0.0-30-generic')
  self.assertEqual(new,'0.5.0+k7.0.0.30.generic-3')
  self.assertEqual(subprocess.run(['dpkg','--compare-versions',new,'gt',app.binary_version('0.5.0','7.0.0-30-generic')]).returncode,0)
