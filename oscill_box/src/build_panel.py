"""Generate the panel image in output from the shared enclosure layout."""
from pathlib import Path
import html
import json
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/'src/layout.json').read_text())
CONTROLS=DATA['controls']
MM=72/25.4
BG='#14242c';FIELD='#192c35';INK='#f1f5f5';MUTED='#a9c2cd';GRID='#506772'
HINT='#6f828b'
YELLOW='#ffd166';CYAN='#64d8ef';GREEN='#73e5a4';RED='#f07178'


class Artwork:
    def __init__(self,bounds):self.bounds=bounds;self.items=[];self.holes=[]
    def rect(self,x,y,w,h,fill,stroke=None,lw=.15,r=0):self.items.append(('rect',(x,y,w,h,fill,stroke,lw,r)))
    def polygon(self,points,fill,stroke=None,lw=.15):self.items.append(('polygon',(points,fill,stroke,lw)))
    def circle(self,x,y,r,fill=None,stroke=None,lw=.15):self.items.append(('circle',(x,y,r,fill,stroke,lw)))
    def line(self,x,y,u,v,color=GRID,lw=.12):self.items.append(('line',(x,y,u,v,color,lw)))
    def text(self,s,x,y,size=1.6,color=INK,bold=False,align='middle'):
        font='Helvetica-Bold' if bold else 'Helvetica'
        width=stringWidth(s,font,size*MM)/MM
        left=x-width/2 if align=='middle' else x if align=='start' else x-width
        bx,by,bw,bh,_=self.bounds
        if left<bx+.35 or left+width>bx+bw-.35 or y<by+.35 or y+size*.8>by+bh-.2:
            raise ValueError('Text outside sticker: '+s)
        def inside(px,py):
            poly=DATA['panel_outline'];hit=False
            for (x0,y0),(x1,y1) in zip(poly,poly[1:]+poly[:1]):
                if (y0>py)!=(y1>py) and px<(x1-x0)*(py-y0)/(y1-y0)+x0:hit=not hit
            return hit
        if not all(inside(px,py) for px in (left-.15,left+width+.15) for py in (y-.15,y+size*.8+.15)):
            raise ValueError('Text crosses the angled sticker edge: '+s)
        for key,(cx,cy) in CONTROLS.items():
            if key in ['SET_CH0','SET_CH1','TIME','TRG','FUNC'] or key.startswith('BNC') or key=='LED_RUN':
                radius=DATA['parameters']['encoder_cap_diameter_mm']/2+.5 if key in ['SET_CH0','SET_CH1','TIME','TRG','FUNC'] else DATA['parameters']['bnc_opening_mm']/2+2.3 if key.startswith('BNC') else 2.5
                nearx=max(left,min(cx,left+width));neary=max(y,min(cy,y+size*.8))
                if (nearx-cx)**2+(neary-cy)**2<radius**2:
                    raise ValueError('Text touches circular control: '+s+' / '+key)
            elif key in ['EN_CH0','EN_CH1','CURSOR','TRG_MODE','MENU_BACK','RUN_STOP']:
                if left<cx+4.125 and left+width>cx-4.125 and y<cy+4.125 and y+size*.8>cy-4.125:
                    raise ValueError('Text touches button outline: '+s+' / '+key)
        self.items.append(('text',(s,x,y,size,color,bold,align)))
    def pdf(self,c,px,py):
        bx,by,_,_,_=self.bounds
        c.saveState();c.translate(px*MM,py*MM);c.scale(MM,MM);c.translate(-bx,-by)
        clip=c.beginPath();clip.moveTo(*DATA['panel_outline'][0])
        for point in DATA['panel_outline'][1:]:clip.lineTo(*point)
        clip.close();c.clipPath(clip,stroke=0,fill=0)
        for kind,a in self.items:
            if kind=='polygon':
                points,fill,stroke,lw=a;p=c.beginPath();p.moveTo(*points[0])
                for q in points[1:]:p.lineTo(*q)
                p.close()
                if fill:c.setFillColor(fill)
                if stroke:c.setStrokeColor(stroke)
                c.setLineWidth(lw);c.drawPath(p,stroke=int(bool(stroke)),fill=int(bool(fill)));continue
            if kind=='text':
                s,x,y,size,col,bold,align=a;c.setFillColor(col);c.setFont('Helvetica-Bold' if bold else 'Helvetica',size)
                getattr(c,{'middle':'drawCentredString','start':'drawString','end':'drawRightString'}[align])(x,y,s)
                continue
            if kind=='line':
                x,y,u,v,col,lw=a;c.setStrokeColor(col);c.setLineWidth(lw);c.line(x,y,u,v);continue
            if kind=='rect':
                x,y,w,h,fill,stroke,lw,r=a
                if fill:c.setFillColor(fill)
                if stroke:c.setStrokeColor(stroke)
                c.setLineWidth(lw);c.roundRect(x,y,w,h,r,stroke=int(bool(stroke)),fill=int(bool(fill)))
            else:
                x,y,r,fill,stroke,lw=a
                if fill:c.setFillColor(fill)
                if stroke:c.setStrokeColor(stroke)
                c.setLineWidth(lw);c.circle(x,y,r,stroke=int(bool(stroke)),fill=int(bool(fill)))
        c.restoreState()
    def svg(self,path):
        bx,by,w,h,_=self.bounds
        X=lambda x:x-bx
        Y=lambda y:by+h-y
        elements=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',
                  '<title>OSCILL front overlay, scale 1:1</title>']
        coords=' '.join(f'{X(x)},{Y(y)}' for x,y in DATA['panel_outline'])
        elements.extend([f'<defs><clipPath id="panel"><polygon points="{coords}"/></clipPath></defs>','<g clip-path="url(#panel)">'])
        for kind,a in self.items:
            if kind=='polygon':
                points,fill,stroke,lw=a
                coords=' '.join(f'{X(x)},{Y(y)}' for x,y in points)
                elements.append(f'<polygon points="{coords}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{lw}"/>');continue
            if kind=='text':
                s,x,y,size,col,bold,align=a
                elements.append(f'<text x="{X(x):.4f}" y="{Y(y):.4f}" font-family="Arial,Helvetica,sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" text-anchor="{align}" fill="{col}">{html.escape(s)}</text>')
            elif kind=='line':
                x,y,u,v,col,lw=a;elements.append(f'<path d="M {X(x)} {Y(y)} L {X(u)} {Y(v)}" fill="none" stroke="{col}" stroke-width="{lw}"/>')
            elif kind=='rect':
                x,y,ww,hh,fill,stroke,lw,r=a
                elements.append(f'<rect x="{X(x)}" y="{Y(y+hh)}" width="{ww}" height="{hh}" rx="{r}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{lw}"/>')
            else:
                x,y,r,fill,stroke,lw=a
                elements.append(f'<circle cx="{X(x)}" cy="{Y(y)}" r="{r}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{lw}"/>')
        elements.append('</g></svg>');path.write_text('\n'.join(elements)+'\n',encoding='utf-8')


