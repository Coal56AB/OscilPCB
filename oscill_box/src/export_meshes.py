"""Mesh the verified native STEP solids as millimetre, closed print meshes."""
import math
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.BRepCheck import BRepCheck_Analyzer
from vtkmodules.vtkIOGeometry import vtkSTLReader, vtkSTLWriter
from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkFeatureEdges, vtkTriangleFilter, vtkRemoveDuplicatePolys
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from build_case import ROOT, read_solids


def mesh_solid(shape,path):
    mesh=BRepMesh_IncrementalMesh(shape,.01,False,math.radians(5),True)
    if not mesh.IsDone():raise ValueError('Solid meshing failed')
    writer=StlAPI_Writer();writer.ASCIIMode=False
    if not writer.Write(shape,str(path)):raise ValueError('STL export failed')


def weld_mesh(path):
    # Binary STL duplicates float32 vertices. Weld only the submicron
    # round-off, well below the mesher's 0.01 mm chord error.
    reader=vtkSTLReader();reader.SetFileName(str(path));reader.Update()
    bounds=reader.GetOutput().GetBounds()
    placement=vtkTransform();placement.Translate(-bounds[0],-bounds[2],-bounds[4])
    placed=vtkTransformPolyDataFilter();placed.SetInputConnection(reader.GetOutputPort());placed.SetTransform(placement)
    clean=vtkCleanPolyData();clean.SetInputConnection(placed.GetOutputPort())
    clean.SetToleranceIsAbsolute(True);clean.SetAbsoluteTolerance(.0001)
    clean.ConvertPolysToLinesOn();clean.ConvertLinesToPointsOn()
    triangles=vtkTriangleFilter();triangles.SetInputConnection(clean.GetOutputPort())
    triangles.PassLinesOff();triangles.PassVertsOff()
    unique=vtkRemoveDuplicatePolys();unique.SetInputConnection(triangles.GetOutputPort())
    edges=vtkFeatureEdges();edges.SetInputConnection(unique.GetOutputPort())
    edges.BoundaryEdgesOn();edges.NonManifoldEdgesOn();edges.FeatureEdgesOff();edges.ManifoldEdgesOff();edges.Update()
    return unique.GetOutput(),edges.GetOutput().GetNumberOfCells()


def main():
    names=('front','rear','power_cap','kickstand')
    parser=argparse.ArgumentParser();parser.add_argument('--only',choices=names)
    parser.add_argument('--native',action='store_true',help='Consume stamped SolidWorks exports; reuse the meshes accepted by its cache')
    args=parser.parse_args()
    for name in ((args.only,) if args.only else names):
        source=ROOT/'output'/f'{name}.step'
        native=ROOT/'output'/'.native-mesh'/f'{name}.stl'
        target=ROOT/'output'/f'{name}.stl'
        if args.native and not native.is_file():
            if target.is_file():
                with target.open('rb') as saved:
                    if saved.read(80).startswith(b'OscillMesh:'):continue
            raise ValueError(f'{name}: expected a stamped native mesh')
        solids=read_solids(source)
        if len(solids)!=1:raise ValueError(f'{name}: expected one solid before meshing')
        shape=solids[0]
        with TemporaryDirectory() as temporary:
            path=native if args.native else Path(temporary)/f'{name}.stl'
            if not args.native:mesh_solid(shape,path)
            polydata,bad_edges=weld_mesh(path)
            if bad_edges and args.native:
                # Native STL can contain folded triangles at blended junctions.
                # Re-tessellate the actual SolidWorks STEP, never the independent
                # source construction, and keep the same closure/volume checks.
                if not BRepCheck_Analyzer(shape).IsValid():raise ValueError(f'{name}: invalid native CAD solid')
                fallback=Path(temporary)/'native-step.stl'
                mesh_solid(shape,fallback)
                polydata,bad_edges=weld_mesh(fallback)
                print(f'REMESH NATIVE STEP: {name}',flush=True)
            if bad_edges:raise ValueError(f'{name}: meshing left {bad_edges} open/nonmanifold edges')
            output=Path(temporary)/'welded.stl'
            mesh_writer=vtkSTLWriter();mesh_writer.SetFileTypeToBinary()
            mesh_writer.SetFileName(str(output));mesh_writer.SetInputData(polydata)
            if not mesh_writer.Write():raise ValueError(f'{name}: STL welding failed')
            content=output.read_bytes()
            if args.native:
                with path.open('rb') as raw:content=raw.read(80)+content[80:]
        if not target.is_file() or target.read_bytes()!=content:target.write_bytes(content)
        if args.native:native.unlink()
        print(f'STL: {target}',flush=True)


if __name__=='__main__':main()
