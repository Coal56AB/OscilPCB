using System;
using System.IO;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using SolidWorks.Interop.sldworks;

// Native sketches and features; dimensions in the input plan are millimetres.
public static class SolidWorksCase
{
    static ISldWorks app;
    static string partTemplate, assemblyTemplate;
    static bool forceBuild;
    static string Digest(byte[] bytes) {
        using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","");
    }
    static string TextDigest(string value) {return Digest(Encoding.UTF8.GetBytes(value.Replace("\r\n","\n")));}
    static string FileDigest(string path) {
        using(var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))
        using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(file)).Replace("-","");
    }
    static string BuilderDigest(string root,string[] methods) {
        // Methods are top-level members indented by four spaces in this file.
        // Hash only the code used to create this kind of model.
        string[] lines=File.ReadAllLines(Path.Combine(root,"src","SolidWorksCase.cs"));
        var source=new StringBuilder();
        foreach(string method in methods) {
            bool found=false,copy=false;
            foreach(string line in lines) {
                bool member=(line.StartsWith("    static ") || line.StartsWith("    public static ")) && line.Contains("(");
                if(copy && (member || line=="}"))break;
                if(member && line.Contains(" "+method+"(")){found=true;copy=true;}
                if(copy)source.AppendLine(line);
            }
            if(!found)throw new Exception("Build method missing: "+method);
        }
        return TextDigest(source.ToString());
    }
    public static string InputSignature(string root,string name) {
        var input=new StringBuilder("v2|"+name);
        if(name=="oscill_case") {
            input.Append("|"+BuilderDigest(root,new[]{"Assembly","StandState","Save"}));
            foreach(var n in new[]{"front","rear","rear_glass","panel_overlay","pcb_reference","charger_reference","encoder_caps_reference","power_cap","kickstand"})
                input.Append("|"+FileDigest(Path.Combine(root,n+".SLDPRT")));
            foreach(var n in new[]{"power_cap","kickstand"})input.Append("|"+TextDigest(File.ReadAllText(Path.Combine(root,"src",n+".pose"))));
        } else if(name.EndsWith("_reference")) {
            input.Append("|"+BuilderDigest(root,new[]{"Reference","ImportStep","Save"}));
            input.Append("|"+TextDigest(File.ReadAllText(Path.Combine(root,"src",name+".fingerprint"))));
        } else {
            input.Append("|"+BuilderDigest(root,new[]{"M","Line","SpatialLoop","LoopX","Loop","Loft","ImportStep","ImportedUnion","UnionBodies","Part","Save","HideConstruction"}));
            input.Append("|"+TextDigest(File.ReadAllText(Path.Combine(root,"src",name+".plan"))));
            foreach(string row in File.ReadAllLines(Path.Combine(root,"src",name+".plan"))) {
                var fields=row.Split('\t');
                if(fields.Length>1 && (fields[0]=="ADD_GUSSET" || fields[0]=="ADD_CORNER"))
                    input.Append("|"+TextDigest(File.ReadAllText(Path.Combine(root,"src",fields[1]+".fingerprint"))));
            }
            if(name=="panel_overlay")input.Append("|"+BuilderDigest(root,new[]{"PanelDecal"})+"|"+FileDigest(Path.Combine(root,"output","panel.png")));
        }
        return TextDigest(input.ToString());
    }
    static string StoredSignature(ModelDoc2 doc) {
        string value,resolved;
        doc.Extension.CustomPropertyManager[""].Get4("OscillBuildInput",false,out value,out resolved);
        return value;
    }
    static void Remember(ModelDoc2 doc,string signature) {
        doc.Extension.CustomPropertyManager[""].Add3("OscillBuildInput",30,signature,2);
        if(StoredSignature(doc)!=signature)throw new Exception("Cannot store build signature");
    }
    static bool Current(string root,string name,string signature,int type,bool exportCheck) {
        string path=Path.Combine(root,name+(type==2?".SLDASM":".SLDPRT"));
        if(forceBuild || !File.Exists(path))return false;
        int e=0,w=0;
        var doc=(ModelDoc2)app.OpenDoc6(path,type,1,"",ref e,ref w);
        if(doc==null || e!=0)throw new Exception("Cannot check existing model "+name+": "+e);
        if(StoredSignature(doc)!=signature){app.CloseDoc(doc.GetTitle());return false;}
        if(exportCheck) {
            app.ActivateDoc3(doc.GetTitle(),false,0,ref e);
            doc.ClearSelection2(true);
            Save(doc,Path.Combine(root,"output",name+".step"));
        }
        Console.WriteLine("UNCHANGED / "+name);
        if(type==1)app.CloseDoc(doc.GetTitle());
        return true;
    }
    static double M(string s) { return double.Parse(s, CultureInfo.InvariantCulture) / 1000.0; }
    static void Line(SketchManager sm, double x, double y, double u, double v) {
        sm.CreateLine(x,y,0,u,v,0);
    }
    static void SpatialLoop(SketchManager sm,string value,Func<double,double,double[]> map) {
        Action<double,double,double,double> line=(x,y,u,v)=>{var a=map(x,y);var b=map(u,v);sm.CreateLine(a[0],a[1],a[2],b[0],b[1],b[2]);};
        Action<double,double,double,double,double,double> arc=(x,y,u,v,p,q)=>{var a=map(x,y);var b=map(u,v);var c=map(p,q);if(sm.Create3PointArc(a[0],a[1],a[2],c[0],c[1],c[2],b[0],b[1],b[2])==null)throw new Exception("Cannot create profile arc");};
        var t=value.Split(',');var d=Array.ConvertAll(t,q=>q=="P"||q=="R"||q=="F"?0:M(q));
        if(t[0]=="F") {
            for(int i=1;i<t.Length;i+=6) {
                int j=i+6<t.Length?i+6:1;
                arc(d[i],d[i+1],d[i+2],d[i+3],d[i+4],d[i+5]);
                line(d[i+4],d[i+5],d[j],d[j+1]);
            }
            return;
        }
        if(t[0]=="P") {for(int i=1;i<t.Length;i+=2){int j=i+2<t.Length?i+2:1;line(d[i],d[i+1],d[j],d[j+1]);}return;}
        if(t[0]!="R")throw new Exception("Unsupported spatial profile "+t[0]);
        double x0=d[1],y0=d[2],w=d[3],h=d[4],r=d[5],k=Math.Sqrt(.5);
        if(r==0){line(x0,y0,x0+w,y0);line(x0+w,y0,x0+w,y0+h);line(x0+w,y0+h,x0,y0+h);line(x0,y0+h,x0,y0);return;}
        line(x0+r,y0,x0+w-r,y0);arc(x0+w-r,y0,x0+w-r+r*k,y0+r-r*k,x0+w,y0+r);
        line(x0+w,y0+r,x0+w,y0+h-r);arc(x0+w,y0+h-r,x0+w-r+r*k,y0+h-r+r*k,x0+w-r,y0+h);
        line(x0+w-r,y0+h,x0+r,y0+h);arc(x0+r,y0+h,x0+r-r*k,y0+h-r+r*k,x0,y0+h-r);
        line(x0,y0+h-r,x0,y0+r);arc(x0,y0+r,x0+r-r*k,y0+r-r*k,x0+r,y0);
    }
    static double LoopX(ModelDoc2 doc,string value) {
        var a=value.Split(',');
        var sketch=(Sketch)doc.GetActiveSketch2();
        var toSketch=(MathTransform)sketch.ModelToSketchTransform;
        var util=(MathUtility)app.GetMathUtility();
        if(a[0]=="C") {
            var p=(MathPoint)util.CreatePoint(new double[]{0,M(a[1]),M(a[2])});
            var q=(double[])((MathPoint)p.MultiplyTransform(toSketch)).ArrayData;
            doc.SketchManager.CreateCircleByRadius(q[0],q[1],0,M(a[3]));
        } else {
            SpatialLoop(doc.SketchManager,value,(y,z)=>{var p=(MathPoint)util.CreatePoint(new double[]{0,y,z});return (double[])((MathPoint)p.MultiplyTransform(toSketch)).ArrayData;});
        }
        var n=(MathPoint)util.CreatePoint(new double[]{0,0,1});
        return ((double[])((MathPoint)n.MultiplyTransform((MathTransform)toSketch.Inverse())).ArrayData)[0];
    }
    static void Loop(SketchManager sm, string value) {
        var a=value.Split(',');
        if(a[0]=="C") { sm.CreateCircleByRadius(M(a[1]),M(a[2]),0,M(a[3])); return; }
        if(a[0]=="F") {
            for(int i=1;i<a.Length;i+=6) {
                int j=i+6<a.Length?i+6:1;
                if(sm.Create3PointArc(M(a[i]),M(a[i+1]),0,M(a[i+4]),M(a[i+5]),0,M(a[i+2]),M(a[i+3]),0)==null)
                    throw new Exception("Cannot round panel contour");
                Line(sm,M(a[i+4]),M(a[i+5]),M(a[j]),M(a[j+1]));
            }
            return;
        }
        if(a[0]=="P") {
            for(int i=1;i<a.Length;i+=2) {
                int j=i+2<a.Length?i+2:1;
                Line(sm,M(a[i]),M(a[i+1]),M(a[j]),M(a[j+1]));
            }
            return;
        }
        double x=M(a[1]),y=M(a[2]),w=M(a[3]),h=M(a[4]),r=M(a[5]);
        if(a[0]=="U") {
            Line(sm,x,y,x+w,y);Line(sm,x+w,y,x+w,y+h-r);
            sm.CreateArc(x+w-r,y+h-r,0,x+w,y+h-r,0,x+w-r,y+h,0,1);
            Line(sm,x+w-r,y+h,x+r,y+h);
            sm.CreateArc(x+r,y+h-r,0,x+r,y+h,0,x,y+h-r,0,1);
            Line(sm,x,y+h-r,x,y);return;
        }
        if(r==0) {
            Line(sm,x,y,x+w,y);Line(sm,x+w,y,x+w,y+h);
            Line(sm,x+w,y+h,x,y+h);Line(sm,x,y+h,x,y);return;
        }
        Line(sm,x+r,y,x+w-r,y);
        sm.CreateArc(x+w-r,y+r,0,x+w-r,y,0,x+w,y+r,0,1);
        Line(sm,x+w,y+r,x+w,y+h-r);
        sm.CreateArc(x+w-r,y+h-r,0,x+w,y+h-r,0,x+w-r,y+h,0,1);
        Line(sm,x+w-r,y+h,x+r,y+h);
        sm.CreateArc(x+r,y+h-r,0,x+r,y+h,0,x,y+h-r,0,1);
        Line(sm,x,y+h-r,x,y+r);
        sm.CreateArc(x+r,y+r,0,x,y+r,0,x+r,y,0,1);
    }
    static void Save(ModelDoc2 doc, string path) {
        int e=0,w=0;
        if(!doc.Extension.SaveAs(path,0,1,null,ref e,ref w) || e!=0)
            throw new Exception("Save failed: "+path+" errors="+e+" warnings="+w);
    }
    public static void ExportPrintMeshes(string root,string only,string outputDirectory,double angleDegrees=5) {
        if(app==null)throw new Exception("SolidWorks build session is not connected");
        if(string.IsNullOrEmpty(outputDirectory))outputDirectory=Path.Combine(root,"output",".native-mesh");
        Directory.CreateDirectory(outputDirectory);
        int quality=app.GetUserPreferenceIntegerValue(78),units=app.GetUserPreferenceIntegerValue(211);
        double deviation=app.GetUserPreferenceDoubleValue(2),angle=app.GetUserPreferenceDoubleValue(3);
        int[] toggles={69,70,71,72,73,191};var original=new bool[toggles.Length];
        for(int i=0;i<toggles.Length;i++)original[i]=app.GetUserPreferenceToggle(toggles[i]);
        var active=(ModelDoc2)app.IActiveDoc2;string activeTitle=active==null?null:active.GetTitle();
        try {
            app.SetUserPreferenceIntegerValue(78,3);app.SetUserPreferenceIntegerValue(211,0);
            app.SetUserPreferenceDoubleValue(2,.00001);app.SetUserPreferenceDoubleValue(3,angleDegrees*Math.PI/180);
            bool[] values={true,false,true,true,false,false};
            for(int i=0;i<toggles.Length;i++)app.SetUserPreferenceToggle(toggles[i],values[i]);
            foreach(string name in new[]{"front","rear","power_cap","kickstand"}) {
                if(!string.IsNullOrEmpty(only) && only!=name)continue;
                string path=Path.Combine(root,name+".SLDPRT");int e=0,w=0;
                byte[] stamp=PrintMeshStamp(root,name,angleDegrees);
                string target=Path.Combine(root,"output",name+".stl");
                if(File.Exists(target)) {
                    using(var reader=new BinaryReader(File.OpenRead(target))) {
                        if(reader.BaseStream.Length>=84) {
                            byte[] saved=reader.ReadBytes(80);uint triangles=reader.ReadUInt32();
                            bool current=reader.BaseStream.Length==84L+50L*triangles;
                            for(int k=0;k<80;k++)current&=saved[k]==stamp[k];
                            if(current){
                                string stale=Path.Combine(outputDirectory,name+".stl");
                                if(File.Exists(stale))File.Delete(stale);
                                Console.WriteLine("UNCHANGED STL / "+name);continue;
                            }
                        }
                    }
                }
                var doc=(ModelDoc2)app.GetOpenDocumentByName(path);bool visible=doc!=null&&doc.Visible;
                if(doc==null)doc=(ModelDoc2)app.OpenDoc6(path,1,1,"",ref e,ref w);
                if(doc==null)throw new Exception("Cannot export print mesh "+name+": "+e);
                app.ActivateDoc3(doc.GetTitle(),false,0,ref e);doc.ClearSelection2(true);
                string mesh=Path.Combine(outputDirectory,name+".stl");
                Save(doc,mesh);
                using(var stream=new FileStream(mesh,FileMode.Open,FileAccess.Write))stream.Write(stamp,0,stamp.Length);
                if(!visible)app.CloseDoc(doc.GetTitle());
                Console.WriteLine("NATIVE STL / "+name);
            }
        } finally {
            app.SetUserPreferenceIntegerValue(78,quality);app.SetUserPreferenceIntegerValue(211,units);
            app.SetUserPreferenceDoubleValue(2,deviation);app.SetUserPreferenceDoubleValue(3,angle);
            for(int i=0;i<toggles.Length;i++)app.SetUserPreferenceToggle(toggles[i],original[i]);
            if(activeTitle!=null){int e=0;app.ActivateDoc3(activeTitle,false,0,ref e);}
        }
    }
    static void HideConstruction(ModelDoc2 doc) {
        doc.ClearSelection2(true);
        for(var f=(Feature)doc.FirstFeature();f!=null;f=(Feature)f.GetNextFeature())
            if(f.GetTypeName2()=="RefPlane")f.Select2(true,0);
        doc.BlankRefGeom();
        doc.ClearSelection2(true);
    }
    static void PanelDecal(ModelDoc2 doc,string root) {
        Face2 face=null;double top=double.MinValue;
        foreach(Body2 body in (object[])((PartDoc)doc).GetBodies2(0,false))foreach(Face2 f in (object[])body.GetFaces()) {
            var b=(double[])f.GetBox();if(Math.Abs(b[5]-b[2])<1e-7 && b[2]>top){face=f;top=b[2];}
        }
        if(face==null)throw new Exception("Film face missing");
        var decal=(Decal)doc.Extension.CreateDecal();var material=(RenderMaterial)decal;
        if(!material.AddEntity(face))throw new Exception("Cannot assign film decal");
        material.FileName=Path.Combine(root,"output","panel-decal.p2d");
        material.TextureFilename=Path.Combine(root,"output","panel.png");
        material.MappingType=0;material.FixedAspectRatio=false;material.FitWidth=true;material.FitHeight=true;
        var bounds=(double[])face.GetBox();material.Width=bounds[3]-bounds[0];material.Height=bounds[4]-bounds[1];
        material.SetCenterPoint2((bounds[0]+bounds[3])/2,(bounds[1]+bounds[4])/2,top);
        material.SetUDirection2(1,0,0);material.SetVDirection2(0,1,0);
        int id=0;if(!doc.Extension.AddDecal(decal,out id))throw new Exception("Cannot add film decal");
        doc.Extension.HideDecal(id,false);doc.Extension.SetUserPreferenceToggle(662,0,true);
        doc.Extension.SetUserPreferenceToggle(438,0,true); // Store images with model.
        doc.GraphicsRedraw2();
        Console.WriteLine("Film artwork decal / "+doc.Extension.GetDecalsCount());
    }
    static Feature Loft(ModelDoc2 doc,string name,double lo,double hi,string[] loops,bool axisX,bool add,bool rootBlend=false) {
        var profiles=new Feature[loops.Length];
        for(int k=0;k<loops.Length;k++) {
            Feature basePlane=null;int planeIndex=0;
            for(var f=(Feature)doc.FirstFeature();f!=null;f=(Feature)f.GetNextFeature())if(f.GetTypeName2()=="RefPlane" && ++planeIndex==(axisX?3:1)){basePlane=f;break;}
            double t=(double)k/(loops.Length-1);
            double x=lo+(hi-lo)*(rootBlend?Math.Sin(t*Math.PI/2):t);
            doc.ClearSelection2(true);basePlane.Select2(false,0);
            var plane=(RefPlane)doc.FeatureManager.InsertRefPlane(8|(x<0?256:0),Math.Abs(x),0,0,0,0);
            if(plane==null)throw new Exception("Cannot create loft plane");
            var planeFeature=(Feature)plane;planeFeature.Name=name+"_plane_"+k;
            doc.ClearSelection2(true);planeFeature.Select2(false,0);doc.SketchManager.InsertSketch(true);
            doc.SketchManager.AddToDB=true;doc.SketchManager.DisplayWhenAdded=false;
            var sketch=(Sketch)doc.GetActiveSketch2();var toSketch=(MathTransform)sketch.ModelToSketchTransform;var util=(MathUtility)app.GetMathUtility();
            SpatialLoop(doc.SketchManager,loops[k],(y,z)=>{
                var p=(MathPoint)util.CreatePoint(axisX?new double[]{x,y,z}:new double[]{y,z,x});
                var q=(double[])((MathPoint)p.MultiplyTransform(toSketch)).ArrayData;
                if(Math.Abs(q[2])>1e-8)throw new Exception("Loft plane offset incorrect "+q[2]);q[2]=0;return q;
            });
            doc.SketchManager.InsertSketch(true);
            profiles[k]=(Feature)doc.FeatureByPositionReverse(0);
            profiles[k].Name=name+"_section_"+k;
        }
        doc.ClearSelection2(true);
        foreach(var p in profiles)p.Select2(true,1);
        if(add)return doc.FeatureManager.InsertProtrusionBlend2(false,false,false,1,0,0,0,0,false,false,false,0,0,0,true,true,true,0);
        return doc.FeatureManager.InsertCutBlend(false,false,false,0,0,0,false,0,0,0,true,true);
    }
    static ModelDoc2 ImportStep(string path) {
        bool interconnect=app.GetUserPreferenceToggle(691);
        bool multiBody=app.GetUserPreferenceToggle(220);
        string defaultPart=app.GetUserPreferenceStringValue(8);
        string defaultAssembly=app.GetUserPreferenceStringValue(9);
        ModelDoc2 doc;int e=0;
        try {
            app.SetUserPreferenceToggle(691,false);
            app.SetUserPreferenceToggle(220,true);
            app.SetUserPreferenceStringValue(8,partTemplate);
            app.SetUserPreferenceStringValue(9,assemblyTemplate);
            var data=(ImportStepData)app.GetImportFileData(path);
            if(data!=null)data.MapConfigurationData=false;
            doc=(ModelDoc2)app.LoadFile4(path,"",data,ref e);
        } finally {
            app.SetUserPreferenceToggle(691,interconnect);
            app.SetUserPreferenceToggle(220,multiBody);
            app.SetUserPreferenceStringValue(8,defaultPart);
            app.SetUserPreferenceStringValue(9,defaultAssembly);
        }
        if(doc==null)throw new Exception("Cannot import STEP "+path+": "+e);
        return doc;
    }
    static Feature ImportedUnion(ModelDoc2 target,string file,string name) {
        var existing=(Feature)((PartDoc)target).FeatureByName(name+"_solid");
        if(existing!=null)return UnionBodies(target);
        Body2 copy=null;ModelDoc2 imported=null;
        app.DocumentVisible(false,1);
        try {
            imported=ImportStep(file);
            var bodies=(object[])((PartDoc)imported).GetBodies2(0,false);
            if(bodies==null || bodies.Length!=1)throw new Exception("Expected one gusset solid "+name);
            copy=(Body2)((Body2)bodies[0]).Copy();
        } finally {
            if(imported!=null)app.CloseDoc(imported.GetTitle());
            app.DocumentVisible(true,1);
        }
        int activation=0;app.ActivateDoc3(target.GetTitle(),false,0,ref activation);
        var parent=(Body2)((object[])((PartDoc)target).GetBodies2(0,false))[0];
        var inserted=(Feature)((PartDoc)target).CreateFeatureFromBody3(copy,false,0);
        if(inserted==null)throw new Exception("Cannot embed gusset "+name);
        inserted.Name=name+"_solid";
        var faces=(object[])inserted.GetFaces();
        Body2 tool=faces==null?null:(Body2)((Face2)faces[0]).GetBody();
        if(tool==null)throw new Exception("Embedded gusset body missing "+name);
        target.ClearSelection2(true);
        return UnionBodies(target);
    }
    static Feature UnionBodies(ModelDoc2 target) {
        target.SetAddToDB(false);target.ClearSelection2(true);
        foreach(Body2 body in (object[])((PartDoc)target).GetBodies2(0,false)) {
            if(!target.Extension.SelectByID2(body.Name,"SOLIDBODY",0,0,0,true,2,null,0))
                throw new Exception("Cannot select body for union "+body.Name);
        }
        var feature=target.FeatureManager.InsertCombineFeature(15903,null,null);
        target.SetAddToDB(true);return feature;
    }
    static void Part(string root,string name) {
        string signature=InputSignature(root,name);
        if(Current(root,name,signature,1,true))return;
        var openAssembly=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,"oscill_case.SLDASM"));
        if(openAssembly!=null)app.CloseDoc(openAssembly.GetTitle());
        var openPart=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,name+".SLDPRT"));
        if(openPart!=null)app.CloseDoc(openPart.GetTitle());
        var doc=(ModelDoc2)app.NewDocument(partTemplate,0,0,0);
        if(doc==null) throw new Exception("Cannot create part");
        try {
        Feature plane=(Feature)doc.FirstFeature();
        while(plane!=null && plane.GetTypeName2()!="RefPlane") plane=(Feature)plane.GetNextFeature();
        if(plane==null)throw new Exception("Template has no plane");
        string planeName=plane.Name;
        string rightPlaneName=null;int planeIndex=0;
        for(var q=(Feature)doc.FirstFeature();q!=null;q=(Feature)q.GetNextFeature()) {
            if(q.GetTypeName2()=="RefPlane" && ++planeIndex==3) {rightPlaneName=q.Name;break;}
        }
        doc.SetAddToDB(true);
        foreach(string row in File.ReadAllLines(Path.Combine(root,"src",name+".plan"))) {
            if(string.IsNullOrWhiteSpace(row))continue;
            var f=row.Split('\t');
            double lo=M(f[2]),hi=M(f[3]);
            if(f[0]=="ADD_GUSSET" || f[0]=="ADD_CORNER") {
                var gusset=ImportedUnion(doc,Path.Combine(root,"src",f[1]+".step"),f[1]);
                if(gusset==null)throw new Exception("Cannot join gusset "+f[1]);
                gusset.Name=f[1];Console.WriteLine(name+" / "+f[1]);continue;
            }
            if(f[0]=="FILLET") {
                doc.ClearSelection2(true);var targets=f[4].Split(';');
                var edges=new Edge[0];
                foreach(Body2 body in (object[])((PartDoc)doc).GetBodies2(0,false)) {
                    var next=(object[])body.GetEdges();int offset=edges.Length;
                    Array.Resize(ref edges,offset+next.Length);
                    for(int k=0;k<next.Length;k++)edges[offset+k]=(Edge)next[k];
                }
                var selected=new Edge[targets.Length];int selectedCount=0;
                foreach(var target in targets) {
                    var t=target.Split(',');Edge best=null;double distance=double.MaxValue;
                    foreach(Edge edge in edges) {
                        var mid=(double[])edge.GetClosestPointOn(M(t[1]),M(t[2]),M(t[3]));
                        double d=0;for(int k=0;k<3;k++)d+=Math.Pow(mid[k]-M(t[k+1]),2);
                        if(d<distance){distance=d;best=edge;}
                    }
                    if(best==null || distance>=1e-14)throw new Exception("Cannot locate fillet edge "+f[1]+": "+target);
                    // Parasolid may merge tangent edges split by OpenCascade.
                    if(Array.IndexOf(selected,best)<0) {
                        selected[selectedCount++]=best;
                        if(!((Entity)best).Select4(true,null))throw new Exception("Cannot select round edge");
                    }
                }
                var round=(Feature)doc.FeatureManager.FeatureFillet3(3,lo,0,0,0,0,0,null,null,null,null,null,null,null);
                if(round==null)throw new Exception("Fillet failed "+f[1]);round.Name=f[1];Console.WriteLine(name+" / "+f[1]);continue;
            }
            if(f[0].EndsWith("_LOFT") || f[0].EndsWith("_BLEND")) {
                var cut=Loft(doc,f[1],lo,hi,f[4].Split(';'),f[0].Contains("_X"),f[0].StartsWith("ADD"),f[0].EndsWith("_BLEND"));
                if(cut==null)throw new Exception(name+": loft failed: "+f[1]);
                cut.Name=f[1];Console.WriteLine(name+" / "+f[1]);continue;
            }
            bool axisX=f[0].Contains("_X");double normal=1;
            doc.ClearSelection2(true);
            if(!doc.Extension.SelectByID2(axisX?rightPlaneName:planeName,"PLANE",0,0,0,false,0,null,0))
                throw new Exception("Cannot select plane");
            doc.SketchManager.InsertSketch(true);
            doc.SketchManager.AddToDB=true;
            doc.SketchManager.DisplayWhenAdded=false;
            foreach(var loop in f[4].Split(';')) {
                if(axisX)normal=LoopX(doc,loop);else Loop(doc.SketchManager,loop);
            }
            doc.SketchManager.InsertSketch(true);
            var sf=(Feature)doc.FeatureByPositionReverse(0);
            sf.Name=f[1]+"_profile";
            doc.ClearSelection2(true);
            sf.Select2(false,0);
            Feature feature;
            if(f[0].StartsWith("ADD")) {
                feature=doc.FeatureManager.FeatureExtrusion3(true,false,axisX&&normal<0,0,0,hi-lo,0,
                    false,false,false,false,0,0,false,false,false,false,true,true,true,
                    3,Math.Abs(lo),lo*normal<0);
            } else if(axisX || f[0].Contains("TAPER")) {
                bool taper=f[0].Contains("TAPER");
                double angle=taper?(axisX?Math.Atan(2.0/2.8):Math.Atan(2)):0;
                feature=doc.FeatureManager.FeatureCut4(true,false,axisX?normal>0:true,0,0,hi-lo,0,
                    taper,false,true,false,angle,0,false,false,false,false,false,true,true,
                    false,false,false,3,Math.Abs(lo),lo*normal<0,false);
            } else {
                feature=doc.FeatureManager.FeatureCut4(true,false,false,0,0,hi-lo,0,
                    false,false,false,false,0,0,false,false,false,false,false,true,true,
                    false,false,false,3,Math.Abs(hi),hi<0,false);
            }
            if(feature==null)throw new Exception(name+": feature failed: "+f[1]);
            feature.Name=f[1];
            Console.WriteLine(name+" / "+f[1]);
        }
        doc.SetAddToDB(false);
        doc.EditRebuild3();
        var bodies=(object[])((PartDoc)doc).GetBodies2(0,false);
        if(bodies==null||bodies.Length!=1)throw new Exception(name+": expected one solid");
        doc.MaterialPropertyValues=name=="rear_glass"?new double[]{.7,.85,.9,.2,.8,.35,.3,.65,0}:name=="power_cap"
            ?new double[]{.18,.65,.72,.2,.8,.25,.3,0,0}
            :new double[]{.12,.18,.22,.2,.8,.25,.3,0,0};
        if(name=="panel_overlay")PanelDecal(doc,root);
        HideConstruction(doc);
        doc.ShowNamedView2("",7);doc.ViewZoomtofit2();
        // A restored or reopened assembly can reload the old target while the
        // replacement is being built. Release that reference before overwriting.
        openAssembly=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,"oscill_case.SLDASM"));
        if(openAssembly!=null)app.CloseDoc(openAssembly.GetTitle());
        openPart=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,name+".SLDPRT"));
        if(openPart!=null && openPart.GetTitle()!=doc.GetTitle())app.CloseDoc(openPart.GetTitle());
        Remember(doc,signature);
        Save(doc,Path.Combine(root,name+".SLDPRT"));
        Save(doc,Path.Combine(root,"output",name+".step"));
        int saveError=0,saveWarning=0;
        if(!doc.Save3(1,ref saveError,ref saveWarning) || saveError!=0)
            throw new Exception("Cannot save native part after export "+name+": "+saveError);
        } finally {doc.SetAddToDB(false);app.CloseDoc(doc.GetTitle());}
    }
    static void Reference(string root,string name) {
        string signature=InputSignature(root,name);
        if(Current(root,name,signature,1,false))return;
        var openAssembly=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,"oscill_case.SLDASM"));
        if(openAssembly!=null)app.CloseDoc(openAssembly.GetTitle());
        var openPart=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,name+".SLDPRT"));
        if(openPart!=null)app.CloseDoc(openPart.GetTitle());
        int e=0;
        string path=Path.Combine(root,"src",name+".step");
        var doc=ImportStep(path);
        Save(doc,Path.Combine(root,name+".SLDPRT"));
        app.CloseDoc(doc.GetTitle());
        // An imported multi-body STEP may be an assembly. Save-as-part does
        // not retain its document properties, so stamp the actual native part.
        int w=0;
        doc=(ModelDoc2)app.OpenDoc6(Path.Combine(root,name+".SLDPRT"),1,1,"",ref e,ref w);
        if(doc==null || e!=0)throw new Exception("Cannot stamp native reference "+name+": "+e);
        Remember(doc,signature);
        if(!doc.Save3(1,ref e,ref w) || e!=0)throw new Exception("Cannot save native reference "+name+": "+e);
        app.CloseDoc(doc.GetTitle());
    }
    static void StandState(ModelDoc2 doc,AssemblyDoc assy,bool folded) {
        // Reacquire configuration-specific component handles after switching.
        foreach(Component2 c in (object[])assy.GetComponents(true)) {
            if(!c.Name2.StartsWith("kickstand-"))continue;
            int state=(c.Name2.EndsWith("-2")==folded)?2:0;
            c.SetSuppression2(state);
            if(c.GetSuppression()!=state)throw new Exception("Cannot set stand configuration: "+c.Name2);
        }
        doc.ForceRebuild3(false);doc.GraphicsRedraw2();
    }
    static void Assembly(string root) {
        string signature=InputSignature(root,"oscill_case");
        if(Current(root,"oscill_case",signature,2,false))return;
        var previous=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,"oscill_case.SLDASM"));
        if(previous!=null)app.CloseDoc(previous.GetTitle());
        var doc=(ModelDoc2)app.NewDocument(assemblyTemplate,0,0,0);
        if(doc==null)throw new Exception("Cannot create assembly");
        bool complete=false;
        try {
        var assy=(AssemblyDoc)doc;
        app.DocumentVisible(false,1);
        foreach(string name in new[]{"front","rear","rear_glass","panel_overlay","pcb_reference","charger_reference","encoder_caps_reference","power_cap","kickstand_open","kickstand_folded"}) {
            Console.WriteLine("Assembly / "+name);
            string sourceName=name.StartsWith("kickstand_")?"kickstand":name;
            string path=Path.Combine(root,sourceName+".SLDPRT");
            int e=0,w=0;
            var source=app.OpenDoc6(path,1,1,"",ref e,ref w);
            if(source==null) {
                app.DocumentVisible(true,1);
                source=app.OpenDoc6(path,1,1,"",ref e,ref w);
                app.DocumentVisible(false,1);
            }
            if(source==null)throw new Exception("Cannot load assembly component "+name+" error="+e+" warning="+w);
            app.ActivateDoc3(doc.GetTitle(),false,0,ref e);
            var comp=(Component2)assy.AddComponent5(path,0,"",false,"",0,0,0);
            if(comp==null)throw new Exception("Cannot insert "+name);
            double[] t={1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0};
            if(name=="power_cap" || name=="kickstand_open") {
                // Local X -> world Y, Y -> Z, Z -> X.
                var cells=File.ReadAllText(Path.Combine(root,"src",sourceName+".pose")).Trim().Split(',');
                t=Array.ConvertAll(cells,v=>double.Parse(v,CultureInfo.InvariantCulture));
                for(int i=9;i<=11;i++)t[i]/=1000.0;
            }
            var util=(MathUtility)app.GetMathUtility();
            comp.Transform2=(MathTransform)util.CreateTransform(t);
            doc.ClearSelection2(true);comp.Select4(false,null,false);assy.FixComponent();
        }
        Console.WriteLine("Assembly / configurations");
        doc.ConfigurationManager.ActiveConfiguration.Name="Разложена";
        if(doc.ConfigurationManager.AddConfiguration2("Сложена","","",0,"","",false)==null)
            throw new Exception("Cannot create folded stand configuration");
        doc.ShowConfiguration2("Сложена");
        Console.WriteLine("Assembly / folded pose");
        StandState(doc,assy,true);
        doc.ShowConfiguration2("Разложена");
        Console.WriteLine("Assembly / open pose");
        StandState(doc,assy,false);
        doc.EditRebuild3();doc.ShowNamedView2("",1);doc.ViewZoomtofit2();
        // Components remain loaded by the assembly; close their separate tabs.
        foreach(string name in new[]{"front","rear","rear_glass","panel_overlay","pcb_reference","charger_reference","encoder_caps_reference","power_cap","kickstand"}) {
            var componentDoc=(ModelDoc2)app.GetOpenDocumentByName(Path.Combine(root,name+".SLDPRT"));
            if(componentDoc!=null)app.CloseDoc(componentDoc.GetTitle());
        }
        doc.SetUserPreferenceToggle(5,false);
        doc.SetUserPreferenceToggle(6,false);
        doc.SetUserPreferenceToggle(196,false);
        doc.Extension.SetUserPreferenceToggle(662,0,true);
        doc.Extension.SetUserPreferenceToggle(438,0,true);
        Remember(doc,signature);
        Save(doc,Path.Combine(root,"oscill_case.SLDASM"));
        complete=true;
        } finally {if(!complete)app.CloseDoc(doc.GetTitle());}
    }
    public static string SuspendCam() {
        if(app==null)app=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
        string path=Path.GetFullPath(Path.Combine(app.GetExecutablePath(),"..","SOLIDWORKS CAM","cwaddinu.dll"));
        if(!File.Exists(path))return "";
        int result=app.UnloadAddIn(path);
        if(result==0)return path;
        if(result==1)return "";
        throw new Exception("Cannot suspend CAM add-in: "+result);
    }
    public static void RestoreCam(string path) {
        if(string.IsNullOrEmpty(path))return;
        int result=app.LoadAddIn(path);
        if(result!=0 && result!=2)throw new Exception("Cannot restore CAM add-in: "+result);
    }
    static byte[] PrintMeshStamp(string root,string name,double angleDegrees) {
        string key=FileDigest(Path.Combine(root,name+".SLDPRT"))+"|"+
            angleDegrees.ToString("R",CultureInfo.InvariantCulture)+"|"+
            BuilderDigest(root,new[]{"ExportPrintMeshes","Save","PrintMeshStamp"})+"|"+
            TextDigest(File.ReadAllText(Path.Combine(root,"src","export_meshes.py")));
        var stamp=new byte[80];byte[] value=Encoding.ASCII.GetBytes("OscillMesh:"+TextDigest(key));
        Array.Copy(value,stamp,value.Length);return stamp;
    }
    public static void Build(string root,string templates,string only,bool force=false) {
        forceBuild=force;
        app=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
        app.Visible=true;
        partTemplate=Path.Combine(templates,"gost-part.prtdot");
        assemblyTemplate=Path.Combine(templates,"gost-assy.asmdot");
        app.CommandInProgress=true;
        try {
            if(only=="assembly") {Assembly(root);return;}
            if(only=="references") {foreach(var n in new[]{"pcb_reference","charger_reference","encoder_caps_reference"})Reference(root,n);return;}
            if(only=="pcb_reference" || only=="charger_reference" || only=="encoder_caps_reference") {Reference(root,only);return;}
            if(only!="") {Part(root,only);return;}
            foreach(var n in new[]{"front","rear","power_cap","kickstand","rear_glass","panel_overlay"})Part(root,n);
            foreach(var n in new[]{"pcb_reference","charger_reference","encoder_caps_reference"})Reference(root,n);
            Assembly(root);
        } finally {app.DocumentVisible(true,1);app.CommandInProgress=false;}
    }
}