def artworks():
    panel=Artwork(DATA['panel'])
    panel.polygon(DATA['panel_outline'],BG,GRID,.18)
    groups=[(('SET_CH0','EN_CH0','SET_CH1','EN_CH1'),44.7),
            (('TIME','CURSOR'),21.4),(('TRG','TRG_MODE'),21.4),(('FUNC','MENU_BACK'),21.4)]
    for controls,width in groups:
        centre=sum(CONTROLS[key][0] for key in controls)/len(controls)
        panel.rect(centre-width/2,-1.2,width,38.1,'#1b2e37','#3c5662',.18,r=.8)
    for i,key in enumerate(['BNC_Channel 0','BNC_Channel 1']):
        x,y=CONTROLS[key];col=[YELLOW,CYAN][i]
        radius=DATA['parameters']['bnc_opening_mm']/2
        panel.rect(x-3.3,24.5,6.6,6.5,col,r=1)
        panel.circle(x,y,radius+1.5,None,col,1.3)
        panel.circle(x,y,radius,'#ffffff','#b7c4ca',.12)
        panel.text(str(i+1),x,26.4,4.4,BG,True)
    inputs_middle=(CONTROLS['BNC_Channel 0'][0]+CONTROLS['BNC_Channel 1'][0])/2
    panel.text('Diff Input',inputs_middle,2.6,2.2,HINT)
    panel.text('1 MΩ each side',inputs_middle,-.2,2.2,HINT)
    panel.text('to GND',inputs_middle,-3,2.2,HINT)
    knobs=[('SET_CH0','CH1','SCALE / POSITION',YELLOW),('SET_CH1','CH2','SCALE / POSITION',CYAN),
           ('TIME','TIME','SCALE / POSITION',MUTED),('TRG','TRIGGER','PREVIEW / APPLY',GREEN),('FUNC','FUNC','HOLD: FINE',MUTED)]
    for key,label,hint,col in knobs:
        x,y=CONTROLS[key];panel.circle(x,y,DATA['parameters']['encoder_cap_diameter_mm']/2+.3,None,col,.4)
        panel.circle(x,y,DATA['parameters']['encoder_shaft_opening_mm']/2,'#ffffff','#b7c4ca',.1)
        panel.text(label,x,y+DATA['parameters']['encoder_cap_diameter_mm']/2+.85,2.6,INK,True)
        panel.text(hint,x,y-DATA['parameters']['encoder_cap_diameter_mm']/2-2.45,1.7,HINT)
    keys=[('EN_CH0','EN CH1','ON / MENU / 2X OFF',YELLOW),('EN_CH1','EN CH2','ON / MENU / 2X OFF',CYAN),
          ('CURSOR','CURSOR','HOLD: SETTINGS',MUTED),('TRG_MODE','TRG MODE','HOLD: SETTINGS',GREEN),
          ('MENU_BACK','MENU / BACK','HOLD: ZOOM',MUTED)]
    for key,label,hint,col in keys:
        x,y=CONTROLS[key]
        panel.rect(x-3.9,y-3.9,7.8,7.8,None,col,.45,DATA['parameters']['button_corner_radius_mm']+.4)
        panel.rect(x-3.5,y-3.5,7,7,'#ffffff','#b7c4ca',.1,DATA['parameters']['button_corner_radius_mm'])
        panel.text(label,x,12.5,2.2,INK,True)
        panel.text(hint,x,1.3,1.9,HINT)
    # Firmware chord: EN CH1 + MENU/BACK (Windows and SDL frontends).
    first=CONTROLS['EN_CH0'][0];last=CONTROLS['MENU_BACK'][0]
    middle=(first+last)/2
    for key in ['EN_CH0','MENU_BACK']:
        x,y=CONTROLS[key]
        # Start below the hint, with no mark touching the button outline.
        panel.line(x,.7,x,-3.6,HINT,.3)
    panel.line(first,-3.6,middle-15.5,-3.6,HINT,.3)
    panel.line(middle+15.5,-3.6,last,-3.6,HINT,.3)
    panel.text('SCREENSHOT + CSV',middle,-4.45,2.3,HINT,True)
    x,y=CONTROLS['LED_RUN']
    panel.circle(x,y,2.3,None,RED,.3);panel.circle(x,y,1.8,'#ffffff','#b7c4ca',.1)
    x,y=CONTROLS['RUN_STOP']
    gap_bottom=y+4.125;gap_top=CONTROLS['LED_RUN'][1]-2.45
    # Centre the capital letters in the free gap, not their baseline.
    panel.text('RUN / STOP',x,(gap_bottom+gap_top)/2-3*.718/2,3,INK,True)
    panel.rect(x-3.9,y-3.9,7.8,7.8,None,RED,.45,DATA['parameters']['button_corner_radius_mm']+.4)
    panel.rect(x-3.5,y-3.5,7,7,'#ffffff','#b7c4ca',.1,DATA['parameters']['button_corner_radius_mm'])
    panel.text('HOLD:',x,5.7,1.9,HINT);panel.text('FORCE TRIG',x,3.1,1.9,HINT)
    return panel


