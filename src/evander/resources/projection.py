"""Private job file operations shared by optional providers."""

PRIVATE_TMPFS = "/run/evander-resources"
PRIVATE_TMPFS_SPEC = PRIVATE_TMPFS + ":rw,nosuid,nodev,noexec,size=16m,mode=1777"
RESOURCE_CHAIN = "EVANDER-RESOURCE-EGRESS"

PREPARE = r"""
import os, stat, sys
base, lease=sys.argv[1:3]
for path in [base, base+'/'+lease]:
    try: os.mkdir(path, 0o711)
    except FileExistsError: pass
    st=os.lstat(path)
    allowed={0o700, 0o711, 0o733} if path != base else {0o700, 0o711}
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in allowed:
        raise RuntimeError('Unsafe resource directory')
    os.chmod(path, 0o711)
"""
PROJECT = r"""
import json, os, re, socket, stat, sys
data=json.load(sys.stdin)
base, owner, temporary=sys.argv[1], int(sys.argv[2]), sys.argv[3]
if os.path.dirname(temporary) != os.path.dirname(base) or not re.fullmatch(r'evander-resource-[a-f0-9]{32}', os.path.basename(temporary)):
    raise RuntimeError('Unsafe resource staging path')
for path in [base, base+'/'+data['lease_id']]:
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != owner or stat.S_IMODE(st.st_mode) != 0o711:
        raise RuntimeError('Unsafe resource directory')
fd=os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data['connections'], stream)
        stream.flush(); os.fsync(stream.fileno())
    if data.get('probe_connections'):
        for connection in data['connections'].values():
            host=connection.get('server') or connection['host']
            with socket.create_connection((host, connection['port']), timeout=3): pass
except BaseException:
    os.unlink(temporary)
    raise
"""
SEAL = r"""
import os, re, stat, sys
base, lease, uid, gid=sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
temporary=sys.argv[5]
if os.path.dirname(temporary) != os.path.dirname(base) or not re.fullmatch(r'evander-resource-[a-f0-9]{32}', os.path.basename(temporary)):
    raise RuntimeError('Unsafe resource staging path')
for path in [base, base+'/'+lease]:
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o711:
        raise RuntimeError('Unsafe resource directory')
st=os.lstat(temporary)
if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
    raise RuntimeError('Unsafe resource configuration')
# Publish into a parent the job cannot rename entries in, then verify that the
# staging entry was not replaced between lstat and rename. No CHOWN capability.
path=base+'/'+lease+'/connection.json'
os.replace(temporary, path)
actual=os.lstat(path)
if (actual.st_dev, actual.st_ino) != (st.st_dev, st.st_ino):
    os.unlink(path)
    raise RuntimeError('Resource staging entry changed')
"""
UNPROJECT = r"""
import os, re, shutil, stat, sys, uuid
base, lease=sys.argv[1:3]
if not os.path.lexists(base): sys.exit(0)
st=os.lstat(base)
if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in {0o700, 0o711}:
    raise RuntimeError('Unsafe resource directory')

def discard_junk(path):
    if not os.path.lexists(path): return
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            os.unlink(path)
            return
        if not shutil.rmtree.avoids_symlink_attacks:
            raise OSError('Safe tree removal unavailable')
        shutil.rmtree(path)
        return
    except OSError:
        # A legacy job-writable directory may contain unreadable job-owned
        # subdirectories. Hide only that job's junk under a private root-owned
        # parent; its tmpfs disappears at job end. It must not block revocation
        # or reuse of the canonical resource path. Never follow its symlinks.
        retired=base+'/.retired'
        try: os.mkdir(retired, 0o700)
        except FileExistsError: pass
        st=os.lstat(retired)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise RuntimeError('Unsafe retired resource directory')
        os.rename(path, retired+'/'+uuid.uuid4().hex)

if len(sys.argv) > 3 and sys.argv[3]:
    temporary=sys.argv[3]
    if os.path.dirname(temporary) != os.path.dirname(base) or not re.fullmatch(r'evander-resource-[a-f0-9]{32}', os.path.basename(temporary)):
        raise RuntimeError('Unsafe resource staging path')
    discard_junk(temporary)
path=base+'/'+lease
if os.path.lexists(path):
    st=os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) not in {0o700, 0o711, 0o733}:
        raise RuntimeError('Unsafe resource directory')
    os.chmod(path, 0o700)
    # Unlink the actual private files before handling legacy untrusted junk.
    for name in ['connection.json','connection.new']:
        entry=path+'/'+name
        if os.path.lexists(entry) and not stat.S_ISDIR(os.lstat(entry).st_mode): os.unlink(entry)
    discard_junk(path)
"""
