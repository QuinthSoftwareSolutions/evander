"""Private job file operations shared by optional providers."""

PREPARE = r"""
import os, stat, sys
base, lease=sys.argv[1:3]
for path in [base, base+'/'+lease]:
    try:
        os.mkdir(path, 0o711)
        os.chmod(path, 0o711)
    except FileExistsError: pass
    st=os.lstat(path)
    allowed={0o700, 0o711, 0o733} if path != base else {0o700, 0o711}
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in allowed:
        raise RuntimeError('Unsafe resource directory')
    if stat.S_IMODE(st.st_mode) == 0o700:
        os.chmod(path, 0o711)
directory=base+'/'+lease
try: os.unlink(directory+'/connection.new')
except FileNotFoundError: pass
os.chmod(directory, 0o733)
"""
PROJECT = r"""
import json, os, socket, stat, sys
data=json.load(sys.stdin)
base, owner=sys.argv[1], int(sys.argv[2])
for path, mode in [(base, 0o711), (base+'/'+data['lease_id'], 0o733)]:
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != owner or stat.S_IMODE(st.st_mode) != mode:
        raise RuntimeError('Unsafe resource directory')
directory=base+'/'+data['lease_id']
temporary=directory+'/connection.new'
fd=os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data['connections'], stream)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, directory+'/connection.json')
finally:
    if os.path.lexists(temporary): os.unlink(temporary)
if data.get('probe_connections'):
    for connection in data['connections'].values():
        host=connection.get('server') or connection['host']
        with socket.create_connection((host, connection['port']), timeout=3): pass
"""
SEAL = r"""
import os, stat, sys
base, lease, uid, gid=sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
for path in [base, base+'/'+lease]:
    st=os.lstat(path)
    allowed={0o711, 0o733} if path != base else {0o711}
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in allowed:
        raise RuntimeError('Unsafe resource directory')
directory=base+'/'+lease
os.chmod(directory, 0o711)
path=directory+'/connection.json'
st=os.lstat(path)
if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != 0o600:
    raise RuntimeError('Unsafe resource configuration')
"""
UNPROJECT = r"""
import os, stat, sys
path=sys.argv[1]
if not os.path.lexists(path): sys.exit(0)
for component in [sys.argv[2]]:
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022: raise RuntimeError('Unsafe resource directory')
    path+='/'+component
if os.path.lexists(path):
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in {0o700, 0o711, 0o733}: raise RuntimeError('Unsafe resource directory')
    os.chmod(path, 0o711)
    for name in ['connection.json','connection.new']:
        try: os.unlink(path+'/'+name)
        except FileNotFoundError: pass
    os.rmdir(path)
"""
