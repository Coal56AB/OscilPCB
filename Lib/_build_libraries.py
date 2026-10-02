from __future__ import annotations

import math
import sys
from pathlib import Path

from altium_monkey import (
    AltiumPcbLib, AltiumSchLib, PadHoleShape, PadShape, PcbBodyProjection, PcbLayer,
    PinElectrical, Rotation90, SchFontSpec, SchPointMils, make_sch_pin,
)

ROOT = Path(__file__).resolve().parent
MM = 1000 / 25.4


def mil(value):
    return value * MM


def track(fp, p1, p2, layer=PcbLayer.TOP_OVERLAY, width=0.12):
    fp.add_track(tuple(map(mil, p1)), tuple(map(mil, p2)),
                 width_mils=mil(width), layer=layer)


def rect(fp, x1, y1, x2, y2, layer, width=0.12):
    for p, q in [((x1,y1),(x2,y1)),((x2,y1),(x2,y2)),
                 ((x2,y2),(x1,y2)),((x1,y2),(x1,y1))]:
        track(fp,p,q,layer,width)


def pad(fp, n, x, y, w, h, hole=0, shape=PadShape.RECTANGLE):
    fp.add_pad(designator=str(n), position_mils=(mil(x),mil(y)),
               width_mils=mil(w), height_mils=mil(h),
               layer=PcbLayer.MULTI_LAYER if hole else PcbLayer.TOP,
               shape=shape, hole_size_mils=mil(hole),
               plated=True if hole else None)


def body(fp, name, x1, y1, x2, y2, height, z=0, color=0x303030):
    fp.add_extruded_3d_body(
        outline_points_mils=[(mil(x1),mil(y1)),(mil(x2),mil(y1)),
                             (mil(x2),mil(y2)),(mil(x1),mil(y2))],
        layer=PcbLayer.MECHANICAL_1, overall_height_mils=mil(height),
        standoff_height_mils=mil(z), side=PcbBodyProjection.TOP,
        name=name, body_color_3d=color)


def round_body(fp, name, radius, height, z=0, color=0xC0C0C0):
    pts=[(mil(radius*math.cos(i*math.tau/32)),
          mil(radius*math.sin(i*math.tau/32))) for i in range(32)]
    fp.add_extruded_3d_body(outline_points_mils=pts,
        layer=PcbLayer.MECHANICAL_1, overall_height_mils=mil(height),
        standoff_height_mils=mil(z), side=PcbBodyProjection.TOP,
        name=name, body_color_3d=color)


pcb=AltiumPcbLib()
sch=AltiumSchLib(show_comments_designators=True)
font=SchFontSpec(name='Arial',size=10)


def circuit_symbol(name, prefix, footprint, desc, designator_xy, comment_xy,
                   *, comment=None):
    """A schematic symbol drawn from its electrical function, without a box."""
    s = sch.add_symbol(name)
    s.set_description(desc)
    s.add_designator(prefix + '?', *designator_xy)
    s.add_parameter('Comment', comment or name,
                    x=comment_xy[0], y=comment_xy[1])
    s.add_footprint(footprint, library_name='Oscill.PcbLib')
    return s


def circuit_pin(s, number, name, x, y, orientation):
    s.add_pin(make_sch_pin(
        designator=str(number), name=name,
        location_mils=SchPointMils.from_mils(x, y),
        orientation=orientation, length_mils=200,
        electrical_type=PinElectrical.PASSIVE,
        name_visible=False, name_font=font, designator_font=font))


def functional_pin(s, number, name, x, y, orientation, *, show_name=True,
                   length=200):
    s.add_pin(make_sch_pin(
        designator=str(number), name=name,
        location_mils=SchPointMils.from_mils(x, y),
        orientation=orientation, length_mils=length,
        electrical_type=PinElectrical.PASSIVE,
        name_visible=show_name, designator_visible=True,
        name_font=font, designator_font=font))


