"""CNC contour from the exact acrylic STEP; DXF millimetres, unoffset geometry."""
from pathlib import Path
from build_case import ROOT, read_solids, bbox
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Circle
import math
import json


def main():
    layout=json.loads((ROOT/'src/layout.json').read_text())['rear_glass']
    dx,dy=[-v for v in layout['origin_mm'][:2]]
    solid=read_solids(ROOT/'output/rear_glass.step')[0]
    top=bbox(solid)[5];face=None
    faces=TopExp_Explorer(solid,TopAbs_FACE)
    while faces.More():
        q=bbox(faces.Current())
        if abs(q[2]-top)<1e-5 and abs(q[5]-top)<1e-5:face=TopoDS.Face(faces.Current());break
        faces.Next()
    if face is None:raise ValueError('Acrylic top face missing')
    data=[]
    def put(*pairs):
        for code,value in pairs:data.extend((str(code),str(value)))
    put((0,'SECTION'),(2,'HEADER'),(9,'$ACADVER'),(1,'AC1015'),(9,'$INSUNITS'),(70,4),(0,'ENDSEC'),(0,'SECTION'),(2,'ENTITIES'))
    edges=TopExp_Explorer(face,TopAbs_EDGE);count=0;circles={}
    while edges.More():
        curve=BRepAdaptor_Curve(TopoDS.Edge(edges.Current()))
        a,b=curve.FirstParameter(),curve.LastParameter();p=curve.Value(a);q=curve.Value(b)
        if curve.GetType()==GeomAbs_Line:
            put((0,'LINE'),(8,'CUT'),(10,p.X()+dx),(20,p.Y()+dy),(30,0),(11,q.X()+dx),(21,q.Y()+dy),(31,0))
        elif curve.GetType()==GeomAbs_Circle:
            circle=curve.Circle();c=circle.Location();r=circle.Radius()
            key=(round(c.X()+dx,6),round(c.Y()+dy,6),round(r,6))
            circles.setdefault(key,[])
            if abs(b-a-2*math.pi)<1e-5:circles[key].append((0,360))
            else:
                def angle(t):
                    pt=curve.Value(t);return math.degrees(math.atan2(pt.Y()-c.Y(),pt.X()-c.X()))%360
                start,end,mid=angle(a),angle(b),angle((a+b)/2)
                if (mid-start)%360>(end-start)%360:start,end=end,start
                circles[key].append((start,end))
        else:raise ValueError('Non-circular CNC contour')
        count+=1;edges.Next()
    for (x,y,r),arcs in circles.items():
        if abs(sum((end-start)%360 if end-start!=360 else 360 for start,end in arcs)-360)<1e-5:
            put((0,'CIRCLE'),(8,'CUT'),(10,x),(20,y),(30,0),(40,r))
        else:
            for start,end in arcs:put((0,'ARC'),(8,'CUT'),(10,x),(20,y),(30,0),(40,r),(50,start),(51,end))
    put((0,'ENDSEC'),(0,'EOF'))
    dest=ROOT/'output/rear-glass-cut.dxf';dest.write_text('\n'.join(data)+'\n',encoding='ascii')
    print('CNC DXF:',layout['size_mm'],'mm; R1.5 minimum internal corners;',count,'analytic edges;',dest)


if __name__=='__main__':main()
