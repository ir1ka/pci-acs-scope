import subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Rollback(unittest.TestCase):
 def test_partial_clear_is_ours_only_inside_immediate_transaction(self):
  text=(ROOT/'src/pci_acs_scope.c').read_text();start=text.index('static bool restore_conflict(');end=text.index('\n}',start)
  function=text[start:end+2]
  code='#include <stdbool.h>\n#include <assert.h>\ntypedef unsigned short u16;\n'+function+'\nint main(void) {\n for (unsigned original=0;original<128;original++)\n  for (unsigned ctrl=0;ctrl<128;ctrl++) {\n   bool subset=!(ctrl&0x2c&~original);\n   assert(restore_conflict(ctrl,original,0x2c,true)==!subset);\n   bool conflict=(ctrl&0x2c)&&(ctrl&0x2c)!=(original&0x2c);\n   assert(restore_conflict(ctrl,original,0x2c,false)==conflict);\n  }\n assert(!restore_conflict(0x20,0x2c,0x2c,true));\n assert(restore_conflict(0x20,0x2c,0x2c,false));\n return 0;\n}\n'
  with tempfile.TemporaryDirectory()as tmp:
   c=Path(tmp)/'test.c';exe=Path(tmp)/'test';c.write_text(code)
   subprocess.run(['cc','-Wall','-Wextra','-Werror',str(c),'-o',str(exe)],check=True)
   subprocess.run([str(exe)],check=True)
