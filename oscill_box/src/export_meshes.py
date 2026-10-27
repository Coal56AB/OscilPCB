"""Mesh the verified native STEP solids as millimetre, closed print meshes."""
import math
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from vtkmodules.vtkIOGeometry import vtkSTLReader, vtkSTLWriter
from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkFeatureEdges, vtkTriangleFilter, vtkRemoveDuplicatePolys
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from build_case import ROOT, read_solids, bbox, transform


def main():
    names=('front','rear','power_cap','kickstand')
    parser=argparse.ArgumentParser();parser.add_argument('--only',choices=names)
    args=parser.parse_args()
    for name in ((args.only,) if args.only else names):
        source=ROOT/'output'/f'{name}.step'
        if not source.is_file():continue
        solids=read_solids(source)
        if len(solids)!=1:raise ValueError(f'{name}: expected one solid before meshing')
        shape=solids[0]
        native=ROOT/'output'/'.native-mesh'/f'{name}.stl'
        with TemporaryDirectory() as temporary:
            path=native if native.is_file() else Path(temporary)/f'{name}.stl'
            if not native.is_file():
                mesh=BRepMesh_IncrementalMesh(shape,.01,False,math.radians(5),True)
                if not mesh.IsDone():raise ValueError(f'{name}: meshing failed')
                writer=StlAPI_Writer();writer.ASCIIMode=False
                if not writer.Write(shape,str(path)):raise ValueError(f'{name}: STL export failed')
            # Binary STL stores each triangle separately as float32. Weld the
            # submicron round-off at shared edges, below the 0.01 mm chord error.
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
            if edges.GetOutput().GetNumberOfCells():
                raise ValueError(f'{name}: meshing left open/nonmanifold edges; regenerate with SolidWorks ExportPrintMeshes')
            output=Path(temporary)/'welded.stl'
            mesh_writer=vtkSTLWriter();mesh_writer.SetFileTypeToBinary()
            mesh_writer.SetFileName(str(output));mesh_writer.SetInputConnection(unique.GetOutputPort())
            if not mesh_writer.Write():raise ValueError(f'{name}: STL welding failed')
            content=output.read_bytes()
        target=ROOT/'output'/f'{name}.stl'
        if not target.is_file() or target.read_bytes()!=content:target.write_bytes(content)
        if native.is_file():native.unlink()
        print(f'STL: {target}',flush=True)


if __name__=='__main__':main()