def diode_right(s, left, right, y=0, cathode_kink=False):
    """GOST 2.730-73 table 5: anode left, cathode right."""
    bar = right - 100
    base = bar - 120
    s.add_line(left, y, base, y)
    s.add_polyline([(base, y-100), (bar, y), (base, y+100),
                    (base, y-100)])
    s.add_line(bar, y+110, bar, y-110)
    if cathode_kink:
        s.add_line(bar, y-110, bar+45, y-110)
    s.add_line(bar, y, right, y)


def diode_left(s, left, right, y=0, cathode_kink=False):
    """Mirror of the GOST diode: cathode left, anode right."""
    bar = (left + right) // 2 - 60
    base = bar + 120
    s.add_line(left, y, bar, y)
    s.add_line(bar, y+110, bar, y-110)
    if cathode_kink:
        s.add_line(bar, y-110, bar-45, y-110)
    s.add_polyline([(base, y-100), (bar, y), (base, y+100),
                    (base, y-100)])
    s.add_line(base, y, right, y)


def fp(name,desc,height):
    return pcb.add_footprint(name,height=f'{mil(height):.4f}mil',description=desc)


# ADI ST-48, body 7 x 7, lead span 9 x 9, 0.5 mm pitch.
f=fp('AD9288BSTZ-100_LQFP48','ADI ST-48, LQFP-48 7x7 mm, 0.5 mm pitch',1.6)
for side in range(4):
    for i in range(12):
        n=side*12+i+1
        t=-2.75+i*0.5
        if side==0: x,y=-4.3,t; w,h=1.5,0.3
        elif side==1: x,y=t,4.3; w,h=0.3,1.5
        elif side==2: x,y=4.3,-t; w,h=1.5,0.3
        else: x,y=-t,-4.3; w,h=0.3,1.5
        pad(f,n,x,y,w,h)
rect(f,-3.45,-3.45,3.45,3.45,PcbLayer.TOP_OVERLAY)
rect(f,-5.35,-5.35,5.35,5.35,PcbLayer.MECHANICAL_15,0.05)
track(f,(-5.1,-3.4),(-4.8,-3.4))
body(f,'LQFP-48 mould',-3.5,-3.5,3.5,3.5,1.5,0.1)
ad={i:'GND' for i in [1,12,16,27,29,32,34,45]}
ad.update({2:'AINA',3:'AINA_N',4:'DFS',5:'REFINA',6:'REFOUT',7:'REFINB',
  8:'S1',9:'S2',10:'AINB_N',11:'AINB',14:'ENCB',47:'ENCA'})
ad.update({i:'VD' for i in [13,30,31,48]})
ad.update({i:'VDD' for i in [15,28,33,46]})
ad.update({i:'NC' for i in [25,26,35,36]})
ad.update({17+i:f'D{7-i}B' for i in range(8)})
ad.update({37+i:f'D{i}A' for i in range(8)})
s=sch.add_symbol('AD9288BSTZ-100')
s.set_description('Analog Devices dual 8-bit ADC, 100 MSPS, LQFP-48 ST-48')
s.add_rectangle(-500,-1350,500,1350)
s.add_label('А/Ц',-70,1250)
analog=[2,3,11,10,5,6,7]
control=[47,14,4,8,9]
for row,n in enumerate(analog):
    functional_pin(s,n,ad[n],-500,1200-row*100,Rotation90.DEG_180)
channel_a=list(range(44,36,-1))  # D7A ... D0A
channel_b=list(range(17,25))     # D7B ... D0B
for row,n in enumerate(channel_a):
    functional_pin(s,n,ad[n],500,1200-row*100,Rotation90.DEG_0)
for row,n in enumerate(channel_b):
    functional_pin(s,n,ad[n],500,300-row*100,Rotation90.DEG_0)
for row,n in enumerate(control):
    functional_pin(s,n,ad[n],500,-600-row*100,Rotation90.DEG_0)
power=[13,15,28,30,31,33,46,48]
grounds=[1,12,16,27,29,32,34,45]
for row,n in enumerate(power):
    functional_pin(s,n,ad[n],-500,400-row*100,
                   Rotation90.DEG_180)
