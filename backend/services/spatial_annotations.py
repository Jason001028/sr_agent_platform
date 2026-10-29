"""Display-only spatial results. No path, mask or production authority."""
import math
import json
from copy import deepcopy
from uuid import uuid5, NAMESPACE_URL
from backend.tools.contract import Tool, ok, err


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def geometry(kind, coords, w, h, *, closed=False):
    if not isinstance(coords, list):
        raise ValueError('coordinates must be an array')
    if kind == 'point' and len(coords) == 2:
        points = [coords]
    elif kind == 'bbox' and len(coords) == 4:
        points = [coords[:2], coords[2:]]
        if not all(number(v) for v in coords) or not (coords[0] < coords[2] and coords[1] < coords[3]):
            raise ValueError('invalid bbox; expected left, top, right, bottom')
    elif kind == 'polygon' and 3 <= len(coords) <= 128:
        points = coords
    else:
        raise ValueError('invalid geometry')
    if not all(isinstance(p, list) and len(p) == 2 and all(number(v) for v in p)
               and 0 <= p[0] and 0 <= p[1] and (p[0] <= w if closed or kind != 'point' else p[0] < w) and (p[1] <= h if closed or kind != 'point' else p[1] < h) for p in points):
        raise ValueError(f'coordinates outside image: size={w}x{h}, received={repr(coords)[:500]}; x=column, y=row; point requires 0<=x<width, 0<=y<height; bbox/polygon edges may equal width/height. Use the declared input coordinate_space; do not use source dimensions for input_pixel.')
    if kind == 'polygon':
        area = sum(p[0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*p[1] for i,p in enumerate(points))
        if not area:
            raise ValueError('degenerate polygon')
        # Reject intersecting edges, including overlapping collinear segments.
        def cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        for i,a in enumerate(points):
            b=points[(i+1)%len(points)]
            if a == b: raise ValueError('duplicate polygon vertex')
            for j in range(i+1,len(points)):
                if j == i+1 or (i == 0 and j == len(points)-1): continue
                c,d=points[j],points[(j+1)%len(points)]
                if max(min(a[0],b[0]),min(c[0],d[0])) <= min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1])) <= min(max(a[1],b[1]),max(c[1],d[1])) and cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0:
                    raise ValueError('self-intersecting polygon')
    return {'type': kind, 'coordinates': deepcopy(coords)}


def validate_input(m, actual_size):
    if not isinstance(m, dict): raise ValueError('missing visual mapping')
    m=deepcopy(m)
    if not isinstance(m.get('input_ref'),str) or not 1 <= len(m['input_ref']) <= 128: raise ValueError('invalid input_ref')
    image=m.get('image_ref', {})
    if not isinstance(image,dict) or not isinstance(image.get('image_key'),str) or not 1<=len(image['image_key'])<=512: raise ValueError('invalid image key')
    w,h=image.get('source_width'),image.get('source_height')
    if not all(number(v) and v>0 and v==int(v) for v in (w,h)): raise ValueError('invalid source size')
    if (m.get('width'),m.get('height')) != tuple(actual_size): raise ValueError('attachment dimensions mismatch')
    crop=m.get('crop')
    if not isinstance(crop,list) or len(crop)!=4 or not all(number(v) for v in crop): raise ValueError('invalid crop')
    x,y,cw,ch=crop
    if x<0 or y<0 or cw<=0 or ch<=0 or x+cw>w+1e-6 or y+ch>h+1e-6: raise ValueError('crop outside source')
    return {k:m[k] for k in ('input_ref','image_ref','width','height','crop')}


