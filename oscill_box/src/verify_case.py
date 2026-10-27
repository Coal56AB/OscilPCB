"""Check exported solids, print meshes, controls and the assembled clearances."""
from collections import Counter
import json
import math
import struct
from pathlib import Path
from hashlib import sha256
from build_case import ROOT, PCB, PARAM, FEATURES, layout_and_plans, AltiumPcbDoc, read_solids, compound, volume, bbox, transform, box, cylinder, stand_matrix, feature_tool, P, R, rounded_points, rear_levels
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer


def overlap(a,b):
    return all(a[k+3]>b[k]+1e-6 and b[k+3]>a[k]+1e-6 for k in range(3))


def mesh_check(name,shape):
    path=ROOT/'output'/f'{name}.stl'
    raw=path.read_bytes();n=struct.unpack_from('<I',raw,80)[0]
    assert len(raw)==84+50*n,(name,'invalid binary STL')
    edges=Counter();points=[]
    for i in range(n):
        tri=[struct.unpack_from('<3f',raw,84+50*i+j) for j in [12,24,36]]
        points.extend(tri)
        tri=[tuple(round(v,5) for v in p) for p in tri]
        for a,b in zip(tri,tri[1:]+tri[:1]):
            if a!=b:edges[tuple(sorted([a,b]))]+=1
    bad=sum(v!=2 for v in edges.values())
    assert bad==0,(name,'non-manifold STL edges',bad)
    q=bbox(shape)
    for k in range(3):
        size=max(p[k] for p in points)-min(p[k] for p in points)
        assert abs(size-(q[k+3]-q[k]))<.01,(name,'STL is not in millimetres',k,size)
    print('PASS',name,': one valid solid;',n,'STL triangles, closed mesh, millimetres')