for row,n in enumerate(grounds):
    functional_pin(s,n,'GND',-500,-500-row*100,
                   Rotation90.DEG_180)
s.add_designator('DA?',-500,1550)
s.add_parameter('Comment','AD9288BSTZ-100',x=-500,y=-1550)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


# TI D package SOIC-8.
f=fp('OPA356_SOIC8','TI D SOIC-8, 1.27 mm pitch',1.75)
for i in range(4):
    y=1.905-i*1.27
    pad(f,i+1,-2.65,y,1.5,0.6)
    pad(f,8-i,2.65,y,1.5,0.6)
rect(f,-1.75,-2.4,1.75,2.4,PcbLayer.TOP_OVERLAY)
rect(f,-3.7,-2.85,3.7,2.85,PcbLayer.MECHANICAL_15,0.05)
track(f,(-3.55,2.55),(-3.25,2.55))
body(f,'SOIC-8 mould',-1.95,-2.45,1.95,2.45,1.75,0)
s=sch.add_symbol('OPA356')
s.set_description('TI OPA356AID, SOIC-8 (D), 1.27 mm pitch')
s.add_polyline([(-400,-400),(-400,400),(400,0),(-400,-400)])
functional_pin(s,2,'-IN',-400,150,Rotation90.DEG_180)
functional_pin(s,3,'+IN',-400,-150,Rotation90.DEG_180)
functional_pin(s,6,'OUT',400,0,Rotation90.DEG_0)
functional_pin(s,7,'V+',0,400,Rotation90.DEG_90)
functional_pin(s,4,'V-',0,-400,Rotation90.DEG_270)
s.add_line(0,400,0,200)
s.add_line(0,-400,0,-200)
s.add_designator('DA?',-400,600)
s.add_parameter('Comment','OPA356',x=-400,y=-800)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


# ADI ADA4940-1ARZ, R-8 narrow SOIC: 3.9 x 4.9 mm body, 1.27 mm pitch.
f=fp('ADA4940-1ARZ_SOIC8','ADI R-8 SOIC_N, 1.27 mm pitch',1.75)
for i in range(4):
    y=1.905-i*1.27
    pad(f,i+1,-2.65,y,1.5,0.6)
    pad(f,8-i,2.65,y,1.5,0.6)
rect(f,-1.75,-2.4,1.75,2.4,PcbLayer.TOP_OVERLAY)
rect(f,-3.7,-2.85,3.7,2.85,PcbLayer.MECHANICAL_15,0.05)
track(f,(-3.55,2.55),(-3.25,2.55))
body(f,'ADA4940-1 SOIC-8 mould',-1.95,-2.45,1.95,2.45,1.75)
s=sch.add_symbol('ADA4940-1ARZ')
s.set_description('ADI ADA4940-1ARZ fully differential ADC driver, SOIC-8 R-8')
s.add_rectangle(-450,-400,450,400)
for n,name,y in [(8,'+IN',300),(1,'-IN',100),
                 (2,'VOCM',-100),(7,'DISABLE',-300)]:
    functional_pin(s,n,name,-450,y,Rotation90.DEG_180)
for n,name,y in [(4,'+OUT',300),(5,'-OUT',100),
                 (3,'+VS',-100),(6,'-VS',-300)]:
    functional_pin(s,n,name,450,y,Rotation90.DEG_0)
s.add_designator('DA?',-450,600)
s.add_parameter('Comment','ADA4940-1ARZ',x=-450,y=-600)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


