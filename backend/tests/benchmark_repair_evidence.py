"""Machine-readable bounded-reader benchmark. Never submits SR."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
import tifffile
from backend.services.evidence_raster import read_window


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    path=root/'benchmark.tif';tifffile.imwrite(path,np.arange(4096*4096,dtype=np.uint16).reshape(4096,4096),rowsperstrip=16)
    samples=[]
    for _ in range(5):
        start=time.monotonic();_,_,meta=read_window(path,[1700,1700,2212,2212]);samples.append({**meta,'elapsed_ms':(time.monotonic()-start)*1000})
    values=[s['elapsed_ms'] for s in samples]
    Path(args.output).write_text(json.dumps({'schema_version':'evidence-benchmark.v1','source':'synthetic_warm_cache','samples':samples,
        'median_ms':float(np.median(values)),'p95_ms':float(np.percentile(values,95)),
        'limitations':['No production array or GPU','No cold-cache claim','A/B critical-path comparison pending']},indent=2),encoding='utf-8')

if __name__=='__main__':main()
