"""P2.2 read-only benchmark. Run from the repository root in the GDAL environment."""
import argparse
import io
import json
from pathlib import Path
from statistics import median
from threading import Event, Thread
from time import perf_counter

import numpy as np
from PIL import Image, ImageDraw
import psutil
import tifffile

from backend.services.image_tiles import ImageTiles, tile_window
from backend.services.raster_tone import display_pixels, parse_display
from backend.services.raster_window import raster_revision

SIZE = (49475, 49454)
ORIGIN = 24576


def pattern():
    # Keep the original audit's 1024-cell and fixed ROI origin, with independent
    # bands for weak detail, a one-pixel road, periodic signal, and phase aliasing.
    y, x = np.indices((1024,1024))
    dn = (7000 + 4*x + 3*y + (x*233+y*457+x*y*23)%683).astype(np.uint16)
    dn[64:288,64:224] = 7000
    dn[64:288,130] = 39000
    dn[320:448,64:384] = 12000 + 4000*((y[320:448,64:384]//16)%2)
    dn[512:640,64:384] = 12000 + 4000*((x[512:640,64:384]+(y[512:640,64:384]//16))%2)
    dn[672:800,64:384] = 12000 + 4000*((x[672:800,64:384]+(y[672:800,64:384]//16)+1)%2)
    dn[480:504,509:516] = np.arange(21700,21707,dtype=np.uint16)
    return dn


def generate(path):
    w,h=SIZE
    dn=pattern()
    xs=np.arange(w)%1024
    rows=(dn[np.ix_(np.arange(y,min(y+32,h))%1024,xs)] for y in range(0,h,32))
    tifffile.imwrite(path,data=rows,shape=(h,w),dtype=np.uint16,bigtiff=True,
                     rowsperstrip=32,photometric="minisblack",metadata=None)


def measured(fn, repeats=1):
    process=psutil.Process()
    peak=[process.memory_info().rss]
    stop=Event()
    def sample():
        while not stop.wait(.002):
            peak[0]=max(peak[0],process.memory_info().rss)
    timer=Thread(target=sample)
    timer.start()
    reads,times=[],[]
    try:
        for _ in range(repeats):
            before=process.io_counters().read_bytes
            start=perf_counter()
            result=fn()
            times.append((perf_counter()-start)*1000)
            reads.append(process.io_counters().read_bytes-before)
        peak[0]=max(peak[0],process.memory_info().rss)
        return result,dict(ms=round(median(times),3),readBytes=int(median(reads)),
                           peakRSS=peak[0],n=repeats)
    finally:
        stop.set()
        timer.join()


def run(path,out):
    service=ImageTiles(None)
    info=service.reader.info(path)
    assert (info.width,info.height)==SIZE
    rev=raster_revision(path)
    raw=np.block([[service.reader.read_window(path,ORIGIN+x*512,ORIGIN+y*512,512,512).dn
                   for x in range(2)] for y in range(2)])
    np.testing.assert_array_equal(raw,pattern())
    tifffile.imwrite(out/"raw-roi.tif",raw,photometric="minisblack",metadata=None)
    stats,stats_cost=measured(lambda:service._source_stats(path,rev,1,info))
    report={"source":[*SIZE],"roi":[ORIGIN,ORIGIN,1024,1024],"stats":stats.summary(),
            "statsCost":stats_cost,"tiles":{}}
    tone=parse_display("window:7000:41000")
    columns=[("L0 reference",display_pixels(raw,None,None,tone))]
    hits=0
    requests=0
    for level in (1,2):
        step=2**level
        x=y=ORIGIN//(512*step)
        window=tile_window(*SIZE,level,x,y)
        for policy in ("nearest","area"):
            method=(lambda:service.reader.read_area(path,*window,step=step,band=1,revision=rev,info=info)) if policy=="area" else (
                lambda:service.reader.read_window(path,*window,revision=rev,grid_step=step))
            read,read_cost=measured(method)
            render=lambda:service._tile(path,rev,1,info,level,x,y,tone,policy)
            tile,cold=measured(render)
            cached,warm=measured(render,7)
            assert cached is tile
            hits+=7
            requests+=8
            with Image.open(io.BytesIO(tile[0])) as im:
                decoded=np.asarray(im).copy()
            q=1024//step
            roi=read.dn[:q,:q]
            if policy=="area":
                np.testing.assert_array_equal(roi,raw.reshape(q,step,q,step).mean((1,3)))
            expanded=np.repeat(np.repeat(roi,step,axis=0),step,axis=1)
            road=np.max(expanded[80:272,126:137],axis=1)
            def amplitude(rows):
                profile=expanded[rows,80:360].astype(float).mean(axis=1)
                return float(np.ptp(profile))
            key=f"L{level}-{policy}"
            report["tiles"][key]=dict(read=read_cost,cold=cold,warm=warm,
                roadContinuity=float(np.mean(road>10000)),
                trueStripeAmplitudeDN=amplitude(slice(336,432)),
                falseStripePhase0AmplitudeDN=amplitude(slice(528,624)),
                falseStripePhase1AmplitudeDN=amplitude(slice(688,784)),
                pngBytes=len(tile[0]))
            pixels=decoded[:q,:q]
            Image.fromarray(pixels).save(out/f"{key}.png")
            columns.append((key,np.repeat(np.repeat(pixels,step,axis=0),step,axis=1)))
    tone_sheet=Image.new("RGB",(420,3*118),"#192027")
    labels=ImageDraw.Draw(tone_sheet)
    levels={}
    for i,config in enumerate(("equal","linear2","window:21700:21706")):
        native=[]
        for tx in (48,49):
            pixels,_=service._tile(path,rev,1,info,0,tx,48,parse_display(config))
            native.append(np.asarray(Image.open(io.BytesIO(pixels))))
        mapped=np.concatenate(native,axis=1)
        levels[config]=mapped[490,509:516].tolist()
        crop=Image.fromarray(mapped[478:506,498:528]).resize((106,84),Image.Resampling.NEAREST)
        labels.text((8,i*118+3),config+" | "+str(levels[config]),fill="white")
        tone_sheet.paste(crop,(8,i*118+22))
    samples=levels["window:21700:21706"]
    assert len(set(samples))==7
    report.update(manualL0Gray=samples,pngCache=dict(hits=hits,requests=requests,rate=hits/requests))
    report["toneComparison"]=levels
    tone_sheet.save(out/"tone-comparison.png")
    Image.fromarray(mapped).save(out/"native-manual.png")
    sheet=Image.new("RGB",(5*256, 3*200+32),"#192027")
    text=ImageDraw.Draw(sheet)
    for i,(name,pixels) in enumerate(columns):
        text.text((i*256+4,6),name,fill="white")
        for j,(y,x) in enumerate(((64,64),(312,64),(510,64))):
            sheet.paste(Image.fromarray(pixels[y:y+200,x:x+256]),(i*256,32+j*200))
    sheet.save(out/"sampling-comparison.png")
    service.reader.release(path)
    service.pool.shutdown()
    (out/"backend.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("source",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--generate",action="store_true")
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if args.generate:
        args.source.parent.mkdir(parents=True,exist_ok=True)
        if args.source.exists():
            p.error("Choose a new synthetic TIFF path")
        generate(args.source)
    run(args.source,args.output)
