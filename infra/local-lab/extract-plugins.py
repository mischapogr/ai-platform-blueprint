"""Extract checksum-pinned signed plugin archives into one directory per plugin ID."""

import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

for archive in sorted(Path('/archives').glob('*.zip')):
    with ZipFile(archive) as source:
        manifests = [p for p in source.namelist() if p.endswith('/plugin.json')
                     and p.count('/') == 1]
        if len(manifests) != 1:
            raise ValueError('Unexpected plugin archive layout')
        manifest = manifests[0]
        data = json.loads(source.read(manifest))
        if data['id'] != archive.stem:
            raise ValueError('Plugin identity mismatch')
        prefix = manifest.removesuffix('plugin.json')
        for member in source.infolist():
            if not member.filename.startswith(prefix):
                raise ValueError('Unexpected plugin archive member')
            relative = PurePosixPath(member.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts or member.is_dir():
                if member.is_dir():
                    continue
                raise ValueError('Unsafe plugin path')
            if member.file_size > 512 * 1024 * 1024:
                raise ValueError('Oversized plugin file')
            target = Path('/out') / archive.stem / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(member))
            target.chmod(0o755 if (member.external_attr >> 16) & 0o111 else 0o644)
        if not (Path('/out') / archive.stem / 'MANIFEST.txt').is_file():
            raise ValueError('Official plugin signature manifest missing')