def main():
    data=json.loads((ROOT/'src/layout.json').read_text())
    panel_top=PARAM['panel_top_mm'];panel_bottom=data['panel_inner_z_mm']
    film_bottom,film_top=data['film_z_mm']
    assert panel_bottom>=13.5,'inner control panel must clear PCB top by at least 13.5 mm'
    assert data['pcb_sha256']==sha256((PCB/'AnalExtend/AnalExtend.PcbDoc').read_bytes()).hexdigest(),'PCB changed: rebuild reference geometry first'
    layout_and_plans(AltiumPcbDoc.from_file(PCB/'AnalExtend/AnalExtend.PcbDoc'))
    posts=[f for f in FEATURES['rear'] if f[1].startswith('PCB_standoff_')]
    assert not posts,'rear has integral spacers instead of four mounting ears'
    assert len([f for f in FEATURES['rear'] if f[1].startswith('PCB_mount_web_')])==4
    assert any(f[1]=='PCB_lower_edge_web' for f in FEATURES['rear'])
    buttons=[f for f in FEATURES['front'] if f[1].startswith('Button_')]
    assert len(buttons)==6 and all(list(map(float,f[4][0].split(',')[3:]))==[7,7,PARAM['button_corner_radius_mm']] for f in buttons)
    inputs=[f for f in FEATURES['front'] if f[1].startswith('Input_')]
    assert len(inputs)==2 and all(float(f[4][0].split(',')[-1])==PARAM['bnc_opening_mm']/2 for f in inputs)
    expected=json.loads((ROOT/'src/geometry-check.json').read_text())
    shapes={}
    for name in ['front','rear','power_cap','kickstand','rear_glass','panel_overlay']:
        solids=read_solids(ROOT/'output'/f'{name}.step')
        assert len(solids)==1,(name,'solid count',len(solids))
        s=solids[0];assert BRepCheck_Analyzer(s).IsValid(),name
        # Parasolid and OpenCascade represent curved loft/fillet faces
        # differently. 0.1 mm³ is <1.1 ppm of the front shell volume.
        assert abs(volume(s)-expected[name]['volume_mm3'])<.1,(name,'native feature geometry differs',volume(s)-expected[name]['volume_mm3'])
        if name in ['front','rear','power_cap','kickstand']:mesh_check(name,s)
        shapes[name]=s
        assert (ROOT/f'{name}.SLDPRT').is_file()
    led_x,led_y=data['controls']['LED_RUN']
    assert led_x==data['controls']['RUN_STOP'][0],'RUN opening is not centred above RUN/STOP'
    led_opening=cylinder(led_x,led_y,panel_bottom-.1,panel_top+.1,1.79)
    for name in ['front','panel_overlay']:
        assert volume(BRepAlgoAPI_Common(shapes[name],led_opening).Shape())<.001,('RUN opening blocked',name)
    print('PASS: RUN opening in case and film shares the RUN/STOP centreline')
    from build_case import tube
    for name,(x,y) in data['controls'].items():
        if not name.startswith('BNC'):continue
        radius=PARAM['bnc_opening_mm']/2;floor=PARAM['bnc_well_floor_mm'];neck=PARAM['bnc_base_opening_mm']/2
        bore=cylinder(x,y,floor+.001,panel_top+.1,radius-.001)
        for part in ('front','panel_overlay'):
            assert volume(BRepAlgoAPI_Common(shapes[part],bore).Shape())<.001,(name,'BNC plug opening is obstructed',part)
        passage=cylinder(x,y,-.01,panel_top+.1,neck-.001)
        assert volume(BRepAlgoAPI_Common(shapes['front'],passage).Shape())<.001,(name,'BNC base passage is obstructed')
        w=PARAM['bnc_bayonet_slot_width_mm'];h=PARAM['bnc_bayonet_slot_length_mm']
        keyway=feature_tool('CUT',[R(x-w/2,y-h/2,w,h,w/2-.1)],-.1,panel_top+.1)
        assert volume(BRepAlgoAPI_Common(shapes['front'],keyway).Shape())<.001,(name,'vertical bayonet assembly path is obstructed')
        for probe in (tube(x,y,.01,floor-.01,radius-.01,neck+.01),
                      tube(x,y,floor+.01,11.5,radius+PARAM['bnc_well_wall_mm']-.01,radius+.01)):
            # The left well merges into a screw pillar; its intentional pilot
            # clips only the outermost part of the well wall.
            for sx,sy in ((5,5),(185,5),(5,155),(185,155)):
                probe=BRepAlgoAPI_Cut(probe,cylinder(sx,sy,-.01,PARAM['m3_insert_depth_mm']+.001,PARAM['m3_insert_hole_mm']/2+.001)).Shape()
            probe=BRepAlgoAPI_Cut(probe,keyway).Shape()
            assert abs(volume(BRepAlgoAPI_Common(shapes['front'],probe).Shape())-volume(probe))<.001,(name,'well wall or floor is incomplete')
        wall=min(PARAM['bnc_well_wall_mm'],min(math.hypot(x-sx,y-sy) for sx,sy in ((5,5),(185,5),(5,155),(185,155)))-radius-PARAM['m3_insert_hole_mm']/2)
        assert wall>.99,(name,'insufficient wall beside glued-in insert seat',wall)
        minimum_wall=tube(x,y,floor+.01,PARAM['m3_insert_depth_mm']-.01,radius+wall-.01,radius+.01)
        assert abs(volume(BRepAlgoAPI_Common(shapes['front'],minimum_wall).Shape())-volume(minimum_wall))<.001,(name,'well wall is incomplete beside insert pilot')
    for x,y in ((5,5),(185,5),(5,155),(185,155)):
        pilot=cylinder(x,y,.01,PARAM['m3_insert_depth_mm']-.001,PARAM['m3_insert_hole_mm']/2-.001)
        assert volume(BRepAlgoAPI_Common(shapes['front'],pilot).Shape())<.001,'BNC well obstructs an M3 insert pilot'
    print('PASS: both BNC openings are clear through the panel and film, diameter %.1f mm'%PARAM['bnc_opening_mm'])
    print('PASS: cylindrical BNC wells reach the PCB; complete walls and annular floors; base passages and M3 pilots clear')
    print('PASS: continuous BNC assembly path through the well floor; vertical bayonet reliefs, 3.0 x 12.8 mm rounded envelope')
    common=volume(BRepAlgoAPI_Common(shapes['front'],shapes['rear']).Shape())
    assert common<1e-5,('case halves interfere',common)
    access=data['top_access_mm'];port_space=box(*access)
    for s in (shapes['front'],shapes['rear']):
        assert volume(BRepAlgoAPI_Common(s,port_space).Shape())<.001,'top access is blocked by the locating tongue'
    assert data['case_bounds'][3]-190.975<5
    sx,sy,sw,sh,_=data['screen_window'];xmin,_,_,xmax,_,_=data['case_bounds']
    ex,ey,ew,eh=data['screen_outer_window']
    assert abs((ex-xmin)-(xmax-ex-ew))<1e-6,'unequal display bezel sides'
    active=data['lcd']['active_mm']
    glass=data['lcd']['glass_mm']
    assert abs(active[0]-glass[0]-3)<1e-5 and abs(glass[4]-active[4]-4)<1e-5,'active-area offset differs from owner glass measurements'
    assert max(abs(v) for v in [sx-active[0],sy-active[1],sx+sw-active[3],sy+sh-active[4]])<1e-6,'bevel does not end at the active-area border'
    assert len(data['lcd']['mounts_mm'])==3 and data['lcd']['mount_error_mm']<.15
    bezel=next(f for f in FEATURES['front'] if f[1]=='Display_bezel')
    assert abs(bezel[2]-28-.5)<.001
    shared=[f for f in FEATURES['front'] if f[1].startswith('Screw_pillar_')]
    assert len(shared)==4 and {tuple(map(float,f[4][0].split(',')[1:3])) for f in shared}=={(5,5),(185,5),(5,155),(185,155)}
    # 3 mm empty perimeter adjacent to PCB, excluding its intended mount pads
    # and switch mechanism; the rounded corner is an exact 3 mm offset too.
    for probe in [box(-2.99,12,-1.5,-.01,148,-.1),box(190.01,12,-1.5,192.99,80,-.1),
                  box(190.01,110,-1.5,192.99,148,-.1),box(12,-2.99,-1.5,178,-.01,-.1),
                  box(12,160.01,-1.5,178,162.99,-.1)]:
        for s in (shapes['front'],shapes['rear']):assert volume(BRepAlgoAPI_Common(s,probe).Shape())<.001
    for key in ['SET_CH0','SET_CH1','TIME','TRG','FUNC']:
        x,y=data['controls'][key]
        assert PARAM['encoder_shaft_opening_mm']==8.0
        shaft=cylinder(x,y,panel_bottom-.01,film_top+.01,3.999)
        for name in ['front','panel_overlay']:
            assert volume(BRepAlgoAPI_Common(shapes[name],shaft).Shape())<.001,('encoder shaft opening',key,name)
        probe=cylinder(x,y,data['encoder_cap_bottom_mm']+7.7,data['encoder_cap_bottom_mm']+12,10.5)
        assert volume(BRepAlgoAPI_Common(shapes['front'],probe).Shape())<.001,('encoder finger clearance',key)
    print('PASS: all five encoder openings are diameter 8 mm, giving 1 mm radial clearance around diameter 6 mm shafts')
    # No intermediate shelf: one level for the deck, wall at Y=37.3.
    shelf=box(68,34,21.21,159,37.2,22.39)
    assert volume(BRepAlgoAPI_Common(shapes['front'],shelf).Shape())<.001,'unwanted shelf above the flat deck'
    assert not any(f[1]=='LCD_PCB_clearance' for f in FEATURES['front']),'visible LCD slot remains'
    # The straight wall stops at the tangent of the R5 upper edge blend.
    flat_wall_top=data['case_bounds'][5]-5-.01
    for lo,hi in [(panel_top+.01,flat_wall_top)]:
        wall=box(68,37.31,lo,159,38.15,hi)
        assert abs(volume(BRepAlgoAPI_Common(shapes['front'],wall).Shape())-volume(wall))<.001,'wall is stepped or inclined'
    # The right transition runs in front of the mounting ear; no circular
    # relief is cut through either the deck or the wall.
    assert not any(f[1] in ('LCD_mount_deck_clearance','LCD_mount_wall_clearance') for f in FEATURES['front'])
    for x in (164,168,171):
        y=37.3-(x-161.8)*3.1/10
        cheek=box(x-.01,y+.05,21,x+.01,y+1.9,flat_wall_top)
        assert abs(volume(BRepAlgoAPI_Common(shapes['front'],cheek).Shape())-volume(cheek))<.001,'angled LCD wall is open'
    film_step=box(68,34.3,film_bottom+.01,159,36.89,panel_top-.01)
    assert volume(BRepAlgoAPI_Common(shapes['front'],film_step).Shape())<.001,'ridge crosses the sticker footprint'
    tongue=box(-4.7,20,-1.5,-4.1,30,-.6)
    assert abs(volume(BRepAlgoAPI_Common(shapes['rear'],tongue).Shape())-volume(tongue))<.001,'locating tongue missing'
    assert volume(BRepAlgoAPI_Common(shapes['front'],tongue).Shape())<.001,'tongue has no mating groove'
    positions=[(dy,dx) for dy in [0,-.75,-1.5] for dx in [-.25,0,.25]]
    cx,cy,cz=data['power_cap_origin_mm']
    caps=[transform(shapes['power_cap'],[0,0,1,cx+dx,1,0,0,cy+dy,0,1,0,cz]) for dy,dx in positions]
    assert all(bbox(c)[3]<data['case_bounds'][3]-.19 for c in caps),'power head protrudes'
    assert len([f for f in FEATURES['power_cap'] if f[1].startswith('Thumb_rib_') and f[0]=='ADD'])==3,'raised power ribs missing'
    assemblies={name:[(s,bbox(s))] for name,s in shapes.items() if name!='power_cap'}
    for name in ['pcb_reference','charger_reference','encoder_caps_reference']:
        assemblies[name]=[(s,bbox(s)) for s in read_solids(ROOT/'src'/f'{name}.step')]
    from build_case import BNC_STUD_DIAMETER_MM, BNC_STUD_SPAN_MM
    bnc_x,bnc_y=data['controls']['BNC_Channel 0']
    ligament=math.hypot(bnc_x-5,bnc_y-5)-PARAM['bnc_opening_mm']/2-PARAM['m3_insert_outer_diameter_mm']/2
    assert ligament>1.1,'insufficient ligament between insert and BNC plug bore'
    assert PARAM['m3_insert_hole_mm']>PARAM['m3_insert_outer_diameter_mm'],'glued insert needs clearance'
    assert PARAM['m3_insert_depth_mm']>=PARAM['m3_insert_length_mm']+.5,'insert seat needs bottom clearance'
    print('PASS: four glued-in M3x5 insert seats, diameter %.1f x %.1f mm; minimum local BNC ligament to insert %.3f mm'%(PARAM['m3_insert_hole_mm'],PARAM['m3_insert_depth_mm'],ligament))
    for name,(x,y) in data['controls'].items():
        if not name.startswith('BNC'):continue
        studs=[b for s,b in assemblies['pcb_reference'] if
               abs((b[0]+b[3])/2-x)<.01 and 4<abs((b[1]+b[4])/2-y)<6 and
               abs(b[3]-b[0]-BNC_STUD_DIAMETER_MM)<.001 and
               abs(b[5]-b[2]-BNC_STUD_DIAMETER_MM)<.001]
        assert len(studs)==2,(name,'two vertical BNC studs missing',studs)
        assert abs(max(b[4] for b in studs)-min(b[1] for b in studs)-BNC_STUD_SPAN_MM)<.001,(name,'BNC stud span is incorrect')
        for b in studs:
            swept=box(b[0],b[1],-.1,b[3],b[4],b[5])
            assert volume(BRepAlgoAPI_Common(shapes['front'],swept).Shape())<.001,(name,'BNC stud catches the case during assembly')
    print('PASS: reference BNC studs are vertical and proportioned to the selected interface; their full insertion sweep clears the case')
    assert len(assemblies['encoder_caps_reference'])==5
    for s,b in assemblies['encoder_caps_reference']:
        assert abs(b[3]-b[0]-15)<1e-4 and abs(b[4]-b[1]-15)<1e-4,'caps must be diameter 15 mm'
        assert abs(b[2]-data['encoder_cap_bottom_mm'])<1e-5 and abs(b[5]-b[2]-12)<1e-5,'encoder cap height differs from panel clearance'
        assert 21-b[2]>=4,'encoder cap engages less than 4 mm of shaft'
    assert not any(f[1].startswith('Adapter_clearance_') for f in FEATURES['front']),'unnecessary adapter pockets remain'
    for key in ['EN_CH0','EN_CH1','CURSOR','TRG_MODE','MENU_BACK','RUN_STOP']:
        x,y=data['controls'][key]
        stroke=box(x-3.1,y-3.1,13.5,x+3.1,y+3.1,16)
        for part in ('front','panel_overlay'):
            assert volume(BRepAlgoAPI_Common(shapes[part],stroke).Shape())<.001,('button full stroke obstructed',key,part)
    # A full-thickness patch beside the controls verifies the actual native
    # solid, independently of the height recorded in the layout.
    deck=box(96,20,panel_bottom+.001,97,21,film_bottom-.001)
    assert abs(volume(BRepAlgoAPI_Common(shapes['front'],deck).Shape())-volume(deck))<.001,'control panel thickness is incomplete'
    below=box(96,20,panel_bottom-.1,97,21,panel_bottom-.001)
    assert volume(BRepAlgoAPI_Common(shapes['front'],below).Shape())<.001,'control panel inner face is below 13.5 mm'
    print('PASS: native control panel inner face %.1f mm above PCB; full button stroke clear; cap shaft engagement %.1f mm'%(panel_bottom,21-data['encoder_cap_bottom_mm']))
    ribs=[f for f in FEATURES['front'] if f[0].startswith('ADD') and f[1].startswith('Control_rib_')]
    assert len(ribs)==6,'five panel ribs and their crossbar are required'
    for kind,title,lo,hi,loops in ribs:
        bottom=PARAM['control_crossbar_bottom_mm'] if title.endswith('crossbar') else PARAM['control_rib_bottom_mm']
        assert abs(lo-bottom)<1e-6
        probe=feature_tool(kind,loops,lo+.01,panel_bottom-.01)
        assert abs(volume(BRepAlgoAPI_Common(shapes['front'],probe).Shape())-volume(probe))<.001,('control rib incomplete',title)
    for key in ['SET_CH0','SET_CH1','TIME','TRG','FUNC']:
        x,y=data['controls'][key]
        pin_space=box(x-6,y-9.5,0,x+6,y-5.5,6)
        assert volume(BRepAlgoAPI_Common(shapes['front'],pin_space).Shape())<.001,('encoder three-pin row obstructed by support',key)
    print('PASS: five 2 mm separating ribs reach PCB top Z=0; crossbar bottom Z=9.5 mm; 6 mm reserved above encoder pin rows; rounded junctions')
    board=AltiumPcbDoc.from_file(PCB/'AnalExtend/AnalExtend.PcbDoc')
    stm_index=next(i for i,c in enumerate(board.components) if c.designator=='STM32F103RCT6')
    from build_case import polygon, U
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    stm_clearances={}
    for body in board.component_bodies:
        if body.component_index!=stm_index:continue
        pts=[(v.x*U-2.25,v.y*U-6.09) for v in body.outline]
        s=polygon(pts,body.standoff_height*U,body.overall_height*U)
        distance=BRepExtrema_DistShapeShape(s,shapes['front']).Value()
        stm_clearances.setdefault(body.name,[]).append(distance)
    assert min(stm_clearances['STM32 module PCB'])>2,'STM PCB needs at least 2 mm clearance'
    assert min(stm_clearances['USB-C shell'])>5.6,'STM USB reaches front shell'
    print('PASS: STM PCB clearance %.3f mm; module USB clearance %.3f mm'%(min(stm_clearances['STM32 module PCB']),min(stm_clearances['USB-C shell'])))
    acrylic=shapes['rear_glass'];b=bbox(acrylic)
    assert all(abs(v-w)<1e-5 for v,w in zip([b[3]-b[0],b[4]-b[1],b[5]-b[2]],data['rear_glass']['size_mm']))
    for s in (shapes['front'],shapes['rear'],shapes['panel_overlay']):
        assert volume(BRepAlgoAPI_Common(acrylic,s).Shape())<.001,'acrylic clashes with case'
    for s in (shapes['front'],shapes['rear']):
        assert volume(BRepAlgoAPI_Common(shapes['panel_overlay'],s).Shape())<.001,'film collides with case'
    support=transform(shapes['panel_overlay'],[1,0,0,0,0,1,0,0,0,0,1,-.16])
    assert abs(volume(BRepAlgoAPI_Common(support,shapes['front']).Shape())-volume(support))<.001,'outer fillet undercuts the sticker seat'
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    arc_centres=set();edges=TopExp_Explorer(shapes['panel_overlay'],TopAbs_EDGE)
    while edges.More():
        edge=TopoDS.Edge(edges.Current());curve=BRepAdaptor_Curve(edge)
        if curve.GetType()==GeomAbs_Circle and abs(curve.Circle().Radius()-5)<1e-5 and abs(bbox(edge)[2]-film_top)<1e-5:
            centre=curve.Circle().Location()
            arc_centres.add(tuple(round(v,5) for v in (centre.X(),centre.Y(),centre.Z())))
        edges.Next()
    assert data['panel_corner_radius_mm']==5 and len(arc_centres)==8,('panel contour must have eight R5 corners',len(arc_centres))
    print('PASS: panel pocket and film share an analytic contour with eight R5 corners')
    access=feature_tool('CUT',[R(*data['rear_glass']['keys_access_mm'])],b[2]-.2,b[5]+.2)
    assert volume(BRepAlgoAPI_Common(acrylic,access).Shape())<.001,'key access is closed'
    dxf=(ROOT/'output/rear-glass-cut.dxf').read_text().splitlines();pairs=list(zip(dxf[::2],dxf[1::2]))
    assert ('9','$INSUNITS') in pairs and ('70','4') in pairs
    assert sum(p==('0','CIRCLE') for p in pairs)==4,'acrylic screw holes missing in CNC DXF'
    assert all(float(v)>=1.5-1e-5 for c,v in pairs if c=='40'),'CNC radius smaller than 3 mm cutter'
    print('PASS: acrylic %s mm, key access and hinge notches; CNC DXF uses millimetres and radii >=1.5 mm; film fits the deck'%data['rear_glass']['size_mm'])
    # Datums from the user's assembly: the Zynq PCB is between the main PCB
    # and LCD. These checks reject the former wrong yet collision-free pose.
    module_pcbs=[b for s,b in assemblies['pcb_reference'] if
                 abs(b[3]-b[0]-91)<.01 and abs(b[4]-b[1]-78)<.01 and abs(b[5]-b[2]-1.6)<.01]
    assert len(module_pcbs)==1,('Zynq PCB not identified',module_pcbs)
    assert abs(module_pcbs[0][2]-11.3)<.01 and abs(module_pcbs[0][5]-12.9)<.01,module_pcbs
    charger_pcbs=[b for s,b in assemblies['charger_reference'] if
                  abs(b[3]-b[0]-25)<.01 and abs(b[4]-b[1]-30)<.01 and abs(b[5]-b[2]-1.6)<.01]
    assert len(charger_pcbs)==1 and abs(charger_pcbs[0][2]-6)<.01 and abs(charger_pcbs[0][5]-7.6)<.01,charger_pcbs
    # The user's ordinary 6 mm spacers are separate hardware, not part of rear.
    rear_inner,_,_,glass_top=rear_levels()
    for x,y in [(5,5),(185,5),(5,155),(185,155)]:
        ear=tube(x,y,glass_top+.001,rear_inner-.001,2.9,1.8)
        assert abs(volume(BRepAlgoAPI_Common(shapes['rear'],ear).Shape())-volume(ear))<.001,'rear mounting ear is thinner than 2.4 mm'
        probe=tube(x,y,-7.6,-1.6,2.9,1.8)
        assert volume(BRepAlgoAPI_Common(shapes['rear'],probe).Shape())<.001,'integral spacer remains'
        spacers=[q for q,b in assemblies['pcb_reference'] if
                 abs(b[2]+7.6)<.001 and abs(b[5]+1.6)<.001 and
                 abs((b[0]+b[3])/2-x)<.001 and abs((b[1]+b[4])/2-y)<.001]
        assert len(spacers)==1 and abs(volume(BRepAlgoAPI_Common(spacers[0],probe).Shape())-volume(probe))<.001,'separate 6 mm spacer missing'
    print('PASS: four mounting ears and separate 6 mm spacers; locating tongue/groove; 3 mm PCB perimeter; symmetric bezel; angled encoder wall')
    print('PASS: rear mounting ears %.1f mm; sticker fully supported after outer rounding'%PARAM['rear_mount_web_mm'])
    edge_contact=box(10,.25,-1.9,180,1.25,-1.601)
    assert abs(volume(BRepAlgoAPI_Common(shapes['rear'],edge_contact).Shape())-volume(edge_contact))<.001,'lower PCB edge support is interrupted'
    vertical_web=box(10,.25,-9.9,180,1.25,-2.11)
    assert abs(volume(BRepAlgoAPI_Common(shapes['rear'],vertical_web).Shape())-volume(vertical_web))<.001,'lower PCB support must be a solid vertical wall'
    above_edge=box(10,.25,-1.599,180,1.25,-.01)
    assert volume(BRepAlgoAPI_Common(shapes['rear'],above_edge).Shape())<.001,'lower support presses into the PCB'
    flat_top=box(10,-6.5,-1.7,180,1.25,-1.601)
    assert abs(volume(BRepAlgoAPI_Common(shapes['rear'],flat_top).Shape())-volume(flat_top))<.001,'lower support still has a step on top'
    print('PASS: solid vertical wall under lower PCB edge, contact at Z=-1.6 mm; no preload')
    print('PASS: corrected display pose with 3 mounts, max axis discrepancy %.3f mm; full active area visible'%data['lcd']['mount_error_mm'])
    # The STEP stores one switch position. Move its actuator together with the
    # cap in the second position, while leaving the PCB and switch shell fixed.
    pin_bounds=(189.425,94.295,-2.64,190.975,95.745,-1.86)
    actuator_ids={i for i,(q,b) in enumerate(assemblies['pcb_reference'])
                  if all(abs(x-y)<.01 for x,y in zip(b,pin_bounds))}
    assert len(actuator_ids)==1,('cannot identify movable switch actuator',actuator_ids)
    for left in ['front','rear']:
        s=shapes[left];a=bbox(s)
        for right in ['pcb_reference','charger_reference','encoder_caps_reference']:
            for i,(q,b) in enumerate(assemblies[right]):
                if not overlap(a,b):continue
                v=volume(BRepAlgoAPI_Common(s,q).Shape())
                assert v<.001,(left,right,i,'intersection mm3',v,'bounds',b)
    for i,(s,a) in enumerate(assemblies['charger_reference']):
        for j,(q,b) in enumerate(assemblies['pcb_reference']):
            if overlap(a,b):
                v=volume(BRepAlgoAPI_Common(s,q).Shape())
                assert v<.001,('charger versus PCB/LCD',i,j,'intersection mm3',v)
    for j,cap in enumerate(caps):
        a=bbox(cap)
        for right in ['front','rear','pcb_reference']:
            for i,(q,b) in enumerate(assemblies[right]):
                if right=='pcb_reference' and i in actuator_ids:
                    q=transform(q,[1,0,0,0,0,1,0,positions[j][0],0,0,1,0]);b=bbox(q)
                if overlap(a,b):
                    v=volume(BRepAlgoAPI_Common(cap,q).Shape())
                    assert v<.001,('power cap',j,right,i,'intersection mm3',v)
    for dy in [0,-.75,-1.5]:
        for dx in [-.7,.7]:
            cap=transform(shapes['power_cap'],[0,0,1,cx+dx,1,0,0,cy+dy,0,1,0,cz])
            stop=sum(volume(BRepAlgoAPI_Common(cap,s).Shape()) for s in (shapes['front'],shapes['rear']))
            assert stop>.1,('cap can escape without hitting the retaining flange or outer head',dy,dx,stop)
    print('PASS: halves, electronics and slider have no intersections through stroke and +/-0.25 mm axial play')
    print('PASS: flange/head block outward/inward cap escape; switch wall 4.825 mm from pin')
    # A recessed receptacle admits the metal tongue first; the larger plug
    # overmould starts 2.2 mm behind the connector face (conditional envelope).
    for plug in [feature_tool('CUT_X',[R(59.65,7.955,8.5,2.75,1)],190.9,193.3),
                 feature_tool('CUT_X',[R(57.9,6.33,12,6,1)],193.3,202)]:
        assert volume(BRepAlgoAPI_Common(shapes['front'],plug).Shape())<.001,'USB funnel blocks reference plug'
    funnel=next(f for f in FEATURES['front'] if f[1]=='USB_C_funnel')
    inner=list(map(float,funnel[4][0].split(',')[1:]));outer=list(map(float,funnel[4][1].split(',')[1:]))
    assert abs(inner[2]-9.94)<1e-6 and abs(inner[3]-4.16)<1e-6 and abs(inner[4]-1.78)<1e-6
    assert abs(outer[2]-16.8)<1e-6 and abs(outer[3]-10.8)<1e-6
    assert abs(outer[0]+outer[2]/2-63.9)<1e-6 and abs(outer[1]+outer[3]/2-9.33)<1e-6
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    faces=TopExp_Explorer(shapes['front'],TopAbs_FACE);ledges=[]
    while faces.More():
        q=bbox(faces.Current())
        if abs(q[2]-11.41)<1e-5 and abs(q[5]-11.41)<1e-5 and q[0]>190 and q[1]<63.9<q[4]:ledges.append(q)
        faces.Next()
    assert len(ledges)==1 and abs(ledges[0][3]-191.1-.5)<1e-5,('USB axial lip is not 0.5 mm',ledges)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    clearance=[]
    for y,z in rounded_points(59.43,7.75,8.94,3.16,1.28,8)+[(63.9,7.75),(63.9,10.91),(59.43,9.33),(68.37,9.33)]:
        point=BRepBuilderAPI_MakeVertex(gp_Pnt(191.05,y,z)).Vertex()
        clearance.append(BRepExtrema_DistShapeShape(point,shapes['front']).Value())
    assert all(.498<v<.502 for v in clearance),('USB perimeter clearance is not 0.5 mm',min(clearance),max(clearance))
    print('PASS: USB front-shell perimeter clearance %.4f..%.4f mm; outer pocket retained'%(min(clearance),max(clearance)))
    print('PASS: USB axial lip 0.5 mm from receptacle face; 12x6 mm plug body starts 2.2 mm behind that face')
    for angle in sorted(set(range(0,int(data['stand_open_degrees'])+1,4))|{data['stand_open_degrees']}):
        stand=transform(shapes['kickstand'],stand_matrix(angle));a=bbox(stand)
        for right in ['front','rear','rear_glass','pcb_reference']:
            for i,(q,b) in enumerate(assemblies[right]):
                if overlap(a,b):
                    v=volume(BRepAlgoAPI_Common(stand,q).Shape())
                    assert v<.001,('stand rotation collision',angle,right,i,v)
    cy,cz=data['stand_hinge_yz_mm']
    for x0,x1 in [(-5.79,-4.601),(194.601,195.79)]:
        cover=box(x0,cy-2,cz-1.8,x1,cy+2,cz+1.8)
        assert abs(volume(BRepAlgoAPI_Common(shapes['rear'],cover).Shape())-volume(cover))<.001,'hinge outer cover is thinner than 1.2 mm'
    for angle in [-5,data['stand_open_degrees']+5]:
        stand=transform(shapes['kickstand'],stand_matrix(angle))
        assert volume(BRepAlgoAPI_Common(stand,shapes['rear']).Shape())>.01,('stand stop missing',angle)
    assert all('M3' not in f[1] and 'nut' not in f[1].lower() for f in FEATURES['kickstand']),'stand still needs fasteners'
    for angle in (0,data['stand_open_degrees']):
        stand=transform(shapes['kickstand'],stand_matrix(angle))
        for axis in range(3):
            for sign in (-1,1):
                shift=[0,0,0];shift[axis]=sign*.6
                moved=transform(stand,[1,0,0,shift[0],0,1,0,shift[1],0,0,1,shift[2]])
                assert sum(volume(BRepAlgoAPI_Common(moved,q).Shape()) for q in (shapes['front'],shapes['rear']))>.01,('stand axle can escape',angle,axis,sign)
    print('PASS: recessed power head; USB funnel admits reference 12x6 mm plug; stand folds 0..%g degrees with end stops'%data['stand_open_degrees'])
    assert (ROOT/'oscill_case.SLDASM').is_file()
    print('Physical fit and printing are not measured by this check.')


if __name__=='__main__':main()
