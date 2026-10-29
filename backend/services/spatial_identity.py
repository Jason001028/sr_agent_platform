"""Read-only filesystem resource revision; not file access authority."""
import hashlib
import json

def resource_identity(path):
    path=path.resolve(strict=True)
    st=path.stat()
    resource=hashlib.sha256(str(path).encode('utf-8')).hexdigest()
    revision=hashlib.sha256(json.dumps([st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]).encode()).hexdigest()
    return {'image_key':'scene-v1:'+resource+':'+revision}