# Panasonic AQY212S/AQY214S, individual library models with common SOP4 geometry.
for name in ('AQY212S','AQY214S'):
    f=fp(name+'_SOP4','Panasonic GU SOP4, 2.54 mm pitch',2.2)
    for n,x,y in [(1,-1.27,3),(2,-1.27,-3),(3,1.27,-3),(4,1.27,3)]:
        pad(f,n,x,y,0.8,1.2)
    rect(f,-2.15,-2.2,2.15,2.2,PcbLayer.TOP_OVERLAY)
    rect(f,-2.8,-3.85,2.8,3.85,PcbLayer.MECHANICAL_15,0.05)
    track(f,(-2.6,3.5),(-2.35,3.5))
    body(f,'SOP4 mould',-2.2,-2.15,2.2,2.15,2.2,0)
    s=sch.add_symbol(name)
    s.set_description('Panasonic PhotoMOS GU SOP4, 1 Form A')
    s.add_rectangle(-500,-300,500,300)
    for n,x,y,angle in [(1,-500,150,Rotation90.DEG_180),
                        (2,-500,-150,Rotation90.DEG_180),
                        (4,500,150,Rotation90.DEG_0),
                        (3,500,-150,Rotation90.DEG_0)]:
        functional_pin(s,n,'',x,y,angle,show_name=False)
    # LED input and normally open isolated output, as a functional PhotoMOS UGO.
    s.add_polyline([(-330,80),(-250,-40),(-170,80),(-330,80)])
    s.add_line(-330,-50,-170,-50)
    s.add_polyline([(-500,150),(-250,150),(-250,80)])
    s.add_polyline([(-250,-50),(-250,-150),(-500,-150)])
    s.add_line(500,150,250,150)
    s.add_line(500,-150,250,-150)
    s.add_line(250,-150,290,65)
    for y in (80,-20):
        s.add_line(-100,y,80,y+50)
        s.add_polyline([(55,y+55),(80,y+50),(65,y+25)])
    s.add_designator('K?',-500,500)
    s.add_parameter('Comment',name,x=-500,y=-500)
    s.add_footprint(f.name,library_name='Oscill.PcbLib')


# Hongfa standard SMT S terminal, monostable 3 V coil. Body dimensions 10 x 6.5 x 5.65.
f=fp('HFD4_3_S_SMT','Hongfa HFD4/3-S, standard SMT leads (not S1 or S3)',5.65)
xs=[-3.8,-0.6,1.6,3.8]
for i,x in enumerate(xs):
    pad(f,i+1,x,3.175,0.8,2.15)
    pad(f,8-i,x,-3.175,0.8,2.15)
track(f,(-5.1,-2.7),(-5.1,2.7))
track(f,(5.1,-2.7),(5.1,2.7))
rect(f,-5.65,-4.65,5.65,4.65,PcbLayer.MECHANICAL_15,0.05)
track(f,(-5.5,4.2),(-5.1,4.2))
body(f,'HFD4 SMT mould',-5,-3.25,5,3.25,5.65,0)
s=sch.add_symbol('HFD4_3-S')
s.set_description('Hongfa HFD4/3-S monostable DPDT relay, standard SMT terminal')
s.add_rectangle(-650,-650,650,650)
functional_pin(s,1,'COIL+',-400,650,Rotation90.DEG_90,show_name=False)
functional_pin(s,8,'COIL-',-400,-650,Rotation90.DEG_270,show_name=False)
s.add_rectangle(-500,-250,-300,250)
s.add_line(-400,650,-400,250)
s.add_line(-400,-250,-400,-650)
s.add_label('K',-420,0)
for row,(nc,common,no) in enumerate(((2,3,4),(7,6,5))):
    shift=0 if row==0 else -700
    for n,y in ((nc,500+shift),(common,350+shift),(no,200+shift)):
        functional_pin(s,n,'',650,y,Rotation90.DEG_0,show_name=False)
    s.add_line(650,500+shift,200,500+shift)
    s.add_line(650,350+shift,350,350+shift)
    s.add_line(650,200+shift,200,200+shift)
    s.add_line(350,350+shift,220,500+shift)
    s.add_label('NC',430,520+shift)
    s.add_label('COM',430,370+shift)
    s.add_label('NO',430,220+shift)
s.add_designator('K?',-650,850)
s.add_parameter('Comment','HFD4_3-S',x=-650,y=-1100)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


