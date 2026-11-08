#!/usr/bin/env python3
"""Fast OCR for video frames and still images.

Samples video frames at a fixed interval (default 0.5 s), runs Tesseract OCR,
and writes only frames with usable text to an output directory. It also writes
JSON/CSV results so an agent can inspect the exact frame and timestamp later.

Examples:
  python scripts/video_ocr.py scan Assets --interval 0.5 --lang eng
  python scripts/video_ocr.py scan clip.mp4 --interval 0.5 --out-dir temp/video-ocr/clip

The source media is read-only. Snapshots, OCR text and manifests go to --out-dir.
Requires ffmpeg/ffprobe, Pillow and Tesseract OCR on PATH or --tesseract.
"""
from __future__ import annotations
import argparse, csv, json, os, re, shutil, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from typing import Any
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

VIDEO_EXT={'.mp4','.mov','.mkv','.webm','.avi','.m4v','.mts','.m2ts'}
IMAGE_EXT={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'}

def run(cmd:list[str], input_bytes:bytes|None=None):
    try: return subprocess.run(cmd,input=input_bytes,capture_output=True,check=False)
    except FileNotFoundError: raise SystemExit(f'{cmd[0]} staat niet op PATH')

def ffprobe(path:Path)->dict[str,Any]:
    r=run(['ffprobe','-v','error','-print_format','json','-show_streams','-show_format',str(path)])
    if r.returncode: raise RuntimeError(r.stderr.decode(errors='replace'))
    j=json.loads(r.stdout); v=next((s for s in j['streams'] if s.get('codec_type')=='video'),None)
    if not v: raise RuntimeError('geen videostream')
    return {'duration':float(v.get('duration') or j.get('format',{}).get('duration') or 0),'width':v.get('width'),'height':v.get('height')}

def images_from(path:Path, interval:float):
    if path.suffix.lower() in IMAGE_EXT:
        yield 0.0, Image.open(path).convert('RGB'); return
    meta=ffprobe(path); duration=meta['duration']; t=0.0
    while t < duration:
        r=run(['ffmpeg','-v','error','-ss',f'{t:.3f}','-i',str(path),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        if r.stdout:
            from io import BytesIO
            yield t,Image.open(BytesIO(r.stdout)).convert('RGB')
        t += interval

def prepare(im:Image.Image)->Image.Image:
    im=ImageOps.exif_transpose(im).convert('RGB')
    maxw=1600
    if im.width>maxw: im=im.resize((maxw,round(im.height*maxw/im.width)),Image.Resampling.LANCZOS)
    # Mild contrast/sharpness improves small signage without changing the saved original snapshot.
    return ImageEnhance.Sharpness(ImageEnhance.Contrast(im).enhance(1.35)).enhance(1.4)

def ocr(im:Image.Image,tesseract:str,lang:str,tessdata_dir:str|None=None)->str:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.png',delete=False) as f:
        p=f.name; im.save(p)
    try:
        cmd=[tesseract,p,'stdout','-l',lang,'--psm','11']
        if tessdata_dir: cmd[1:1]=['--tessdata-dir',tessdata_dir]
        r=run(cmd)
        return re.sub(r'\s+',' ',r.stdout.decode('utf8','replace')).strip()
    finally:
        try: os.unlink(p)
        except OSError: pass

def files(inputs:list[str]):
    out=[]
    for raw in inputs:
        p=Path(raw)
        if p.is_dir(): out += [x for x in p.rglob('*') if x.suffix.lower() in VIDEO_EXT|IMAGE_EXT]
        elif p.exists(): out.append(p)
        else: raise SystemExit(f'bestaat niet: {p}')
    return sorted(set(out),key=lambda p:str(p).lower())

def scan(args):
    tess=args.tesseract or shutil.which('tesseract') or next((str(p) for p in [Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe'),Path(r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe')] if p.exists()),None)
    if not tess: raise SystemExit('Tesseract niet gevonden; installeer Tesseract of gebruik --tesseract')
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True); records=[]
    for path in files(args.inputs):
        if path.suffix.lower() not in VIDEO_EXT|IMAGE_EXT: continue
        snapdir=out/(path.stem); snapdir.mkdir(parents=True,exist_ok=True)
        for t,im in images_from(path,args.interval):
            text=ocr(prepare(im),tess,args.lang,args.tessdata_dir)
            if len(re.sub(r'[^A-Za-zÀ-ÿ0-9]','',text)) < args.min_chars: continue
            stamp=f'{t:09.3f}'.replace('.','_'); snap=snapdir/f'{path.stem}__{stamp}s.png'; im.save(snap)
            records.append({'file':str(path),'time_seconds':round(t,3),'text':text,'snapshot':str(snap)})
            print(f'{path.name}\t{t:.3f}s\t{text[:160]}')
    (out/'ocr-results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
    with (out/'ocr-results.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=['file','time_seconds','text','snapshot']); w.writeheader(); w.writerows(records)
    print(f'\n{len(records)} tekstframes opgeslagen in {out}')

def main():
    p=argparse.ArgumentParser(description=__doc__.splitlines()[0]); sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('scan'); s.add_argument('inputs',nargs='+'); s.add_argument('--interval',type=float,default=.5); s.add_argument('--out-dir',default='temp/video-ocr'); s.add_argument('--lang',default='eng'); s.add_argument('--min-chars',type=int,default=3); s.add_argument('--tesseract'); s.add_argument('--tessdata-dir'); s.set_defaults(func=scan)
    a=p.parse_args(); a.func(a)
if __name__=='__main__': main()
