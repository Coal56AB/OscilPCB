"""Build native-SolidWorks feature plans and verify their solid geometry.

Requires altium-monkey==2026.9.22 and cadquery-ocp. No installed Altium is needed.
The charger snapshot is a local STEP after its first explicit import.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import tempfile
from pathlib import Path
from hashlib import sha256

from altium_monkey import AltiumPcbDoc, AltiumPcbLib
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeEdge
from OCP.GC import GC_MakeArcOfCircle
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt, gp_Vec, gp_Trsf

ROOT=Path(__file__).resolve().parents[1]
PCB=ROOT.parent
import sys
sys.path.insert(0,str(PCB/'Lib'))
from _tactile_stack import add_tactile_stack, RELEASED_MM, PRESSED_MM
from _build_mechanical_models import box, cylinder, tube, compound, read_solids, bbox


def bbox(shape):
    b=Bnd_Box();BRepBndLib.AddOptimal_s(shape,b,False,False)
    return (b.GetXMin(),b.GetYMin(),b.GetZMin(),b.GetXMax(),b.GetYMax(),b.GetZMax())

U=.00000254
PARAM=json.loads((ROOT/'src/parameters.json').read_text())
BNC_STUD_SPAN_MM=11.07
BNC_STUD_DIAMETER_MM=2.06
BLUE=(25,79,111)
BLACK=(30,35,39)
METAL=(181,188,193)
FEATURES={}


def write_model(path,parts):
    from io import BytesIO
    from OCP.BRepTools import BRepTools
    from OCP.TopTools import TopTools_FormatVersion
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorGen
    from OCP.IFSelect import IFSelect_RetDone
    doc=TDocStd_Document(TCollection_ExtendedString('XmlXCAF'))
    shapes=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors=XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    fingerprint=sha256()
    for s,c in parts:
        # XCAF STEP presentation ordering and occurrence IDs are not stable.
        # Hash exact BRep geometry (without meshing) and its assigned colour.
        stream=BytesIO()
        BRepTools.Write_s(s,stream,False,False,TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3)
        raw=stream.getvalue()
        fingerprint.update(len(raw).to_bytes(8,'little'));fingerprint.update(raw)
        fingerprint.update(json.dumps(c).encode())
        label=shapes.AddShape(s)
        colors.SetColor(label,Quantity_Color(*(v/255 for v in c),Quantity_TOC_RGB),XCAFDoc_ColorGen)
    writer=STEPCAFControl_Writer();writer.SetColorMode(True);writer.Transfer(doc,STEPControl_AsIs)
    if writer.Write(str(path))!=IFSelect_RetDone:raise ValueError('STEP write failed '+str(path))
    if not read_solids(path):raise ValueError('Empty STEP '+str(path))
    if path.parent==ROOT/'src' and path.stem.endswith('_reference'):
        path.with_suffix('.fingerprint').write_text(fingerprint.hexdigest()+'\n')


def volume(shape):
    p=GProp_GProps(); BRepGProp.VolumeProperties_s(shape,p,1e-8)
    return p.Mass()


def transform(shape,matrix):
    t=gp_Trsf();t.SetValues(*matrix)
    return BRepBuilderAPI_Transform(shape,t,True).Shape()


def polygon(points,z0,z1):
    wire=BRepBuilderAPI_MakePolygon()
    for x,y in points:wire.Add(gp_Pnt(x,y,z0))
    wire.Close()
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(),gp_Vec(0,0,z1-z0)).Shape()


def rr(x,y,w,h,r,z0,z1):
    if not r:return box(x,y,z0,x+w,y+h,z1)
    shape=box(x+r,y,z0,x+w-r,y+h,z1)
    shape=BRepAlgoAPI_Fuse(shape,box(x,y+r,z0,x+w,y+h-r,z1)).Shape()
    for a,b in [(x+r,y+r),(x+w-r,y+r),(x+r,y+h-r),(x+w-r,y+h-r)]:
        shape=BRepAlgoAPI_Fuse(shape,cylinder(a,b,z0,z1,r)).Shape()
    return shape


def encode(*args):return ','.join(str(a) for a in args)
def R(x,y,w,h,r=0):return encode('R',x,y,w,h,r)
def C(x,y,r):return encode('C',x,y,r)
def P(points):return encode('P',*[v for p in points for v in p])
def UPPER(x,y,w,h,r):return encode('U',x,y,w,h,r)
def chamfer_rect(y,z,w,h,c):
    return P([(y+c,z),(y+w-c,z),(y+w,z+c),(y+w,z+h-c),
              (y+w-c,z+h),(y+c,z+h),(y,z+h-c),(y,z+c)])


def rounded_points(x,y,w,h,r,n=24):
    points=[]
    for cx,cy,start in [(x+w-r,y+r,-90),(x+w-r,y+h-r,0),
                        (x+r,y+h-r,90),(x+r,y+r,180)]:
        for i in range(n+1):
            a=math.radians(start+90*i/n)
            points.append((cx+r*math.cos(a),cy+r*math.sin(a)))
    return points


def rounded_panel(points,radius):
    """One analytic arc per corner, plus a sampled outline for the artwork."""
    corners=[];outline=[]
    for i,p in enumerate(points):
        prev=points[i-1];nxt=points[(i+1)%len(points)]
        a=[p[k]-prev[k] for k in (0,1)];b=[nxt[k]-p[k] for k in (0,1)]
        la=math.hypot(*a);lb=math.hypot(*b);a=[v/la for v in a];b=[v/lb for v in b]
        turn=math.atan2(a[0]*b[1]-a[1]*b[0],sum(u*v for u,v in zip(a,b)))
        tangent=radius*math.tan(abs(turn)/2)
        assert tangent<min(la,lb)/2,'panel radius exceeds available edge length'
        start=[p[k]-a[k]*tangent for k in (0,1)]
        end=[p[k]+b[k]*tangent for k in (0,1)]
        side=math.copysign(1,turn)
        center=[start[0]-a[1]*radius*side,start[1]+a[0]*radius*side]
        angle=math.atan2(start[1]-center[1],start[0]-center[0])
        def at(f):return [center[0]+radius*math.cos(angle+turn*f),center[1]+radius*math.sin(angle+turn*f)]
        corners.extend(start+at(.5)+end)
        count=max(2,math.ceil(abs(turn)/math.radians(3)))
        outline.extend(at(j/count) for j in range(count+1))
    return encode('F',*corners),outline


def loop_segments(loop):
    a=loop.split(',');v=list(map(float,a[1:]))
    if a[0]=='F':
        segments=[]
        for i in range(0,len(v),6):
            segments.extend([('A',v[i:i+6]),('L',v[i+4:i+6]+v[(i+6)%len(v):(i+6)%len(v)+2])])
        return segments
    if a[0]=='P':
        pts=list(zip(v[::2],v[1::2]));return [('L',list(p+pts[(i+1)%len(pts)])) for i,p in enumerate(pts)]
    x,y,w,h,r=v
    if not r:return loop_segments(P([(x,y),(x+w,y),(x+w,y+h),(x,y+h)]))
    k=math.sqrt(.5)
    return [('L',[x+r,y,x+w-r,y]),('A',[x+w-r,y,x+w-r+r*k,y+r-r*k,x+w,y+r]),
            ('L',[x+w,y+r,x+w,y+h-r]),('A',[x+w,y+h-r,x+w-r+r*k,y+h-r+r*k,x+w-r,y+h]),
            ('L',[x+w-r,y+h,x+r,y+h]),('A',[x+r,y+h,x+r-r*k,y+h-r+r*k,x,y+h-r]),
            ('L',[x,y+h-r,x,y+r]),('A',[x,y+r,x+r-r*k,y+r-r*k,x+r,y])]


def section_wire(loop,level=0,axis_x=False):
    wire=BRepBuilderAPI_MakeWire()
    def pt(u,v):return gp_Pnt(level,u,v) if axis_x else gp_Pnt(u,v,level)
    for kind,v in loop_segments(loop):
        if kind=='L':edge=BRepBuilderAPI_MakeEdge(pt(*v[:2]),pt(*v[2:]))
        else:edge=BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(pt(*v[:2]),pt(*v[2:4]),pt(*v[4:])).Value())
        wire.Add(edge.Edge())
    return wire.Wire()


def stand_matrix(angle):
    a=math.radians(angle);c=math.cos(a);s=math.sin(a);y=PARAM['stand_hinge_y_mm'];z=PARAM['stand_hinge_z_mm']
    return [1,0,0,0,0,c,-s,y*(1-c)+s*z,0,s,c,z*(1-c)-s*y]


def arc_slot(cy,cz):
    half=PARAM['stand_open_degrees']/2
    a,b=map(math.radians,(-half,half));r=5;w=2.2;n=24;cap=12
    points=[]
    for i in range(n+1):
        t=a+(b-a)*i/n;points.append((cy-(r+w)*math.cos(t),cz+(r+w)*math.sin(t)))
    ey,ez=cy-r*math.cos(b),cz+r*math.sin(b)
    for i in range(1,cap+1):
        t=math.pi-b-math.pi*i/cap;points.append((ey+w*math.cos(t),ez+w*math.sin(t)))
    for i in range(1,n+1):
        t=b-(b-a)*i/n;points.append((cy-(r-w)*math.cos(t),cz+(r-w)*math.sin(t)))
    ey,ez=cy-r*math.cos(a),cz+r*math.sin(a)
    for i in range(1,cap):
        t=-a-math.pi*i/cap;points.append((ey+w*math.cos(t),ez+w*math.sin(t)))
    return P(points)


def rear_levels():
    inner=-1.6-PARAM['rear_standoff_mm']
    glass_top=inner-PARAM['rear_mount_web_mm']
    glass_bottom=glass_top-2.0
    return inner,glass_bottom-.1,glass_bottom,glass_top


def build_stand():
    cy,cz=PARAM['stand_hinge_y_mm'],PARAM['stand_hinge_z_mm']
    extra=PARAM['lower_extension_mm']
    _,rear_outer,_,_=rear_levels()
    front=rear_outer-.4;back=front-5.6;neck_back=front-2.8
    # Broad U frame lies behind the rear face. Only the short pivot necks
    # enter the existing sidewalls; there are no external hinge brackets.
    feature('kickstand','ADD','Left_leg',back,front,R(-2.6,-2.6-extra,12.8,cy+2.6+extra,2))
    feature('kickstand','ADD','Foot_crossbar',back,front,R(-2.6,-5-extra,195.2,12.8,2))
    feature('kickstand','ADD','Right_leg',back,front,R(179.8,-2.6-extra,12.8,cy+2.6+extra,2))
    half=math.radians(PARAM['stand_open_degrees']/2)
    py=cy-5*math.cos(half);pz=cz-5*math.sin(half)
    for i,(sx0,sx1,px0,px1) in enumerate([(-2.6,3.8,-3.5,-.9),(186.2,192.6,190.9,193.5)]):
        feature('rear','CUT','Internal_hinge_clearance_'+str(i),rear_outer-.1,-1.9,
                R(-3 if i==0 else 185.8,cy-10.8,7.2,17.4))
        feature('kickstand','ADD_X','Pivot_neck_'+str(i),sx0,sx1,
                P([(cy-9.6,neck_back),(cy,neck_back),(cy,cz),(cy-2.2,-2),(cy-9.6,-2)]))
        feature('kickstand','ADD_X','Pivot_lug_'+str(i),sx0,sx1,C(cy,cz,4.3))
        # Integral axles drop into blind U bearings before the case is closed.
        feature('kickstand','ADD_X','Captive_axle_'+str(i),-4.3 if i==0 else 192.3,
                -2.3 if i==0 else 194.3,C(cy,cz,2.5))
        feature('kickstand','CUT_X','Blind_rotation_groove_'+str(i),
                sx0-.1 if i==0 else sx1-2,sx0+2 if i==0 else sx1+.1,arc_slot(cy,cz))
        feature('rear','ADD_X','Internal_stop_pin_'+str(i),px0,px1,C(py,pz,1.8))
        feature('rear','CUT_X','Blind_axle_bearing_'+str(i),-4.6 if i==0 else 192.9,
                -2.9 if i==0 else 194.6,C(cy,cz,2.8))
        feature('rear','CUT_X','Axle_assembly_entry_'+str(i),-4.6 if i==0 else 192.9,
                -2.9 if i==0 else 194.6,P([(cy-2.8,cz),(cy+2.8,cz),(cy+2.8,.1),(cy-2.8,.1)]))
        feature('rear','CUT','Keeper_seat_'+str(i),-3.6,.1,
                R(-4.8 if i==0 else 192.7,cy-3.5,2.1,7))
        feature('front','ADD','Axle_keeper_'+str(i),-3.4,-1.9,
                R(-4.6 if i==0 else 192.9,cy-3.3,1.7,6.6))


def shape_of(loop,z0,z1):
    a=loop.split(',');v=list(map(float,a[1:]))
    if a[0]=='F':
        return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(section_wire(loop,z0)).Face(),gp_Vec(0,0,z1-z0)).Shape()
    if a[0]=='R':return rr(*v,z0,z1)
    if a[0]=='C':return cylinder(*v[:2],z0,z1,v[2])
    if a[0]=='U':
        x,y,w,h,r=v
        shape=box(x,y,z0,x+w,y+h-r,z1)
        shape=BRepAlgoAPI_Fuse(shape,box(x+r,y+h-r,z0,x+w-r,y+h,z1)).Shape()
        for cx in (x+r,x+w-r):
            shape=BRepAlgoAPI_Fuse(shape,cylinder(cx,y+h-r,z0,z1,r)).Shape()
        return shape
    return polygon(list(zip(v[::2],v[1::2])),z0,z1)


def feature(part,kind,name,z0,z1,*loops):
    FEATURES.setdefault(part,[]).append((kind,name,z0,z1,loops))


def feature_tool(kind,loops,lo,hi):
    if kind.endswith('_LOFT'):
        loft=BRepOffsetAPI_ThruSections(True,True)
        for x,loop in zip((lo,hi),loops):
            loft.AddWire(section_wire(loop,x,'_X' in kind))
        loft.Build();return loft.Shape()
    if kind in ('CUT_X_TAPER','CUT_Z_TAPER'):
        vals=list(map(float,loops[0].split(',')[1:]));pts=list(zip(vals[::2],vals[1::2]))
        cy=sum(p[0] for p in pts)/4;cz=sum(p[1] for p in pts)/4
        loft=BRepOffsetAPI_ThruSections(True,True)
        slope=2/2.8 if kind=='CUT_X_TAPER' else 2
        for x,d in [(lo,0),(hi,(hi-lo)*slope)]:
            w=BRepBuilderAPI_MakePolygon()
            for y,z in pts:
                u=y+math.copysign(d,y-cy);v=z+math.copysign(d,z-cz)
                w.Add(gp_Pnt(x,u,v) if kind=='CUT_X_TAPER' else gp_Pnt(u,v,x))
            w.Close();loft.AddWire(w.Wire())
        loft.Build();return loft.Shape()
    shapes=[shape_of(loop,lo,hi) for loop in loops]
    if '_X' in kind:shapes=[transform(s,[0,0,1,0,1,0,0,0,0,1,0,0]) for s in shapes]
    tool=shapes[0]
    for q in shapes[1:]:tool=(BRepAlgoAPI_Cut if kind.startswith('ADD') or kind=='CUT_RING' else BRepAlgoAPI_Fuse)(tool,q).Shape()
    return tool


def model_matrix(body,origin):
    theta=math.radians(body.model_3d_rotz)
    cs,sn=math.cos(theta),math.sin(theta)
    ox=body.model_2d_x*U-origin[0];oy=body.model_2d_y*U-origin[1]
    z=body.model_3d_dz*U
    if int(body.body_projection)==1:
        return [cs,-sn,0,ox,-sn,-cs,0,oy,0,0,-1,-1.6-z]
    rx=math.radians(body.model_3d_rotx);ry=math.radians(body.model_3d_roty)
    cx,sx,cy,sy=math.cos(rx),math.sin(rx),math.cos(ry),math.sin(ry)
    return [cs*cy,cs*sy*sx-sn*cx,cs*sy*cx+sn*sx,ox,
            sn*cy,sn*sy*sx+cs*cx,sn*sy*cx-cs*sx,oy,-sy,cy*sx,cy*cx,z]


def display_layout(board):
    body=next(t for t in board.component_bodies if t.model_name.startswith('LCD_7inch'))
    path=PCB/'Lib'/body.model_name
    solids=read_solids(path)
    matrix=model_matrix(body,(2.25,6.09))
    mounts=[];errors=[]
    holes=[(p.x*U-2.25,p.y*U-6.09) for p in board.pads if not p.is_plated and p.hole_size*U>2.8]
    for s in solids:
        q=bbox(s)
        if abs(q[3]-q[0]-6)<.01 and abs(q[4]-q[1]-6)<.01 and abs(q[2]+19)<.01 and abs(q[5])<.01:
            q=bbox(transform(s,matrix));center=((q[0]+q[3])/2,(q[1]+q[4])/2)
            nearest=min(holes,key=lambda h:math.dist(h,center));error=math.dist(nearest,center)
            if error<.2:mounts.append([round(v,4) for v in center]);errors.append(error)
    if len(mounts)!=3:raise ValueError('Expected three supported LCD mounts; save the corrected PcbDoc first')
    glass=[bbox(transform(s,matrix)) for s in solids if
           bbox(s)[3]-bbox(s)[0]>160 and abs(bbox(s)[5]-8.88)<.01]
    active=[bbox(transform(s,matrix)) for s in solids if
            150<bbox(s)[3]-bbox(s)[0]<160 and abs(bbox(s)[5]-8.88)<.01]
    return dict(model=body.model_name,origin_mm=[matrix[3],matrix[7],matrix[11]],
                mounts_mm=mounts,mount_error_mm=max(errors),glass_mm=glass[0],active_mm=active[0])


def update_controls():
    lib=AltiumPcbLib(PCB/'Lib/Oscill.PcbLib')
    fp=next(f for f in lib.footprints if f.name=='TACT_SMD_ON_DIP8_RAISED')
    scratch=AltiumPcbLib()
    template=scratch.add_footprint('button-stack')
    add_tactile_stack(template)
    old_ids={id(t) for t in fp.component_bodies}
    fp.component_bodies[:]=copy.deepcopy(template.component_bodies)
    fp._record_order[:]=[t for t in fp._record_order if id(t) not in old_ids]+fp.component_bodies
    fp.parameters['HEIGHT']=f'{RELEASED_MM*1000/25.4:.4f}mil'
    fp.parameters['DESCRIPTION']='SMD switch on SOIC/DIP8 adapter in turned-pin socket'
    enc=next(f for f in lib.footprints if f.name=='EC11E_ENCODER_SW')
    heights={'EC11E shaft hub':7.5,'EC11E shaft':21.0}
    for body in enc.component_bodies:
        if body.name in heights:body.overall_height=round(heights[body.name]/U)
    bnc=next(f for f in lib.footprints if f.name=='BNC_VERTICAL_THT_5PIN')
    bnc.parameters['HEIGHT']=f'{16*1000/25.4:.4f}mil'
    for body in bnc.component_bodies:
        if body.name=='BNC barrel':body.overall_height=round(16/U)
    lib.save(PCB/'Lib/Oscill.PcbLib')
    b=AltiumPcbDoc.from_file(PCB/'AnalExtend/AnalExtend.PcbDoc')
    indices=[i for i,c in enumerate(b.components) if c.footprint==fp.name]
    b.component_bodies[:]=[t for t in b.component_bodies if t.component_index not in indices]
    for i in indices:
        c=b.components[i];theta=math.radians(float(c.rotation))
        x=float(c.x.replace('mil',''))*10000;y=float(c.y.replace('mil',''))*10000
        for body in fp.component_bodies:
            t=copy.deepcopy(body);t.component_index=i
            for v in t.outline:
                vx,vy=v.x,v.y
                v.x=round(x+vx*math.cos(theta)-vy*math.sin(theta))
                v.y=round(y+vx*math.sin(theta)+vy*math.cos(theta))
                v.center_x=v.x;v.center_y=v.y
            b.component_bodies.append(t)
        c.raw_record['HEIGHT']=fp.parameters['HEIGHT']
    enc_indices={i for i,c in enumerate(b.components) if c.footprint=='EC11E_ENCODER_SW'}
    for body in b.component_bodies:
        if body.component_index in enc_indices and body.name in heights:
            body.overall_height=round(heights[body.name]/U)
    bnc_indices={i for i,c in enumerate(b.components) if c.footprint==bnc.name}
    for body in b.component_bodies:
        if body.component_index in bnc_indices and body.name=='BNC barrel':body.overall_height=round(16/U)
    for i in bnc_indices:b.components[i].raw_record['HEIGHT']=bnc.parameters['HEIGHT']
    b.save(PCB/'AnalExtend/AnalExtend.PcbDoc')
    print('Updated',len(indices),'button stacks,',len(enc_indices),'encoders and',len(bnc_indices),'BNC 3D models')


def board_geometry(board,extract_dir,origin=(0,0)):
    """Mechanical reference from stored PCB outline and 3D bodies, without copper."""
    mm=lambda s:float(s.replace('mil',''))*.0254
    rec=board.board.raw_record
    points=[];i=0
    while 'VX'+str(i) in rec:
        points.append((mm(rec['VX'+str(i)])-origin[0],mm(rec['VY'+str(i)])-origin[1]));i+=1
    solid=polygon(points,-1.6,0)
    for vertices in board._collect_board_outline_cutouts():
        pts=[(v.x_mils*.0254-origin[0],v.y_mils*.0254-origin[1]) for v in vertices]
        if any(v.is_arc for v in vertices):raise ValueError('Curved board cutout needs an analytic contour')
        solid=BRepAlgoAPI_Cut(solid,polygon(pts,-1.7,.1)).Shape()
    for p in board.pads:
        if not p.is_plated and p.hole_size>200000:
            solid=BRepAlgoAPI_Cut(solid,cylinder(p.x*U-origin[0],p.y*U-origin[1],-1.7,.1,p.hole_size*U/2)).Shape()
    result=[(solid,BLUE)]
    files=board.extract_embedded_models(extract_dir)
    files_by_name={p.name.split('__',1)[1]:p for p in files}
    cached={}
    for body in board.component_bodies:
        if body.model_name:
            path=files_by_name.get(body.model_name)
            # The checked-in module STEP is the project's mechanical source.
            if (PCB/'Lib'/body.model_name).exists():path=PCB/'Lib'/body.model_name
            if not path:raise ValueError('Missing embedded model '+body.model_name)
            if path not in cached:cached[path]=read_solids(path)
            matrix=model_matrix(body,origin)
            color=METAL if body.model_name.startswith('613') or 'USB' in body.model_name.upper() else BLACK
            solids=cached[path]
            # Tiny SMD STEP models dominate file size; their conservative envelopes
            # suffice in the mechanical reference. The PCB retains original models.
            q=bbox(compound(solids))
            if max(q[3]-q[0],q[4]-q[1])<12 and q[5]-q[2]<4 and 'MSK' not in body.model_name:
                solids=[box(*q)]
            for s in solids:
                transformed=transform(s,matrix)
                part_color=color
                if body.model_name.startswith('LCD_7inch'):
                    q=bbox(s)
                    if abs(q[3]-q[0]-154.21)<.01 and abs(q[4]-q[1]-85.92)<.01:
                        part_color=(0,205,225)
                    if abs(q[3]-q[0]-6)<.01 and abs(q[4]-q[1]-6)<.01 and abs(q[2]+19)<.01:
                        q=bbox(transformed);center=((q[0]+q[3])/2,(q[1]+q[4])/2)
                        holes=[(p.x*U-origin[0],p.y*U-origin[1]) for p in board.pads if not p.is_plated and p.hole_size*U>2.8]
                        # Owner chose the three existing holes. Do not add a
                        # fourth PCB hole or move their corrected display pose.
                        if min(math.dist(center,h) for h in holes)>.2:continue
                result.append((transformed,part_color))
        elif body.outline:
            pts=[(v.x*U-origin[0],v.y*U-origin[1]) for v in body.outline]
            z0,z1=body.standoff_height*U,body.overall_height*U
            if z1-z0<.001:continue
            if int(body.body_projection)==1:z0,z1=-1.6-z1,-1.6-z0
            color=body.body_color_3d
            result.append((polygon(pts,z0,z1),tuple((color>>i)&255 for i in (0,8,16))))
            if body.name=='BNC barrel':
                # Typical BNC jack interface; the actual connector is unmeasured.
                # The owner's studs are vertical in the front panel view.
                c=board.components[body.component_index];a=math.radians(float(c.rotation))+math.pi/2
                cx=mm(c.x)-origin[0];cy=mm(c.y)-origin[1]
                matrix=[0,-math.sin(a),math.cos(a),cx,0,math.cos(a),math.sin(a),cy,
                        -1,0,0,z1-5.2]
                for lo,hi in ((-BNC_STUD_SPAN_MM/2,-4.5),(4.5,BNC_STUD_SPAN_MM/2)):
                    result.append((transform(cylinder(0,0,lo,hi,BNC_STUD_DIAMETER_MM/2),matrix),METAL))
    return result


def import_charger(source):
    source=Path(source)
    b=AltiumPcbDoc.from_file(source)
    with tempfile.TemporaryDirectory() as td:
        parts=board_geometry(b,Path(td),origin=(27,25))
    write_model(PCB/'Lib/J5019_HW357_Mechanical.step',parts)
    print('Imported charger snapshot from',source)


def layout_and_plans(board):
    FEATURES.clear()
    panel_top=PARAM['panel_top_mm']
    panel_bottom=panel_top-PARAM['control_panel_thickness_mm']
    film_bottom=panel_top-PARAM['film_recess_mm']
    film_top=film_bottom+.15
    rear_inner,rear_outer,glass_bottom,glass_top=rear_levels()
    gap=PARAM['pcb_edge_clearance_mm'];wall=PARAM['wall_mm']
    extra=PARAM['lower_extension_mm']
    ox=-gap-wall;oy=ox-extra;ow=190+2*(gap+wall);oh=160+2*(gap+wall)+extra
    outer=R(ox,oy,ow,oh,gap+wall)
    inner=R(-gap,-gap-extra,190+2*gap,160+2*gap+extra,gap)
    display=display_layout(board)
    active=display['active_mm']
    # The inner bevel terminates exactly at the active-area border. The outer
    # contour is independently centred on the case, preserving equal side fields.
    screen_x,screen_y=active[:2]
    screen_width=active[3]-active[0]
    screen_height=active[4]-active[1]
    screen_bevel=2*PARAM['panel_thickness_mm']
    screen_outer=[95-screen_width/2-screen_bevel,screen_y-screen_bevel,
                  screen_width+2*screen_bevel,screen_height+2*screen_bevel]
    bezel_bottom=PARAM['display_measured_top_mm']+PARAM['display_front_clearance_mm']
    bezel_top=bezel_bottom+PARAM['panel_thickness_mm']
    wall_front=[(-3.1,34.2),(55.4,34.2),(65.4,37.3),(161.8,37.3),(171.8,34.2),(193.1,34.2)]
    film_outline=[(-2.6,-2.6-extra),(192.6,-2.6-extra),(192.6,33.8),(171.8,33.8),
                  (161.8,36.9),(65.4,36.9),(55.4,33.8),(-2.6,33.8)]
    film_profile,film_outline=rounded_panel(film_outline,5)
    zynq=next(t for t in board.component_bodies if t.model_name.startswith('Zynq020'))
    ports=[]
    for s in read_solids(PCB/'Lib'/zynq.model_name):
        q=bbox(s)
        if abs(q[3]-q[0]-21)<.01 and abs(q[4]-q[1]-16.2)<.01:
            ports.append(bbox(transform(s,model_matrix(zynq,(2.25,6.09)))))
    if len(ports)!=2:raise ValueError('Cannot identify the two top connector envelopes')
    access=[min(p[0] for p in ports)-3,153.5,min(p[2] for p in ports)-3,
            max(p[3] for p in ports)+3,oy+oh+1,max(p[5] for p in ports)+3]
    controls={}
    for c in board.components:
        if c.footprint in ['EC11E_ENCODER_SW','TACT_SMD_ON_DIP8_RAISED','BNC_VERTICAL_THT_5PIN','LED_3MM_GREEN']:
            controls[c.designator]=[round(float(c.x.replace('mil',''))*.0254-2.25,4),round(float(c.y.replace('mil',''))*.0254-6.09,4)]
    # The optical opening is centred above RUN/STOP. The LED stays on its PCB
    # datum below the deck; its body does not pass through the panel opening.
    led_pcb_center=controls['LED_RUN'].copy()
    controls['LED_RUN'][0]=controls['RUN_STOP'][0]
    data={'controls':controls,'parameters':dict(PARAM,button_released_mm=RELEASED_MM,button_pressed_mm=PRESSED_MM),
          'led_pcb_center_mm':led_pcb_center,
          'panel':[-2.6,-2.6-extra,195.2,39.5+extra,1], 'panel_outline':film_outline,
          'panel_corner_radius_mm':5,
          'panel_inner_z_mm':panel_bottom,'film_z_mm':[film_bottom,film_top],
          'encoder_cap_bottom_mm':panel_top+.8,
          'screen_window':[screen_x,screen_y,screen_width,screen_height,0],
          'screen_bevel_width_mm':2*PARAM['panel_thickness_mm'],
          'screen_outer_window':screen_outer,
          'lcd':display,'top_ports_mm':ports,'top_access_mm':access,
          'case_bounds':[ox,oy,rear_outer,ox+ow,oy+oh,bezel_top],
          'encoder_wall_front':wall_front,
          'encoder_wall_y_mm':37.3,
          'power_cap_origin_mm':[189.9,95.02,-2.25],
          'stand_open_degrees':PARAM['stand_open_degrees'],
          'stand_hinge_yz_mm':[PARAM['stand_hinge_y_mm'],PARAM['stand_hinge_z_mm']],
          'pcb_sha256':sha256((PCB/'AnalExtend/AnalExtend.PcbDoc').read_bytes()).hexdigest(),
          'board_origin_altium':[2.25,6.09]}
    (ROOT/'src/layout.json').write_text(json.dumps(data,indent=2)+'\n')
    pose=[0,1,0,0,0,1,1,0,0,*data['power_cap_origin_mm'],1,0,0,0]
    (ROOT/'src/power_cap.pose').write_text(','.join(map(str,pose))+'\n')
    sm=stand_matrix(data['stand_open_degrees'])
    pose=[sm[0],sm[4],sm[8],sm[1],sm[5],sm[9],sm[2],sm[6],sm[10],sm[3],sm[7],sm[11],1,0,0,0]
    (ROOT/'src/kickstand.pose').write_text(','.join(map(str,pose))+'\n')
    for part in ['front','rear']:
        # Common rounded wall profile. Four shared PCB mounts align the halves.
        feature(part,'ADD','Perimeter_shell',-2 if part=='front' else rear_inner,
                panel_top if part=='front' else -2,
                outer,inner)
    feature('front','ADD','Control_panel',panel_bottom,panel_top,R(-3.1,-3.1-extra,196.2,39.3+extra,3))
    feature('front','ADD','Upper_side_walls',panel_top,bezel_bottom,
            outer,inner)
    feature('front','CUT','Lower_wall_height',panel_top,bezel_bottom+.1,R(ox-1,oy-1,ow+2,34.2-(oy-1)))
    feature('front','ADD','Display_bezel',bezel_bottom,bezel_top,UPPER(ox,34.2,ow,oy+oh-34.2,gap+wall))
    # The end walls turn in front of the LCD mounting ear. No mounting hole
    # crosses the control deck, and no thin rectangular cheek is exposed.
    relief=[(55.4,33.8),(171.8,33.8),*reversed(wall_front[1:5])]
    feature('front','CUT','Encoder_finger_relief',panel_top,bezel_top+.1,P(relief))
    feature('front','ADD','Flat_control_deck',panel_bottom,panel_top,
            P([(55.4,34.2),(171.8,34.2),(161.8,37.3),(65.4,37.3)]))
    for i in range(5):
        a,b=wall_front[i:i+2]
        feature('front','ADD','Continuous_encoder_wall_'+str(i),panel_top-.1,bezel_top,
                P([a,b,(b[0],b[1]+2),(a[0],a[1]+2)]))
    # The actual PCB edge between its ears is Y=45.09, beyond this wall.
    # A rectangular bounding-box cut would create an unnecessary visible slot.
    sx,sy,sw,sh,_=data['screen_window']
    ex,ey,ew,eh=screen_outer
    feature('front','CUT_Z_LOFT','Screen_edge_bevel',bezel_bottom,bezel_top,
            P([(sx,sy),(sx+sw,sy),(sx+sw,sy+sh),(sx,sy+sh)]),
            P([(ex,ey),(ex+ew,ey),(ex+ew,ey+eh),(ex,ey+eh)]))
    # Local underside ribs occupy the empty lanes between controls. The panel
    # itself remains 13.5 mm above the PCB; ribs project lower only here.
    rib_width=PARAM['control_rib_width_mm'];rib_bottom=PARAM['control_rib_bottom_mm']
    columns=['SET_CH0','SET_CH1','TIME','TRG','FUNC','RUN_STOP']
    for i,(left,right) in enumerate(zip(columns,columns[1:])):
        x=(controls[left][0]+controls[right][0])/2
        wall_y=37.3 if x<=161.8 else max(34.2,37.3-(x-161.8)*3.1/10)
        y=-gap-extra-.1
        feature('front','ADD','Control_rib_vertical_'+str(i),rib_bottom,panel_bottom+.1,
                R(x-rib_width/2,y,rib_width,wall_y+.2-y,.8))
    feature('front','ADD','Control_rib_crossbar',PARAM['control_crossbar_bottom_mm'],panel_bottom+.1,
            R(52.2,18-rib_width/2,122.8,rib_width,.8))
    for name,(x,y) in controls.items():
        if name in ['SET_CH0','SET_CH1','TIME','TRG','FUNC']:
            feature('front','CUT','Shaft_'+name,panel_bottom-.2,panel_top+.1,C(x,y,PARAM['encoder_shaft_opening_mm']/2))
        elif name in ['EN_CH0','EN_CH1','CURSOR','TRG_MODE','MENU_BACK','RUN_STOP']:
            a=PARAM['button_opening_mm']
            feature('front','CUT','Button_'+name,panel_bottom-.2,panel_top+.1,R(x-a/2,y-a/2,a,a,PARAM['button_corner_radius_mm']))
            if panel_bottom<12.65:
                feature('front','CUT','Adapter_clearance_'+name,panel_bottom-.2,12.65,R(x-5.6,y-5.6,11.2,11.2,.5))
        elif name.startswith('BNC'):
            feature('front','ADD','BNC_well_'+str(round(x)),0,panel_top,
                    C(x,y,PARAM['bnc_opening_mm']/2+PARAM['bnc_well_wall_mm']))
        elif name=='LED_RUN':feature('front','CUT','Run_LED',panel_bottom-.2,panel_top+.1,C(x,y,1.8))
    case_screws=[(5,5),(185,5),(5,155),(185,155)]
    for i,(x,y) in enumerate(case_screws):
        feature('front','ADD','Screw_pillar_'+str(i),0,panel_top if y<10 else bezel_top,C(x,y,4.2))
        feature('front','CUT','Insert_M3_'+str(i),-.1,PARAM['m3_insert_depth_mm'],C(x,y,PARAM['m3_insert_hole_mm']/2))
    # Closed wells reach the PCB; only the BNC base passes through their floor.
    # Cut after the screw pillars so their crowns cannot obstruct the plug.
    for name,(x,y) in controls.items():
        if name.startswith('BNC'):
            feature('front','CUT','Input_'+str(round(x)),PARAM['bnc_well_floor_mm'],panel_top+.1,C(x,y,PARAM['bnc_opening_mm']/2))
            feature('front','CUT','BNC_base_passage_'+str(round(x)),-.1,PARAM['bnc_well_floor_mm']+.1,
                    C(x,y,PARAM['bnc_base_opening_mm']/2))
            w=PARAM['bnc_bayonet_slot_width_mm'];h=PARAM['bnc_bayonet_slot_length_mm']
            feature('front','CUT','BNC_bayonet_passage_'+str(round(x)),-.1,PARAM['bnc_well_floor_mm']+.1,
                    R(x-w/2,y-h/2,w,h,w/2-.1))
    feature('front','FILLET','Insert_entry_round',.2,0,'insert_entries')
    feature('front','FILLET','Control_rib_round',PARAM['control_rib_round_mm'],panel_bottom,'ribs')
    feature('front','FILLET','Panel_wall_root_round',.5,panel_bottom,'panel_wall')
    feature('front','FILLET','Well_and_pillar_root_round',.5,panel_bottom,'round_supports')
    feature('front','CUT','Control_film_recess',panel_top-PARAM['film_recess_mm'],panel_top+.1,film_profile)
    feature('panel_overlay','ADD','Printed_film',film_bottom,film_top,film_profile)
    for name,(x,y) in controls.items():
        if name in ['SET_CH0','SET_CH1','TIME','TRG','FUNC']:loop=C(x,y,PARAM['encoder_shaft_opening_mm']/2)
        elif name in ['EN_CH0','EN_CH1','CURSOR','TRG_MODE','MENU_BACK','RUN_STOP']:loop=R(x-3.5,y-3.5,7,7,PARAM['button_corner_radius_mm'])
        elif name.startswith('BNC'):loop=C(x,y,PARAM['bnc_opening_mm']/2)
        elif name=='LED_RUN':loop=C(x,y,1.8)
        else:continue
        feature('panel_overlay','CUT','Opening_'+name.replace(' ','_'),film_bottom-.1,film_top+.1,loop)
    feature('rear','ADD','Open_back_frame',rear_outer,rear_inner,
            outer,R(-.4,-.4-extra,190.8,160.8+extra,2))
    for i,(x,y) in enumerate(case_screws):
        feature('rear','ADD','PCB_mount_web_'+str(i),rear_outer,rear_inner,R(x-5.5,-2-extra if y<10 else 149.5,11,12.5+extra if y<10 else 12.5,1))
        feature('rear','CUT','PCB_M3_through_'+str(i),rear_outer-.1,rear_inner+.1,C(x,y,1.7))
    feature('rear','CUT','Rear_glass_seat',rear_outer-.1,glass_top,R(-3.25,-3.25-extra,196.5,166.5+extra,3.25))
    # Continuous vertical support from the rear frame to the clear PCB edge.
    support_y=-gap-extra+.2;support_end=1.5
    feature('rear','ADD','PCB_lower_edge_web',glass_top,-1.6,
            R(-.5,support_y,191,support_end-support_y))
    feature('rear','FILLET','PCB_support_root_round',1,-1.6,'support')
    feature('rear_glass','ADD','Acrylic_2mm',glass_bottom,glass_top,R(-3,-3-extra,196,166+extra,3))
    for i,x in enumerate((-6,179.2)):
        feature('rear_glass','CUT','Hinge_clearance_'+str(i),glass_bottom-.2,glass_top+.2,R(x,PARAM['stand_hinge_y_mm']-11.5,16.8,19,2))
    for i,(x,y) in enumerate(case_screws):
        feature('rear_glass','CUT','M3_clearance_'+str(i),glass_bottom-.2,glass_top+.2,C(x,y,1.7))
    # Reproduce the existing main-PCB key cutout in the rear acrylic.
    cutouts=board._collect_board_outline_cutouts()
    keys=[]
    for vertices in cutouts:
        pts=[(v.x_mils*.0254-2.25,v.y_mils*.0254-6.09) for v in vertices]
        xs,ys=zip(*pts)
        if 15<max(xs)-min(xs)<30 and 4<max(ys)-min(ys)<8:
            keys=[min(xs)-.5,min(ys)-.5,max(xs)-min(xs)+1,max(ys)-min(ys)+1,1.5]
    if not keys:raise ValueError('Cannot identify the PCB key-access cutout')
    feature('rear_glass','CUT','Keys_access_R1_5',glass_bottom-.2,glass_top+.2,R(*keys))
    data['rear_glass']={'size_mm':[196,166+extra,2],'corner_radius_mm':3,'keys_access_mm':keys,
                        'origin_mm':[-3,-3-extra,glass_bottom],'mounts_mm':case_screws,'hole_diameter_mm':3.4}
    (ROOT/'src/layout.json').write_text(json.dumps(data,indent=2)+'\n')
    feature('rear','ADD','Locating_tongue',-2,-.5,
            R(-4.9,-4.9-extra,199.8,169.8+extra,4.9),R(-3.9,-3.9-extra,197.8,167.8+extra,3.9))
    feature('front','CUT_RING','Locating_groove',-2.1,-.2,
            R(-5.15,-5.15-extra,200.3,170.3+extra,5.15),R(-3.65,-3.65-extra,197.3,167.3+extra,3.65))
    # Charger is in FRONT of the main PCB, on four separate 6 mm spacers.
    # Front shell section: 8.94 x 3.16 mm, corner R1.28. The rear mounting
    # flange is larger and lower; it must not set the visible opening.
    usb_throat=R(58.93,7.25,9.94,4.16,1.78)
    # Owner widened the short axis of the OUTER pocket for the plug body.
    # The receptacle throat keeps its independent 0.5 mm perimeter gap.
    usb_h=10.8;usb_r=2.4
    usb_funnel_start=191.1+PARAM['usb_c_lip_mm']
    feature('front','ADD_X','USB_C_inner_collar',191,193.2,
            R(56.43,4.75,14.94,9.16,3.2))
    feature('front','CUT_X','USB_C_throat',190.9,usb_funnel_start+.1,usb_throat)
    feature('front','CUT_X_LOFT','USB_C_funnel',usb_funnel_start,195.8,
            usb_throat,R(55.5,9.33-usb_h/2,16.8,usb_h,usb_r))
    for i,y in enumerate(range(78,109,5)):
        feature('front','CUT_X','Air_intake_'+str(i),192.8,196.2,chamfer_rect(y-1.2,5,2.4,17,.8))
    for i,y in enumerate(range(72,118,5)):
        feature('front','CUT_X','Air_outlet_'+str(i),-6.2,-2.8,chamfer_rect(y-1.2,5,2.4,17,.8))
    # Shared access has 3 mm around the two ports, including latch clearance.
    for part,lo,hi in [('rear',access[2],access[5]),('front',min(-3.7,access[2]),access[5])]:
        feature(part,'CUT','Top_connector_access',lo,hi,R(access[0],access[1],access[3]-access[0],access[4]-access[1]))
    # Existing MSK12C02 pin moves along Y; the cap socket is driven by its STEP.
    # Owner requirement: local wall within 5 mm of the original switch pin.
    # A flange is captured inside the two halves; the cap cannot pull out.
    slide=PARAM['sliding_clearance_mm'];slot_width=3.8+1.5+2*slide
    for part in ['rear','front']:
        feature(part,'ADD','Power_retaining_web',-5.85 if part=='rear' else -2,
                -2 if part=='rear' else 1.35,R(192.6,87.5,.6,13.5))
        feature(part,'CUT','Slider_slot',-2.25-1.5-slide if part=='rear' else -3.7,
                -2.25+1.5+slide,R(189.2,94.27-slot_width/2,7.8,slot_width))
        feature(part,'CUT_X','Power_thumb_recess',193.8,197.2,R(88.92,-5.85,10.7,7.2,1.4))
        feature(part,'CUT_X_LOFT','Power_recess_bevel',195,195.8,
                R(88.92,-5.85,10.7,7.2,1.4),R(88.12,-6.65,12.3,8.8,2.2))
    feature('power_cap','ADD','Slider_stem',0,4.2,R(-1.9,-1.5,3.8,3.0,.4))
    feature('power_cap','ADD','Captive_flange',1.2,2.4,R(-4.2,-2.7,8.4,5.4,.5))
    feature('power_cap','ADD','Thumb_pad',4.2,5,R(-4.2,-3.2,8.4,6.4,1))
    sw,sh=PARAM['switch_socket_width_mm'],PARAM['switch_socket_height_mm']
    feature('power_cap','CUT','Switch_pin_socket',-.1,2.3,R(-sw/2,-sh/2,sw,sh,.1))
    # Open the first 0.6 mm of the socket roof to clear the main PCB edge.
    feature('power_cap','CUT','PCB_edge_relief',-.1,.6,R(-2,.55,4,1.05))
    feature('power_cap','FILLET','Stem_shoulder_round',.2,0,'stem')
    for i,x in enumerate([-2.4,0,2.4]):
        feature('power_cap','ADD','Thumb_rib_'+str(i),4.9,5.4,R(x-.4,-2,.8,4,.3))
    feature('power_cap','FILLET','Thumb_rib_root_round',.2,5,'thumb_ribs')
    build_stand()
    feature('front','FILLET','Wall_transition_round',5,0,'wall')
    feature('front','FILLET','Outer_front_round',5,bezel_top,'outer')
    feature('rear','FILLET','Outer_rear_round',2,rear_outer,'outer')
    feature('kickstand','FILLET','Stand_leg_root_round',3,0,'stand_inside')
    feature('kickstand','FILLET','Pivot_neck_root_round',1,rear_outer-.4,'stand_necks')
    feature('kickstand','FILLET','Stand_contact_round',2,rear_outer-.4-5.6,'all')
    return data


def build_reference(board):
    with tempfile.TemporaryDirectory() as td:
        parts=board_geometry(board,Path(td),origin=(2.25,6.09))
    for x,y in [(5,5),(185,5),(5,155),(185,155)]:
        parts.append((tube(x,y,-7.6,-1.6,3,1.7),METAL))
    write_model(ROOT/'src/pcb_reference.step',parts)
    charger=read_solids(PCB/'Lib/J5019_HW357_Mechanical.step')
    # Proper 180-degree planar rotation: components toward LCD, USB-C right.
    matrix=[-1,0,0,190,0,-1,0,78.9,0,0,1,PARAM['charger_board_top_mm']]
    parts=[(transform(s,matrix),(38,101,61)) for s in charger]
    for x,y in [(167.5,51.4),(187.5,51.4),(167.5,76.4),(187.5,76.4)]:
        parts.append((tube(x,y,0,6,3,1.7),METAL))
    write_model(ROOT/'src/charger_reference.step',parts)
    data=json.loads((ROOT/'src/layout.json').read_text())
    caps=[]
    for name in ['SET_CH0','SET_CH1','TIME','TRG','FUNC']:
        x,y=data['controls'][name]
        bottom=data['encoder_cap_bottom_mm']
        cap=BRepAlgoAPI_Cut(cylinder(x,y,bottom,bottom+12,7.5),cylinder(x,y,bottom-.1,21.4,3.1)).Shape()
        caps.append((cap,BLACK))
    write_model(ROOT/'src/encoder_caps_reference.step',caps)


def check_shapes():
    result={}
    for name,features in FEATURES.items():
        solid=None
        lines=[]
        for kind,title,lo,hi,loops in features:
            if kind=='FILLET':
                unify=ShapeUpgrade_UnifySameDomain(solid,True,True,False);unify.Build();solid=unify.Shape()
                fillet=BRepFilletAPI_MakeFillet(solid);edges=[];coords=[]
                ex=TopExp_Explorer(solid,TopAbs_EDGE)
                while ex.More():
                    e=TopoDS.Edge(ex.Current());q=bbox(e)
                    c=BRepAdaptor_Curve(e);p=c.Value((c.FirstParameter()+c.LastParameter())/2)
                    if loops[0]=='wall':
                        selected=q[3]-q[0]<1e-5 and q[4]-q[1]<1e-5 and q[2]<PARAM['panel_top_mm']+.1 and q[5]>28.4 and any(math.dist(q[:2],p)<1e-4 for p in [(55.4,34.2),(65.4,37.3),(161.8,37.3),(171.8,34.2)])
                    elif loops[0]=='ribs':
                        rib_x=[float(f[4][0].split(',')[1])+PARAM['control_rib_width_mm']/2 for f in features if f[1].startswith('Control_rib_vertical_')]
                        along_rib=any(abs(abs(p.X()-x)-1)<1e-5 for x in rib_x)
                        along_bar=abs(abs(p.Y()-18)-1)<1e-5
                        at_root=abs(q[2]-hi)<1e-5 and abs(q[5]-hi)<1e-5 and 54<p.X()<176 and -6.2<p.Y()<37.5
                        at_cross=q[3]-q[0]<1e-5 and q[4]-q[1]<1e-5 and q[5]-q[2]>3 and along_rib and along_bar
                        selected=(at_root and (along_rib or along_bar)) or at_cross
                    elif loops[0]=='panel_wall':
                        selected=abs(q[2]-hi)<1e-5 and abs(q[5]-hi)<1e-5 and (abs(p.X()+3)<1e-5 or abs(p.X()-193)<1e-5 or abs(p.Y()+7)<1e-5) and p.Y()<33
                    elif loops[0]=='round_supports':
                        from OCP.GeomAbs import GeomAbs_Circle
                        selected=c.GetType()==GeomAbs_Circle and any(abs(c.Circle().Radius()-r)<1e-5 for r in (4.2,PARAM['bnc_opening_mm']/2+PARAM['bnc_well_wall_mm'])) and abs(q[2]-hi)<1e-5 and abs(q[5]-hi)<1e-5
                    elif loops[0]=='insert_entries':
                        from OCP.GeomAbs import GeomAbs_Circle
                        selected=c.GetType()==GeomAbs_Circle and abs(c.Circle().Radius()-PARAM['m3_insert_hole_mm']/2)<1e-5 and abs(q[2])<1e-5 and abs(q[5])<1e-5
                    elif loops[0]=='support':
                        selected=q[3]-q[0]<1e-5 and q[4]-q[1]<1e-5 and abs(q[1]-1.5)<1e-5 and any(abs(q[0]-x)<1e-5 for x in (10.5,179.5))
                    elif loops[0]=='thumb_ribs':
                        selected=abs(q[2]-hi)<1e-5 and abs(q[5]-hi)<1e-5 and abs(p.Y())<1.8 and any(abs(abs(p.X()-x)-.4)<1e-5 for x in (-2.4,0,2.4))
                    elif loops[0]=='stem':
                        selected=any(abs(q[2]-z)<1e-5 and abs(q[5]-z)<1e-5 for z in (1.2,2.4,4.2)) and (abs(abs(p.X())-1.9)<1e-5 or abs(abs(p.Y())-1.5)<1e-5)
                    elif loops[0]=='stand_inside':
                        selected=q[3]-q[0]<1e-5 and q[4]-q[1]<1e-5 and abs(q[1]-(7.8-PARAM['lower_extension_mm']))<1e-5 and any(abs(q[0]-x)<1e-5 for x in (10.2,179.8))
                    elif loops[0]=='stand_necks':
                        neck_y=PARAM['stand_hinge_y_mm']
                        selected=abs(q[2]-hi)<1e-5 and abs(q[5]-hi)<1e-5 and (p.X()<4 or p.X()>186) and (abs(p.Y()-(neck_y-9.6))<1e-5 or abs(p.Y()-neck_y)<1e-5 or abs(p.X()-3.8)<1e-5 or abs(p.X()-186.2)<1e-5)
                    else:selected=abs(q[2]-hi)<1e-4 and abs(q[5]-hi)<1e-4 and (loops[0]=='all' or q[0]<-5.7 or q[1]<-5.7-PARAM['lower_extension_mm'] or q[3]>195.7 or q[4]>165.7)
                    if selected:
                        if not any(e.IsSame(old) for old in edges):
                            edges.append(e);fillet.Add(lo,e)
                            c=BRepAdaptor_Curve(e);p=c.Value((c.FirstParameter()+c.LastParameter())/2)
                            coords.append(encode('E',p.X(),p.Y(),p.Z()))
                    ex.Next()
                if not edges:raise ValueError('No fillet edges '+title)
                fillet.Build()
                if not fillet.IsDone():raise ValueError('Fillet failed '+title)
                solid=fillet.Shape();loops=coords
                lines.append('\t'.join([kind,title,str(lo),str(hi),';'.join(loops)]));continue
            lines.append('\t'.join([kind,title,str(lo),str(hi),';'.join(loops)]))
            tool=feature_tool(kind,loops,lo,hi)
            solid=tool if solid is None else (BRepAlgoAPI_Fuse if kind.startswith('ADD') else BRepAlgoAPI_Cut)(solid,tool).Shape()
        if not BRepCheck_Analyzer(solid).IsValid():raise ValueError('Invalid '+name)
        (ROOT/'src'/f'{name}.plan').write_text('\n'.join(lines)+'\n')
        result[name]={'volume_mm3':volume(solid),'bounds_mm':[round(v,4) for v in bbox(solid)]}
    assert PARAM['panel_top_mm']-PARAM['control_panel_thickness_mm']>=13.5
    assert RELEASED_MM>PARAM['panel_top_mm']-PARAM['film_recess_mm']+.15
    (ROOT/'src/geometry-check.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


def main():
    from OCP.Message import Message, Message_Gravity
    for printer in Message.DefaultMessenger_s().Printers():
        printer.SetTraceLevel(Message_Gravity.Message_Warning)
    p=argparse.ArgumentParser();p.add_argument('--charger-source',type=Path)
    p.add_argument('--update-controls',action='store_true');p.add_argument('--skip-reference',action='store_true')
    a=p.parse_args();(ROOT/'output').mkdir(exist_ok=True)
    if a.update_controls:update_controls()
    if a.charger_source:import_charger(a.charger_source)
    b=AltiumPcbDoc.from_file(PCB/'AnalExtend/AnalExtend.PcbDoc')
    layout_and_plans(b)
    if not a.skip_reference:build_reference(b)
    check_shapes()


if __name__=='__main__':main()