# YLPTEC A0503S-2WR3, 2 W isolated dual-output DC/DC converter.
# S package: 19.5 x 7.0 x 10.0 mm; pins 1, 2, 4, 5, 6 on a 2.54 mm grid.
f=fp('A0503S-2WR3_SIP5','YLPTEC A0503S-2WR3, SIP 19.5x7.0 mm',10.0)
for n,x in [(1,-6.35),(2,-3.81),(4,1.27),(5,3.81),(6,6.35)]:
    pad(f,n,x,0,1.6,1.6,0.9,
        PadShape.RECTANGLE if n==1 else PadShape.CIRCLE)
# The datasheet dimensioned view is a bottom view.  Altium displays the
# footprint from the component side, so the body offset is mirrored in Y.
# The pin row is 1.0 mm from the rear face; the printed face is 6.0 mm away.
rect(f,-9.75,-6.0,9.75,1.0,PcbLayer.TOP_OVERLAY)
rect(f,-10.25,-6.5,10.25,1.5,PcbLayer.MECHANICAL_15,0.05)
track(f,(-9.5,-6.25),(-9.0,-6.25))
body(f,'A0503S-2WR3 case',-9.75,-6.0,9.75,1.0,10.0,0,0x202020)
s=sch.add_symbol('A0503S-2WR3')
s.set_description('YLPTEC 2 W isolated DC/DC, 5 V input, +/-3.3 V dual output')
s.add_rectangle(-500,-400,500,400)
functional_pin(s,1,'VIN',-500,200,Rotation90.DEG_180)
functional_pin(s,2,'GND',-500,-200,Rotation90.DEG_180)
functional_pin(s,6,'+VOUT',500,300,Rotation90.DEG_0)
functional_pin(s,5,'0V',500,0,Rotation90.DEG_0)
functional_pin(s,4,'-VOUT',500,-300,Rotation90.DEG_0)
s.add_line(-100,250,-100,-250)
s.add_line(100,250,100,-250)
s.add_label('DC',-400,80)
s.add_label('DC',250,80)
s.add_designator('U?',-500,600)
s.add_parameter('Comment','A0503S-2WR3',x=-500,y=-600)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


# TDK ACH3218-223-TD01, vertical SMD three-terminal T filter.
# Recommended land pattern: 1.4 / 2.2 / 1.4 mm in X, 1.94 mm in Y,
# with a 0.6 mm central ground land.
f=fp('ACH3218_3TERM_FILTER','TDK ACH3218 vertical SMD 3-terminal filter',2.5)
pad(f,1,-1.8,0,1.4,1.94)
pad(f,2,0,0,0.6,1.94)
pad(f,3,1.8,0,1.4,1.94)
track(f,(-1.5,1.15),(1.5,1.15))
track(f,(-1.5,-1.15),(1.5,-1.15))
rect(f,-2.75,-1.25,2.75,1.25,PcbLayer.MECHANICAL_15,0.05)
body(f,'ACH3218 ferrite',-1.15,-0.9,1.15,0.9,2.5,0,0x303030)
body(f,'ACH3218 terminal 1',-1.6,-0.9,-1.15,0.9,2.5,0,0xB0B0B0)
body(f,'ACH3218 terminal 3',1.15,-0.9,1.6,0.9,2.5,0,0xB0B0B0)
s=circuit_symbol('ACH3218-223-TD01','Z',f.name,
 'TDK 22 nF three-terminal T-type EMI filter, 20 V, 1.5 A',
 (-450,300),(-300,-500))
functional_pin(s,1,'LINE1',-450,0,Rotation90.DEG_180)
functional_pin(s,3,'LINE3',450,0,Rotation90.DEG_0)
functional_pin(s,2,'GND',0,-300,Rotation90.DEG_270)
s.add_polyline([(-450,0),(-300,0),(-250,80),(-150,-80),
                (-50,80),(0,0),(50,80),(150,-80),
                (250,80),(300,0),(450,0)])
s.add_line(0,0,0,-90)
s.add_line(-90,-90,90,-90)
s.add_line(-90,-150,90,-150)
s.add_line(0,-150,0,-300)