def main():
    panel=artworks();dest=ROOT/'output';dest.mkdir(exist_ok=True)
    (dest/'panel-decal.p2d').write_text(
        'color texture "decal_image_tex" "panel.png"\n'
        '    "swP2D" on ,\n'
        '    "useUnderlyingAppearance" off ,\n'
        '    "diffuse_factor" 1 ,\n'
        '    "specular_factor" 0 ,\n'
        '    "reflectivity" 0 ,\n'
        '    "transparency" 0 ,\n'
        '    "doubleSided" on ,\n'
        f'    "initTextureWidth" {DATA["panel"][2]/1000} ,\n'
        f'    "initTextureHeight" {DATA["panel"][3]/1000} ,\n'
        '    "masking_mode" 0 ,\n'
        '    "invert_mask" off\n',encoding='ascii')
    # Keep the panel image in output; render without intermediate documents.
    from io import BytesIO
    import pypdfium2 as pdfium
    stream=BytesIO()
    w,h=DATA['panel'][2:4]
    c=canvas.Canvas(stream,pagesize=(w*MM,h*MM),pageCompression=1)
    panel.pdf(c,0,0)
    c.showPage();c.save()
    document=pdfium.PdfDocument(stream.getvalue())
    bitmap=BytesIO()
    document[0].render(scale=6).to_pil().save(bitmap,format='PNG',dpi=(432,432))
    target=dest/'panel.png';pixels=bitmap.getvalue()
    if not target.exists() or target.read_bytes()!=pixels:target.write_bytes(pixels)
    document.close()
    print('Generated output/panel.png for the embedded SolidWorks decal')


if __name__=='__main__':main()
