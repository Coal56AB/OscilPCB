"""Check saved Altium footprints against the reviewed package drawings.

This checks pin geometry, not PCB routing or physical component identity.
See ../docs/FOOTPRINT_AUDIT.md for sources and unresolved hardware checks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path

from altium_monkey import AltiumPcbDoc, AltiumPcbLib

ROOT = Path(__file__).resolve().parent
UNIT_MM = 0.00000254
TOL_MM = 0.00001


def close(actual, expected, context):
    if abs(actual - expected) > TOL_MM:
        raise ValueError(f'{context}: {actual:.6f} != {expected:.6f} mm')


def position(p):
    return p.x * UNIT_MM, p.y * UNIT_MM


def require_pad(f, number, x, y, *, w=None, h=None, hole=None):
    candidates = [p for p in f.pads if p.designator == str(number)]
    if not candidates:
        raise ValueError(f'{f.name}: missing pad {number}')
    p = min(candidates, key=lambda p: math.dist(position(p), (x, y)))
    for actual, expected, label in [(p.x * UNIT_MM, x, 'x'),
                                    (p.y * UNIT_MM, y, 'y'),
                                    (p.width * UNIT_MM, w, 'width'),
                                    (p.height * UNIT_MM, h, 'height'),
                                    (p.hole_size * UNIT_MM, hole, 'hole')]:
        if expected is not None:
            close(actual, expected, f'{f.name}/{number}/{label}')
    return p


def counterclockwise(f, count):
    points = [position(next(p for p in f.pads if p.designator == str(n)))
              for n in range(1, count + 1)]
    area = sum(x * v - u * y for (x, y), (u, v)
               in zip(points, points[1:] + points[:1]))
    if area <= 0:
        raise ValueError(f'{f.name}: mirrored pin order in component-side view')


def check_library(lib):
    expected_counts = {
        'AD9288BSTZ-100_LQFP48': 48, 'OPA356_SOIC8': 8,
        'AD8138ARZ_SOIC8': 8, 'ADA4817-1ARDZ_SOIC8_EP': 9,
        'AMS1117-ADJ_SOT223': 4, 'AQY212S_SOP4': 4, 'AQY214S_SOP4': 4,
        'HFD4_3_S_SMT': 8, 'A0503S-2WR3_SIP5': 5,
        'ACH3218_3TERM_FILTER': 3, 'EC11E_ENCODER_SW': 7,
        'TACT_SMD_ON_DIP8_RAISED': 8, 'BAV99_SOT23': 3,
        'ESD_LOW_CAP_SOD323': 2, 'R_1206': 2, 'BNC_VERTICAL_THT_5PIN': 5,
        'PBD_2X20_2P54_THT': 40, 'STM32F103RCT6_MODULE_PBD_2X14': 56,
        'MINGWU_29P3X18P9_MECH': 3,
    }
    if {f.name for f in lib.footprints} != set(expected_counts):
        raise ValueError('Unexpected Oscill.PcbLib footprint inventory')
    errors = []
    for f in lib.footprints:
        try:
            if len(f.pads) != expected_counts[f.name]:
                raise ValueError(f'{f.name}: unexpected pad count')
            for p in f.pads:
                if min(p.width, p.height) <= 0 or p.hole_size < 0:
                    raise ValueError(f'{f.name}: invalid pad size')
            if f.name == 'AD9288BSTZ-100_LQFP48':
                counterclockwise(f, 48)
                # ADI Fig. 5: eight corner anchors fix rotation as well as chirality.
                for n, x, y in [(1,-4.3,2.75),(12,-4.3,-2.75),
                                (13,-2.75,-4.3),(24,2.75,-4.3),
                                (25,4.3,-2.75),(36,4.3,2.75),
                                (37,2.75,4.3),(48,-2.75,4.3)]:
                    require_pad(f,n,x,y)
                for start in (1,13,25,37):
                    ps=[next(p for p in f.pads if p.designator == str(n))
                        for n in range(start,start+12)]
                    for a,b in zip(ps,ps[1:]):
                        close(math.dist(position(a),position(b)),.5,f'{f.name}/pitch')
                for p in f.pads:
                    close(min(p.width,p.height)*UNIT_MM,.3,f'{f.name}/pad width')
                    close(max(p.width,p.height)*UNIT_MM,1.5,f'{f.name}/pad length')
                    bridge=.5-.3-2*p.soldermask_expansion_manual*UNIT_MM
                    if bridge < .15-0.000003:
                        raise ValueError('AD9288: mask bridge below 0.15 mm')
            elif f.name in ('OPA356_SOIC8','AD8138ARZ_SOIC8','ADA4817-1ARDZ_SOIC8_EP'):
                counterclockwise(f,8)
                for n,x,y in [(1,-2.65,1.905),(4,-2.65,-1.905),
                              (5,2.65,-1.905),(8,2.65,1.905)]:
                    require_pad(f,n,x,y,w=1.5,h=.6)
                if f.name.startswith('ADA4817'):
                    # Copper land rounded up from the 2.29 mm nominal exposed pad.
                    require_pad(f,9,0,0,w=2.30,h=2.30)
            elif f.name == 'AMS1117-ADJ_SOT223':
                # AMS top view, rotated 180 degrees: tab below; 1=ADJ at right.
                for n,x in [(1,2.29),(2,0),(3,-2.29)]:
                    require_pad(f,n,x,3.25,w=1.3,h=2.0)
                require_pad(f,2,0,-3.25,w=3.6,h=2.0)
                if Counter(p.designator for p in f.pads) != Counter({'2':2,'1':1,'3':1}):
                    raise ValueError('AMS1117: tab must share pin 2')
            elif f.name.startswith(('AQY212S','AQY214S')):
                counterclockwise(f,4)
                for n,x,y in [(1,-1.27,-3),(2,1.27,-3),(3,1.27,3),(4,-1.27,3)]:
                    require_pad(f,n,x,y,w=.8,h=1.2)
            elif f.name == 'HFD4_3_S_SMT':
                # Hongfa bottom view reflected in Y; unequal pitch fixes the marked end.
                for n,x in [(1,-3.8),(2,-.6),(3,1.6),(4,3.8)]:
                    require_pad(f,n,x,-3.175,w=.8,h=2.15)
                    require_pad(f,9-n,x,3.175,w=.8,h=2.15)
            elif f.name == 'A0503S-2WR3_SIP5':
                for n,x in [(1,-6.35),(2,-3.81),(4,1.27),(5,3.81),(6,6.35)]:
                    require_pad(f,n,x,0,hole=.9)
                if {p.designator for p in f.pads} != {'1','2','4','5','6'}:
                    raise ValueError('A0503: missing pin 3 must remain absent')
            elif f.name == 'ACH3218_3TERM_FILTER':
                for n,x,w in [(1,-1.8,1.4),(2,0,.6),(3,1.8,1.4)]:
                    require_pad(f,n,x,0,w=w,h=1.94)
            elif f.name == 'EC11E_ENCODER_SW':
                for n,x,y in [('A',-2.5,-7.5),('C',0,-7.5),('B',2.5,-7.5),
                              ('D',-2.5,7),('E',2.5,7)]:
                    require_pad(f,n,x,y,hole=1)
                for x in (-6.25,6.25):
                    p=require_pad(f,'',x,0,hole=1.5)
                    close(p.slot_size*UNIT_MM,2.6,'EC11E mounting slot length')
            elif f.name == 'TACT_SMD_ON_DIP8_RAISED':
                counterclockwise(f,8)
                require_pad(f,1,-3.81,3.81,hole=.9)
                require_pad(f,4,-3.81,-3.81,hole=.9)
                require_pad(f,5,3.81,-3.81,hole=.9)
                require_pad(f,8,3.81,3.81,hole=.9)
            elif f.name == 'BAV99_SOT23':
                for n,x,y in [(1,-.95,-1.15),(2,.95,-1.15),(3,0,1.15)]:
                    require_pad(f,n,x,y,w=.7,h=1)
            elif f.name == 'ESD_LOW_CAP_SOD323':
                require_pad(f,1,-1.15,0,w=.6,h=.8)
                require_pad(f,2,1.15,0,w=.6,h=.8)
            elif f.name == 'R_1206':
                require_pad(f,1,-1.275,0,w=1.05,h=1.8)
                require_pad(f,2,1.275,0,w=1.05,h=1.8)
            elif f.name == 'BNC_VERTICAL_THT_5PIN':
                require_pad(f,1,0,0,hole=1.2)
                for n,x,y in [(2,-3.43,3.43),(3,3.43,3.43),(4,3.43,-3.43),(5,-3.43,-3.43)]:
                    require_pad(f,n,x,y,hole=1.6)
            elif f.name in ('PBD_2X20_2P54_THT','STM32F103RCT6_MODULE_PBD_2X14'):
                # PBD hole grid; module connector assignment is a project convention.
                rows=1 if len(f.pads)==40 else 2
                cols=20 if rows==1 else 14
                for bank in range(rows):
                    cy=0 if rows==1 else (11.43 if bank==0 else -11.43)
                    for col in range(cols):
                        for row in range(2):
                            n=bank*28+col*2+row+1
                            require_pad(f,n,(col-(cols-1)/2)*2.54,
                                        cy+(-1.27 if row==0 else 1.27),hole=1.02)
            else:
                for x,y in [(-12.45,-9.45),(-12.45,9.45),(13.1,0)]:
                    p=require_pad(f,'',x,y,hole=2.5)
                    if p.is_plated or p.net_index is not None:
                        raise ValueError('Mechanical module: mounting holes must be NPTH')
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError('\n'.join(errors))
    return len(expected_counts)


def world_position(component, p):
    angle=math.radians(float(component.rotation or 0))
    x,y=p.x,(-p.y if component.layer=='BOTTOM' else p.y)
    return (round(component.get_x_mils()*10000+x*math.cos(angle)-y*math.sin(angle)),
            round(component.get_y_mils()*10000+x*math.sin(angle)+y*math.cos(angle)))


def check_placed(board, libraries):
    templates={f.name:f for lib in libraries for f in lib.footprints}
    checked=0
    for i,c in enumerate(board.components):
        if c.footprint not in templates:
            continue
        template=templates[c.footprint]
        pads=[p for p in board.pads if p.component_index==i]
        if len(pads)!=len(template.pads):
            raise ValueError(f'{c.designator}: unexpected pad count')
        for p in template.pads:
            x,y=world_position(c,p)
            candidates=[q for q in pads if q.designator==p.designator]
            if not candidates:
                raise ValueError(f'{c.designator}: missing pad {p.designator}')
            q=min(candidates,key=lambda q:math.hypot(q.x-x,q.y-y))
            close(math.hypot(q.x-x,q.y-y)*UNIT_MM,0,f'{c.designator}/{p.designator}')
        checked+=1
    return checked


def check_imported(board):
    # Local pad centres for the remaining nine package types. Sizes are checked
    # separately from orientation because the PcbDoc stores per-pad rotations.
    specs={
        'SOT23-3N': [('1',-.95,.95),('2',-.95,-.95),('3',.95,0)],
        'RESC2012(0805)N': [('1',-.9,0),('2',.9,0)],
        'CAPC2012(0805)N': [('1',-.85,0),('2',.85,0)],
        'CAPMP3528X210N': [('1',-1.45,0),('2',1.45,0)],
        'CAPMP7343X310N': [('1',-3.025,0),('2',3.025,0)],
        'CAP_CER_1206': [('1',-1.55,0),('2',1.55,0)],
        'SOD323': [('1',-1.25,0),('2',1.25,0)],
        'LED_3MM_GREEN': [('1',0,0),('2',2.54,0)],
        'INDC1608(0603)N': [('1',-.8,0),('2',.8,0)],
    }
    checked=0
    for i,c in enumerate(board.components):
        if c.footprint not in specs:
            continue
        pads=[p for p in board.pads if p.component_index==i and p.layer in (1,32,74)]
        if Counter(p.designator for p in pads)!=Counter(n for n,x,y in specs[c.footprint]):
            raise ValueError(f'{c.designator}: unexpected electrical pads')
        for n,x,y in specs[c.footprint]:
            class Point:
                pass
            p=Point()
            p.x=round(x/UNIT_MM)
            p.y=round(y/UNIT_MM)
            expected=world_position(c,p)
            actual=next(p for p in pads if p.designator==n)
            close(math.dist(expected,(actual.x,actual.y))*UNIT_MM,0,f'{c.designator}/{n}')
        checked+=1
    return checked


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,default=ROOT/'Oscill.PcbLib')
    parser.add_argument('--board',type=Path)
    args=parser.parse_args()
    lib=AltiumPcbLib(args.library)
    print(f'PASS: {check_library(lib)} saved footprints')
    if args.board:
        others=[AltiumPcbLib(p) for p in ROOT.glob('*.PcbLib') if p.name!='Oscill.PcbLib']
        board=AltiumPcbDoc.from_file(args.board)
        print(f'PASS: {check_placed(board,[lib,*others])} placed library footprints')
        print(f'PASS: {check_imported(board)} imported package pad layouts')
        print(f'Inventory: {len(board.components)} components, '
              f'{len(set(c.footprint for c in board.components))} footprint types')
    print('Routing DRC and physical fit are separate checks; see FOOTPRINT_AUDIT.md.')


if __name__=='__main__':
    main()