# Alps Alpine EC11E vertical encoder with one integrated push switch.
# Mounting-side coordinates from EC11E drawing No. 2/3.
f=fp('EC11E_ENCODER_SW','Alps Alpine EC11E vertical encoder with push switch',21.0)
for n,x,y in [('A',-2.5,-7.5),('C',0,-7.5),('B',2.5,-7.5),
              ('D',-2.5,7.0),('E',2.5,7.0)]:
    pad(f,n,x,y,1.8,1.8,1.0,PadShape.CIRCLE)
for x in (-6.25,6.25):
    f.add_pad(designator='', position_mils=(mil(x),0),
              width_mils=mil(2.5), height_mils=mil(3.6),
              layer=PcbLayer.MULTI_LAYER, shape=PadShape.RECTANGLE,
              hole_size_mils=mil(1.5), slot_length_mils=mil(2.6),
              slot_rotation_degrees=90, hole_shape=PadHoleShape.SLOT,
              plated=True)
rect(f,-5.85,-6.0,5.85,6.0,PcbLayer.TOP_OVERLAY)
rect(f,-7.75,-8.75,7.75,8.25,PcbLayer.MECHANICAL_15,0.05)
f.add_arc(center_mils=(0,0),radius_mils=mil(3.0),start_angle_degrees=0,
          end_angle_degrees=360,width_mils=mil(0.12),layer=PcbLayer.TOP_OVERLAY)
body(f,'EC11E base',-5.85,-6.0,5.85,6.0,1.0,0,0x206040)
body(f,'EC11E metal case',-5.5,-5.5,5.5,5.5,5.5,1.0,0xA0A0A0)
round_body(f,'EC11E shaft hub',4.2,1.0,6.5,0x303030)
round_body(f,'EC11E shaft',3.0,13.5,7.5,0xB0B0B0)
s=circuit_symbol('EC11E_ENCODER_SW','ENC',f.name,
 'Alps Alpine EC11E vertical incremental encoder with one push-on switch',
 (-250,400),(-250,-450),comment='EC11E')
s.add_rectangle(-250,-300,250,300)
functional_pin(s,'A','A',-250,200,Rotation90.DEG_180,length=100)
functional_pin(s,'C','C',-250,0,Rotation90.DEG_180,length=100)
functional_pin(s,'B','B',-250,-200,Rotation90.DEG_180,length=100)
functional_pin(s,'D','D',250,150,Rotation90.DEG_0,length=100)
functional_pin(s,'E','E',250,-150,Rotation90.DEG_0,length=100)
s.add_label('ENC',-130,140)
s.add_line(-120,40,80,40)
s.add_line(80,40,80,-30)
s.add_line(250,150,130,150)
s.add_line(250,-150,130,-150)
s.add_line(130,-150,170,90)
s.add_label('SW',140,-10)


# Custom raised tactile switch: an SMD button on a DIP-8-like carrier.
# Pins 1-4 are one internally common contact; pins 5-8 are the other.
f=fp('TACT_SMD_ON_DIP8_RAISED','Raised tactile switch on DIP-8 7.62 mm carrier',7.5)
for n,y in [(1,3.81),(2,1.27),(3,-1.27),(4,-3.81)]:
    pad(f,n,-3.81,y,1.7,1.7,0.9,
        PadShape.RECTANGLE if n==1 else PadShape.CIRCLE)
for n,y in [(8,3.81),(7,1.27),(6,-1.27),(5,-3.81)]:
    pad(f,n,3.81,y,1.7,1.7,0.9,PadShape.CIRCLE)
rect(f,-4.8,-5.1,4.8,5.1,PcbLayer.TOP_OVERLAY)
rect(f,-5.35,-5.65,5.35,5.65,PcbLayer.MECHANICAL_15,0.05)
track(f,(-5.1,4.75),(-4.85,4.75))
body(f,'DIP8 raised carrier',-4.8,-5.1,4.8,5.1,4.0,0,0x202020)
body(f,'SMD tactile switch',-3.1,-3.1,3.1,3.1,3.0,4.0,0x404040)
body(f,'Tact actuator',-1.6,-1.6,1.6,1.6,0.5,7.0,0x606060)
s=circuit_symbol('TACT_SMD_ON_DIP8_RAISED','SB',f.name,
 'Custom raised NO tactile switch; pins 1-4 common, pins 5-8 common',
 (-150,250),(-150,-300),comment='TACT_SMD')
