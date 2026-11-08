#!/usr/bin/python3
"""Export this component and build packages locally; never install/load."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=('binary', 'source'))
    args = p.parse_args()
    def field(name):
        return subprocess.check_output(['dpkg-parsechangelog', '-l', str(ROOT/'debian/changelog'),
                                        '-S', name], text=True).strip()
    name, version, epoch = field('Source'), field('Version'), int(field('Timestamp'))
    # This independent repository always owns its build/package output.
    output_root = ROOT
    build, dist = output_root/'build', output_root/'dist'
    build.mkdir(exist_ok=True)
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='acs-debian-', dir=build) as tmp:
        work = Path(tmp)
        source = work/(name+'-'+version)
        source.mkdir()
        for directory in ('src', 'bin', 'debian', 'tests', 'tools', 'lib', 'initramfs', 'systemd', 'kernel', 'packaging'):
            shutil.copytree(ROOT/directory, source/directory,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.ko', '*.o', '*.mod*',
                                                         '.*.cmd', 'Module.symvers', 'modules.order',
                                                         '.debhelper', 'files', '*.substvars',
                                                         'debhelper-build-stamp', 'pci-acs-scope-dkms'))
        from preinstall import text
        preinst=source/'debian/pci-acs-scope-native.preinst'
        preinst.write_text(text(ROOT));preinst.chmod(0o755)
        for filename in ('README.md', 'Makefile', 'dkms.conf', 'LICENSE'):
            shutil.copy2(ROOT/filename, source/filename)
        for item in sorted(source.rglob('*'), reverse=True):
            os.utime(item, (epoch, epoch), follow_symlinks=False)
        os.utime(source, (epoch, epoch))
        if args.kind == 'source':
            upstream = version.rsplit('-', 1)[0]
            original = work / (name + '_' + upstream + '.orig.tar.xz')
            subprocess.run(['tar', '--sort=name', '--mtime=@' + str(epoch),
                            '--owner=0', '--group=0', '--numeric-owner',
                            '--exclude=' + source.name + '/debian',
                            '-cJf', str(original), '-C', str(work), source.name], check=True)
        subprocess.run(['dpkg-buildpackage', '-b' if args.kind == 'binary' else '-S', '-us', '-uc'],
                       cwd=source, env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)), check=True)
        for item in sorted(work.iterdir()):
            if item.is_file():
                shutil.copy2(item, dist/item.name)
                print(dist/item.name)


if __name__ == '__main__':
    main()
