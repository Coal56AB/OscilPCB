"""Socketed button: measured total heights 16 mm released, 15 mm pressed.

Intermediate dimensions describe the photographed stack, not a factory drawing.
"""
from altium_monkey import PcbLayer, PcbBodyProjection

RELEASED_MM = 16.0
PRESSED_MM = 15.0
MM_TO_MIL = 1000 / 25.4


def add_tactile_stack(footprint):
    def block(name, x0, y0, x1, y1, z0, z1, color):
        footprint.add_extruded_3d_body(
            outline_points_mils=[(x*MM_TO_MIL,y*MM_TO_MIL)
                                 for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]],
            layer=PcbLayer.MECHANICAL_1, side=PcbBodyProjection.TOP,
            standoff_height_mils=z0*MM_TO_MIL,
            overall_height_mils=z1*MM_TO_MIL, name=name, body_color_3d=color)
    block('DIP8 turned-pin socket',-4.8,-5.1,4.8,5.1,0,4.5,0x202020)
    for x in (-3.81,3.81):
        for y in (-3.81,-1.27,1.27,3.81):
            block('Socket contact',x-.55,y-.55,x+.55,y+.55,4.0,4.65,0xB0A050)
            block('Adapter pin',x-.3,y-.3,x+.3,y+.3,4.5,10.6,0xB0B0B0)
    block('SOIC8 DIP8 adapter PCB',-4.8,-5.1,4.8,5.1,10.6,12.2,0x287042)
    block('SMD switch housing',-3.1,-3.1,3.1,3.1,12.2,14.5,0x909090)
    block('Released actuator (pressed top 15 mm)',-1.6,-1.6,1.6,1.6,
          14.5,RELEASED_MM,0x202020)