s.add_rectangle(-150,-150,150,150)
for n in range(1,5):
    s.add_pin(make_sch_pin(
        designator=str(n), name='A',
        location_mils=SchPointMils.from_mils(-150,0),
        orientation=Rotation90.DEG_180, length_mils=100,
        electrical_type=PinElectrical.PASSIVE,
        name_visible=False, designator_visible=False,
        name_font=font, designator_font=font))
for n in range(5,9):
    s.add_pin(make_sch_pin(
        designator=str(n), name='B',
        location_mils=SchPointMils.from_mils(150,0),
        orientation=Rotation90.DEG_0, length_mils=100,
        electrical_type=PinElectrical.PASSIVE,
        name_visible=False, designator_visible=False,
        name_font=font, designator_font=font))
s.add_label('1-4',-130,60)
s.add_label('5-8',30,60)
s.add_line(-150,0,-50,0)
s.add_line(150,0,50,0)
s.add_line(-50,0,40,80)


# Nexperia BAV99 in SOT23, reflow land pattern.
f=fp('BAV99_SOT23','Nexperia BAV99 SOT23/TO-236AB',1.1)
for n,x,y in [(1,-0.95,-1.15),(2,0.95,-1.15),(3,0,1.15)]:
    pad(f,n,x,y,0.7,1.0)
track(f,(-1.45,-0.45),(-1.45,0.45))
track(f,(1.45,-0.45),(1.45,0.45))
rect(f,-1.8,-1.85,1.8,1.85,PcbLayer.MECHANICAL_15,0.05)
track(f,(-1.5,-1.55),(-1.3,-1.55))
body(f,'SOT23 mould',-1.45,-0.65,1.45,0.65,1.1,0)
s=circuit_symbol('BAV99','VD',f.name,
 'Nexperia BAV99 dual series diode, SOT23',(-350,220),(-150,-250))
circuit_pin(s,1,'A1',-350,0,Rotation90.DEG_180)
circuit_pin(s,2,'K2',350,0,Rotation90.DEG_0)
circuit_pin(s,3,'K1_A2',0,100,Rotation90.DEG_90)
diode_right(s,-350,0)
diode_right(s,0,350)
s.add_line(0,0,0,100)


# Universal low-capacitance ESD diode. SOD323 dimensions from Nexperia package data.
f=fp('ESD_LOW_CAP_SOD323','Generic low capacitance ESD diode, SOD323',0.95)
for n,x in [(1,-1.15),(2,1.15)]: pad(f,n,x,0,0.6,0.8)
rect(f,-0.8,-0.625,0.8,0.625,PcbLayer.TOP_OVERLAY)
rect(f,-1.85,-1.05,1.85,1.05,PcbLayer.MECHANICAL_15,0.05)
track(f,(-1.6,0.85),(-1.35,0.85))
body(f,'SOD323 mould',-0.85,-0.625,0.85,0.625,0.95,0)
s=circuit_symbol('ESD_LOW_CAP_SOD323','VD',f.name,
 'Universal unidirectional low capacitance ESD diode, SOD323',
 (-100,180),(-100,-250))
circuit_pin(s,1,'K',-300,0,Rotation90.DEG_180)
circuit_pin(s,2,'A',300,0,Rotation90.DEG_0)
diode_left(s,-300,300,cathode_kink=True)


# Generic vertical BNC pattern: centre plus four shell legs.
f=fp('BNC_VERTICAL_THT_5PIN','Generic vertical BNC THT, 4 shell legs',20.6)
pad(f,1,0,0,2.0,2.0,1.2,PadShape.CIRCLE)
for n,x,y in [(2,-3.43,3.43),(3,3.43,3.43),
              (4,3.43,-3.43),(5,-3.43,-3.43)]:
    pad(f,n,x,y,2.8,2.8,1.6,PadShape.CIRCLE)
