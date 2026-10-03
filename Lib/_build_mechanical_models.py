"""Build the standalone mechanical STEP/STL models.

Requires cadquery-ocp for STEP/STL geometry.
The existing mechanical STEP files supply the unchanged PCB and component
solids. Geometry selection makes repeated runs idempotent.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_DocumentTool
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

ROOT = Path(__file__).resolve().parent
LCD = ROOT / "LCD_7inch_1024x600_HDMI.step"
ZYNQ = ROOT / "Zynq020_Mini_Mechanical.step"
MINGWU_MODULE = ROOT / "Mingwu_29p3x18p9_Mechanical.step"

# These are the offsets in the current PcbDoc. The display's rear PCB is
# 19 mm above the main board top; Zynq is 14.5 mm below it. Only the
# standalone models are regenerated here; their PcbDoc placements are not.
ZYNQ_OFFSET_MM = 14.5
LCD_OFFSET_MM = 19.0
MAIN_BOARD_THICKNESS_MM = 1.6

BLUE_PCB = (24, 80, 118)
GREEN_PCB = (34, 105, 62)
METAL = (182, 188, 194)
DARK_METAL = (106, 114, 122)
BLACK = (25, 28, 33)
GLASS = (16, 22, 31)
FLEX = (175, 105, 35)
GOLD = (206, 164, 62)


def bbox(shape):
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    return (
        bounds.GetXMin(), bounds.GetYMin(), bounds.GetZMin(),
        bounds.GetXMax(), bounds.GetYMax(), bounds.GetZMax(),
    )


def read_solids(path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"Cannot read STEP: {path}")
    reader.TransferRoots()
    iterator = TopExp_Explorer(reader.OneShape(), TopAbs_SOLID)
    solids = []
    while iterator.More():
        solids.append(TopoDS.Solid(iterator.Current()))
        iterator.Next()
    return solids


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(
        gp_Pnt(x0, y0, z0), x1 - x0, y1 - y0, z1 - z0
    ).Shape()


def tube(x, y, z0, z1, outer_radius, hole_radius):
    axis = gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1))
    outer = BRepPrimAPI_MakeCylinder(axis, outer_radius, z1 - z0).Shape()
    inner = BRepPrimAPI_MakeCylinder(axis, hole_radius, z1 - z0).Shape()
    return BRepAlgoAPI_Cut(outer, inner).Shape()


def cylinder(x, y, z0, z1, radius):
    axis = gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1))
    return BRepPrimAPI_MakeCylinder(axis, radius, z1 - z0).Shape()


def connector_shell(extents, cavity):
    return BRepAlgoAPI_Cut(box(*extents), box(*cavity)).Shape()


def compound(solids):
    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for solid in solids:
        builder.Add(result, solid)
    return result


def write_model(path, colored_solids):
    solids = [shape for shape, _color in colored_solids]
    shape = compound(solids)
    document = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    for solid, color in colored_solids:
        label = shape_tool.AddShape(solid)
        rgb = Quantity_Color(*(channel / 255 for channel in color), Quantity_TOC_RGB)
        color_tool.SetColor(label, rgb, XCAFDoc_ColorGen)
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.Transfer(document, STEPControl_AsIs)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".step", delete=False) as f:
        temp_step = Path(f.name)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".stl", delete=False) as f:
        temp_stl = Path(f.name)
    try:
        if writer.Write(str(temp_step)) != IFSelect_RetDone:
            raise RuntimeError(f"Cannot export STEP: {path}")
        BRepMesh_IncrementalMesh(shape, 0.12)
        StlAPI_Writer().Write(shape, str(temp_stl))
        # Validate both outputs before replacing either file.
        if (not read_solids(temp_step) or temp_stl.stat().st_size < 100
                or temp_step.read_bytes().count(b"COLOUR_RGB") < 3):
            raise RuntimeError(f"Invalid generated 3D model: {path}")
        os.replace(temp_step, path)
        os.replace(temp_stl, path.with_suffix(".stl"))
    finally:
        temp_step.unlink(missing_ok=True)
        temp_stl.unlink(missing_ok=True)


def make_lcd():
    retained = []
    for solid in read_solids(LCD):
        x0, _y0, z0, x1, _y1, z1 = bbox(solid)
        # Preserve display stack and the central parts on the rear PCB.
        # Edge connectors, old switch and old spacers are recreated below.
        if z0 >= -0.01 or (x0 > 20 and x1 < 145 and z1 <= 0.01):
            if z0 >= 8.86:
                color = GLASS
            elif z0 >= 7.1:
                color = BLACK
            elif z0 >= 3.6:
                color = BLACK
            elif z0 >= 1.6:
                color = DARK_METAL
            elif z0 >= -0.01:
                color = BLUE_PCB
            elif 70 < x0 < 100 and 15 < _y0 < 45:
                color = FLEX
            else:
                color = BLACK
            retained.append((solid, color))

    # Coordinates use the screen's FRONT view. The supplied MPI7002 rear
    # drawing is mirrored horizontally: all four controls are on the right
    # edge in this local system, and on the left edge when viewed from back.
    # HDMI type A, two Micro-USB sockets and the backlight switch. Recesses
    # show plug entry and cable clearance in Altium's 3D Viewer.
    retained.extend([
        (connector_shell(
            (151.9, 97.5, -6.27, 166.1, 111.7, -0.15),
            (161.3, 99.2, -4.85, 167.0, 110.0, -1.65),
        ), METAL),
        (connector_shell(
            (157.5, 81.8, -3.0, 165.7, 91.3, -0.10),
            (161.4, 83.0, -2.35, 166.5, 90.1, -0.65),
        ), METAL),
        (connector_shell(
            (157.5, 69.5, -3.0, 165.7, 78.0, -0.10),
            (161.4, 70.5, -2.35, 166.5, 77.0, -0.65),
        ), METAL),
        (box(156.9, 57.5, -2.8, 164.7, 65.0, -0.10), BLACK),
        (box(163.7, 59.4, -2.25, 166.0, 63.1, -1.05), DARK_METAL),
    ])

    # LCD rear PCB z=0; with a 19 mm placement the spacers end at local
    # z=-19, exactly at the main PCB top.
    for x in (4.0, 160.9):
        for y in (4.66, 119.61):
            retained.append((tube(
                x, y,
                -LCD_OFFSET_MM, 0,
                outer_radius=3.0, hole_radius=1.6,
            ), METAL))
    write_model(LCD, retained)


def make_zynq():
    holes = [(x, y) for x in (3.01, 74.99) for y in (3.0, 88.0)]
    retained = []
    for solid in read_solids(ZYNQ):
        x0, y0, z0, x1, y1, z1 = bbox(solid)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        is_old_spacer = (
            abs(x1 - x0 - 6.2) < 0.2
            and abs(y1 - y0 - 6.2) < 0.2
            and abs(z0 - 1.6) < 0.2
            and 8.0 < z1 < 20.0
            and any(abs(cx - x) < 0.2 and abs(cy - y) < 0.2 for x, y in holes)
        )
        if not is_old_spacer:
            if x1 - x0 > 77 and y1 - y0 > 90 and z1 <= 1.61:
                color = GREEN_PCB
            elif z0 < -2.9 and z1 > 10:
                color = GOLD
            elif z1 > 14 or y0 > 86 or y0 < 0:
                color = METAL
            elif x0 < 6 and y0 >= 7 and y1 <= 58:
                color = BLACK
            else:
                color = BLACK
            retained.append((solid, color))

    # Zynq components face the main PCB, with the tall connectors occupying
    # its existing board cutout. Local +z therefore points toward the main
    # board. The spacer spans from Zynq PCB top to main PCB underside.
    for x, y in holes:
        retained.append((tube(
            x, y, 1.6,
            ZYNQ_OFFSET_MM - MAIN_BOARD_THICKNESS_MM,
            outer_radius=3.1, hole_radius=1.7,
        ), METAL))
    write_model(ZYNQ, retained)


def make_mingwu_module():
    # Board dimensions are printed on the supplied product photograph.
    # Mount centres and component envelopes are scaled from that photograph.
    width, depth = 29.3, 18.9
    mount_centres = ((-12.45, -9.45), (-12.45, 9.45), (13.10, 0.0))
    board = box(-width / 2, -depth / 2, 6.0,
                width / 2, depth / 2, 7.6)
    for x, y, radius in ((-12.45, -9.45, 1.45),
                         (-12.45, 9.45, 1.45),
                         (13.10, 0.0, 1.20)):
        board = BRepAlgoAPI_Cut(
            board, cylinder(x, y, 5.9, 7.7, radius)
        ).Shape()

    parts = [(board, GREEN_PCB)]
    for x, y in mount_centres:
        parts.append((tube(x, y, 0.0, 6.0, 2.0, 1.25), METAL))

    # A recognisable mechanical envelope: IC, leads and tall metal crystal.
    parts.append((box(-3.1, -4.6, 7.6, 1.3, 4.6, 9.0), BLACK))
    for row in range(14):
        y = -3.575 + row * 0.55
        for x0, x1 in ((-3.7, -3.1), (1.3, 1.9)):
            parts.append((box(x0, y - 0.12, 7.6,
                              x1, y + 0.12, 7.85), METAL))
    parts.append((box(5.8, -6.0, 7.6, 11.6, 6.0, 8.2), BLACK))
    parts.append((box(6.4, -3.4, 8.2, 10.8, 3.4, 10.9), METAL))
    for y in (-3.4, 3.4):
        parts.append((cylinder(8.6, y, 8.2, 10.9, 2.2), METAL))
    for x, y in ((-9.4, -5.7), (-8.0, 3.8), (-5.6, 5.5),
                 (2.6, -5.6), (3.1, 5.9), (11.8, -6.9), (12.1, 6.8)):
        parts.append((box(x - 0.55, y - 0.35, 7.6,
                          x + 0.55, y + 0.35, 8.25), GOLD))
    write_model(MINGWU_MODULE, parts)


if __name__ == "__main__":
    make_lcd()
    make_zynq()
    make_mingwu_module()