def bound_tool(inputs):
    frozen={m['input_ref']:deepcopy(m) for m in inputs}
    def present(input_ref, annotations, coordinate_space='input_pixel'):
        try:
            if input_ref not in frozen: raise ValueError('input_ref is not a visual attachment of this message')
            m=frozen[input_ref]
            if not isinstance(annotations,list) or not 1<=len(annotations)<=64: raise ValueError('expected 1..64 annotations')
            if coordinate_space not in ('input_pixel','normalized_01','normalized_1000'):
                raise ValueError('unsupported coordinate_space; use input_pixel, normalized_01 or normalized_1000')
            out=[]
            for i,a in enumerate(annotations):
                for field,limit in [('title',80),('summary',500)]:
                    if not isinstance(a.get(field),str) or not 1<=len(a[field])<=limit: raise ValueError('invalid '+field)
                try:
                    if coordinate_space=='input_pixel':
                        g=geometry(a['type'],a['coordinates'],m['width'],m['height'])
                    else:
                        bound=1 if coordinate_space=='normalized_01' else 1000
                        g=geometry(a['type'],a['coordinates'],bound,bound,closed=True)
                        def scale(p):return [p[0]*(m['width']-1)/bound,p[1]*(m['height']-1)/bound]
                        c=g['coordinates']
                        scaled=scale(c) if g['type']=='point' else (scale(c[:2])+scale(c[2:]) if g['type']=='bbox' else [scale(p) for p in c])
                        g=geometry(g['type'],scaled,m['width'],m['height'])
                except ValueError as exc:
                    raise ValueError(f'annotation[{i}], input_ref={input_ref}, coordinate_space={coordinate_space}: {exc}') from exc
                def convert(p): return [m['crop'][0]+p[0]*m['crop'][2]/m['width'],m['crop'][1]+p[1]*m['crop'][3]/m['height']]
                c=g['coordinates']
                mapped=convert(c) if g['type']=='point' else (convert(c[:2])+convert(c[2:]) if g['type']=='bbox' else [convert(p) for p in c])
                g=geometry(g['type'],mapped,m['image_ref']['source_width'],m['image_ref']['source_height'])
                g['coordinate_space']='source_pixel'
                out.append(dict(version=1,annotation_id=str(uuid5(NAMESPACE_URL,input_ref+':'+str(i)+':'+repr(a)+(':'+coordinate_space if coordinate_space!='input_pixel' else ''))),image_ref=m['image_ref'],input_ref=input_ref,geometry=g,title=a['title'],summary=a['summary'],source='agent',verification='unverified'))
            if len(json.dumps(out, ensure_ascii=False).encode('utf-8')) > 128000:
                raise ValueError('spatial result exceeds 128KB; provide fewer regions')
            return ok({'spatial_annotations':out})
        except (ValueError,KeyError,TypeError) as exc:
            return err(str(exc))
    sizes='; '.join(f"{ref}: width={m['width']}, height={m['height']}" for ref,m in frozen.items())
    return Tool('present_spatial_annotations',
        '用户要求标注或显示区域时，用本工具展示真实视觉附件中具有定位依据的待核实候选。无需经纬度或投影；这是图像像素批注，不是地理坐标。input_ref 来自本轮 visual_inputs，坐标是该附件像素，原点左上，x=列、y=行；bbox=[left,top,right,bottom]，polygon 顶点不重复闭合。禁止从模糊方位或元数据虚构精确数值。无法定位时仅文字回答。工具只验证结构和映射，不保证视觉位置准确，不创建 ROI 或掩码。'
        + ' Input attachment sizes: '+sizes+'. coordinate_space: input_pixel (default), normalized_01, or normalized_1000. bbox right/bottom may equal width/height; point must be strictly inside. Normalized endpoints map to last pixel centers. No automatic coordinate inference.',
        {'type':'object','required':['input_ref','annotations'],'additionalProperties':False,'properties':{
          'coordinate_space':{'type':'string','enum':['input_pixel','normalized_01','normalized_1000'],'description':'Default input_pixel. Normalized coordinates map 0..1 or 0..1000 to attachment pixel centers 0..width-1, 0..height-1. Never guess or mix spaces.'},'input_ref':{'type':'string','enum':list(frozen)},'annotations':{'type':'array','minItems':1,'maxItems':64,'items':{'type':'object','additionalProperties':False,'required':['type','coordinates','title','summary'],'properties':{'type':{'type':'string','enum':['point','bbox','polygon']},'coordinates':{'type':'array','items':{'anyOf':[{'type':'number'},{'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2}]}},'title':{'type':'string','minLength':1,'maxLength':80},'summary':{'type':'string','minLength':1,'maxLength':500}}}}}},present)