f.add_arc(center_mils=(0,0),radius_mils=mil(2.5),start_angle_degrees=0,
          end_angle_degrees=360,width_mils=mil(0.12),layer=PcbLayer.TOP_OVERLAY)
rect(f,-8,-8,8,8,PcbLayer.MECHANICAL_15,0.05)
track(f,(-0.2,2.9),(0.2,2.9))
round_body(f,'BNC base',5.5,7.0,0,0xB0B0B0)
round_body(f,'BNC barrel',4.8,20.6,7.0,0xC0C0C0)
s=sch.add_symbol('BNC_VERTICAL_THT')
s.set_description('Generic vertical BNC jack, five through-hole terminals')
s.add_rectangle(-200,-250,200,250)
functional_pin(s,1,'SIGNAL',200,0,Rotation90.DEG_0,
               show_name=False)
for row,n in enumerate(range(2,6)):
    functional_pin(s,n,'SHIELD',-200,150-row*100,
                   Rotation90.DEG_180,
                   show_name=False)
s.add_label('BNC',-50,100)
s.add_label('SIG',-100,0)
s.add_label('SH',-150,-200)
s.add_designator('XW?',-200,400)
s.add_parameter('Comment','BNC_VERTICAL_THT',x=-200,y=-450)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


# PBD-40 female vertical socket, 2x20, based on Connfly DS1023 straight type.
f=fp('PBD_2X20_2P54_THT','2x20 vertical female socket, 2.54 mm pitch, 8.5 mm body',8.5)
for col in range(20):
    x=(col-9.5)*2.54
    for row in range(2):
        n=2*col+row+1
        y=-1.27 if row==0 else 1.27
        pad(f,n,x,y,1.7,1.7,1.02,
            PadShape.RECTANGLE if n==1 else PadShape.CIRCLE)
rect(f,-25.7,-2.55,25.7,2.55,PcbLayer.TOP_OVERLAY)
rect(f,-26.2,-3.1,26.2,3.1,PcbLayer.MECHANICAL_15,0.05)
track(f,(-26.0,-2.85),(-25.6,-2.85))
# Model the upper face as a grid of forty open sockets instead of male pins.
body(f,'PBD insulator base',-25.65,-2.5,25.65,2.5,6.2,0,0x202020)
for y1,y2 in [(-2.5,-1.95),(-0.425,0.425),(1.95,2.5)]:
    body(f,'PBD socket wall',-25.65,y1,25.65,y2,2.3,6.2,0x202020)
for x1,x2 in [(-25.65,-25.05),(25.05,25.65)]:
    body(f,'PBD end wall',x1,-2.5,x2,2.5,2.3,6.2,0x202020)
for col in range(19):
    x=(col-9)*2.54
    body(f,'PBD socket partition',x-0.35,-2.5,x+0.35,2.5,
         2.3,6.2,0x202020)
for col in range(20):
    x=(col-9.5)*2.54
    for y in (-1.27,1.27):
        body(f,'PBD recessed contact',x-0.28,y-0.28,x+0.28,y+0.28,
             0.15,6.2,0xC0A050)
s=sch.add_symbol('PBD_2X20_2P54')
s.set_description('PBD-40 vertical 2x20 2.54 mm female through-hole socket')
s.add_rectangle(-300,-1100,300,1100)
for row in range(20):
    y=1000-row*100
    functional_pin(s,2*row+1,'',-300,y,Rotation90.DEG_180,
                   show_name=False)
    functional_pin(s,2*row+2,'',300,y,Rotation90.DEG_0,
                   show_name=False)
s.add_designator('XS?',-300,1300)
s.add_parameter('Comment','PBD_2X20_2P54',x=-300,y=-1300)
s.add_footprint(f.name,library_name='Oscill.PcbLib')


sch.save(ROOT/'Oscill.SchLib')
if '--sch-only' not in sys.argv:
    pcb.save(ROOT/'Oscill.PcbLib')
print('symbols:',len(list(sch.symbols)),
      'footprints:', 'unchanged' if '--sch-only' in sys.argv
      else len(list(pcb.footprints)))
