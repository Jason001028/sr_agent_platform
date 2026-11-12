"""Reproducible synthetic CPU/RSS measurements; no accuracy claim."""
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import psutil
from scipy.ndimage import gaussian_filter
from backend.services.image_diff import analyze_arrays

def benchmark(edge):
    rng=np.random.default_rng(20261112)
    a=rng.uniform(20000,50000,(edge,edge)).astype(np.float32)
    b=a.copy();lo,hi=edge//3,edge*2//3
    b[lo:hi,lo:hi]=gaussian_filter(a,2)[lo:hi,lo:hi]
    process=psutil.Process();baseline=process.memory_info().rss;peak=[baseline];stop=threading.Event()
    def sample():
        while not stop.wait(.005):peak[0]=max(peak[0],process.memory_info().rss)
    thread=threading.Thread(target=sample);thread.start();started=time.perf_counter()
    try:
        result=analyze_arrays(b,a,method='smart',reference_side='B',source='source-grid',auto_mask=True)
        elapsed=(time.perf_counter()-started)*1000
    finally:stop.set();thread.join()
    return {'edge':edge,'elapsedMs':round(elapsed,2),'baselineRssMiB':round(baseline/2**20,2),
            'peakRssMiB':round(peak[0]/2**20,2),'incrementRssMiB':round((peak[0]-baseline)/2**20,2),
            'regions':len(result['regions']),'source':'synthetic-arrays-including-png-and-auto-mask'}

if __name__=='__main__':
    print(json.dumps({'platform':sys.platform,'python':sys.version.split()[0],
        'measurements':[benchmark(int(x)) for x in (sys.argv[1:] or ['512','1024','2048'])]},indent=2))
