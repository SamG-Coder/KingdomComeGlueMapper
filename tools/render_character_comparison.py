"""Render the audited source geometry; does not run game shaders or skinning.

Requires the optional NPC-effects dependencies (numpy and Pillow). All mesh
triangles come from the local audit dump. Colours identify asset boundaries,
not surface materials; no hiding groups, morphs or animation are applied.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from audit_character_bodies import inspect_mesh, key


BODY = (40, 167, 162)
HEAD = (234, 157, 66)
BACKGROUND = (237, 242, 246)


def font(size, bold=False):
    for name in ([r'C:/Windows/Fonts/segoeuib.ttf'] if bold else [r'C:/Windows/Fonts/segoeui.ttf']) + ['DejaVuSans.ttf']:
        try: return ImageFont.truetype(name,size)
        except OSError: pass
    return ImageFont.load_default(size=size)


def geometry(data, labels):
    triangles=[]; colours=[]
    for label, colour in labels:
        _, positions, faces=inspect_mesh(Path(data['assets'][label]['path']).read_bytes())
        v=np.asarray(positions);f=np.asarray(faces)
        tri=v[f];triangles.append(tri)
        colours.append(np.broadcast_to(np.array(colour), (len(tri),3)))
    return np.concatenate(triangles),np.concatenate(colours)


def render(triangles, colours, size, xspan, zspan):
    """Orthographic front elevation, +Y towards camera; opaque triangle draw."""
    w,h=size
    scale=min(w/(xspan[1]-xspan[0]),h/(zspan[1]-zspan[0]))
    xcentre=sum(xspan)/2;zcentre=sum(zspan)/2
    image=Image.new('RGB',(w,h),BACKGROUND);draw=ImageDraw.Draw(image)
    for z in np.arange(.0,1.81,.1):
        y=h/2-(z-zcentre)*scale
        if 0<=y<h: draw.line((0,y,w,y),fill=(217,226,232))
    normal=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    normal/=np.maximum(np.linalg.norm(normal,axis=1,keepdims=True),1e-12)
    light=np.array([-.35,.7,.62]);light/=np.linalg.norm(light)
    intensity=.47+.53*np.maximum(0,np.abs(normal@light))
    shade=np.clip(colours*intensity[:,None],0,255).astype('uint8')
    projected=np.stack([w/2+(triangles[:,:,0]-xcentre)*scale,
                        h/2-(triangles[:,:,2]-zcentre)*scale],axis=-1)
    for index in np.argsort(triangles[:,:,1].mean(axis=1)):
        p=projected[index]
        if p[:,0].max()<0 or p[:,0].min()>w or p[:,1].max()<0 or p[:,1].min()>h:continue
        draw.polygon([tuple(x) for x in p],fill=tuple(shade[index]))
    return image


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit',type=Path)
    args=parser.parse_args()
    data=json.loads((args.audit/'comparison.json').read_text())
    selections=[]
    for sex in ('male','female'):
        parts=data['components']['KDC1_'+sex]['parts']
        old=[(p['asset'],BODY if p['kind']=='body' else HEAD) for p in parts if p['kind'] in ('head','body')]
        body=data['components']['KDC2_'+('male_body_npc' if sex=='male' else 'female_body')]
        head=data['components']['KDC2_'+('m_head_father' if sex=='male' else 'f_head_mother')]
        native=[('KDC2/'+key(e['ModelResolved']),colour)
                for c,colour in ((body,BODY),(head,HEAD)) for e in c['elements'] if e.get('ModelResolved')]
        selections.extend([('KDC1 '+sex,old),('KDC2 '+sex,native)])
    factor=2;col=460;w=4*col;h=1150
    output=Image.new('RGB',(w*factor,h*factor),(250,252,254))
    draw=ImageDraw.Draw(output)
    def text(x,y,s,size=20,bold=False,colour=(28,45,61)):
        draw.text((x*factor,y*factor),s,font=font(size*factor,bold),fill=colour)
    text(28,16,'Original character geometry: body and head boundaries',30,True)
    text(28,61,'Body = teal   |   Head = amber   |   Exact parent head assets from both games',19)
    text(28,90,'Authored coordinates. No animation, morphs, materials or hiding masks applied. These are mesh renders, not game screenshots.',17)
    for i,(title,labels) in enumerate(selections):
        x=i*col+12
        text(x+8,128,title,24,True)
        tris,colours=geometry(data,labels)
        output.paste(render(tris,colours,((col-24)*factor,530*factor),(-.72,.72),(-.03,1.81)),(x*factor,170*factor))
        text(x+8,713,'Neck / shoulder join (same scale)',17,True)
        output.paste(render(tris,colours,((col-24)*factor,330*factor),(-.31,.31),(1.16,1.66)),(x*factor,750*factor))
    text(28,1097,'KDC1 female base body is a set of exposed limbs; clothing supplies the covered torso. Native KDC2 female body has a fuller torso.',17)
    text(28,1122,'The lower amber edge belongs to the head asset. KDC1 and KDC2 place this boundary at different heights.',17)
    output=output.resize((w,h),Image.Resampling.LANCZOS)
    path=args.audit/'body-head-comparison.png';output.save(path)
    print(path)


if __name__=='__main__':main()
