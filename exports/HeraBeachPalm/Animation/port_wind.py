"""Evaluate the exported UEFN wind graph for one Blender palm mesh.

Original graph, parameter values, pivot textures, and wind waveform are inputs.
Environmental storm, physics interaction, and live gust events are neutral.
"""
import bpy
import json
import numpy as np
from pathlib import Path

BASE = Path('C:/Users/tas13/Documents/GitHub/UEFN-Ducky-Release/exports/HeraBeachPalm/Animation')
GRAPH = {n['id']: n for n in json.loads((BASE/'uefn_wind_graph.json').read_text())['nodes']}
PARAMS = json.loads((BASE/'uefn_wind_parameters.json').read_text())

def array(v):
    if isinstance(v, dict): v = [v[k] for k in ['r','g','b','a'] if k in v]
    a = np.asarray(v, dtype=np.float64)
    return a.reshape(1, -1) if a.ndim < 2 else a

class WindGraph:
    def __init__(self, obj):
        self.obj = obj
        self.rest = np.array([v.co[:] for v in obj.data.vertices], dtype=float)
        self.pos = self.rest * [1,-1,1]
        self.uv = np.zeros((len(self.pos),2))
        for loop in obj.data.loops:
            u = obj.data.uv_layers[1].data[loop.index].uv
            self.uv[loop.vertex_index] = [u.x, 1-u.y]
        self.images = {}
        for name in ['SM_Hera_BeachPalm_A_Fallback_Position','SM_Hera_BeachPalm_A_Fallback_XVector','Wind_SineWaves_HDR']:
            im = bpy.data.images.load(str(BASE/(name+'.exr')), check_existing=True)
            im.colorspace_settings.name = 'Non-Color'; im.pack()
            pixels = np.empty(len(im.pixels),dtype=np.float32); im.pixels.foreach_get(pixels)
            self.images[name] = pixels.reshape(im.size[1],im.size[0],4)[::-1].astype(float)
        self.texnames = {'Position':'SM_Hera_BeachPalm_A_Fallback_Position','X-Axis':'SM_Hera_BeachPalm_A_Fallback_XVector'}
        self.used = set()

    def sample(self, name, uv):
        im = self.images[name]; h,w,_=im.shape
        if uv.shape[1] == 1: uv = np.repeat(uv,2,axis=1)
        u = np.mod(uv[:,0],1)*w - .5; v = np.mod(uv[:,1],1)*h - .5
        if name != 'Wind_SineWaves_HDR':
            return im[np.floor(v+.5).astype(int)%h, np.floor(u+.5).astype(int)%w]
        x=np.floor(u).astype(int); y=np.floor(v).astype(int)
        fx=(u-x)[:,None]; fy=(v-y)[:,None]
        return (im[y%h,x%w]*(1-fx)+im[y%h,(x+1)%w]*fx)*(1-fy)+(im[(y+1)%h,x%w]*(1-fx)+im[(y+1)%h,(x+1)%w]*fx)*fy

    def evaluate(self, ident, output=''):
        key=(ident,output)
        if key in self.cache:return self.cache[key]
        n=GRAPH[ident]; p=n['properties']; kind=ident.rsplit('_',1)[0].replace('MaterialExpression','')
        self.used.add(ident)
        ins={i['input_name']:i for i in n['inputs']}
        def value(name=None, default=0):
            if name is None: i=next(iter(ins.values()),None)
            else: i=ins.get(name)
            if not i or not i.get('expression'):return array(default)
            return self.evaluate(i['expression']['refPath'].split(':')[-1],i.get('output_name',''))
        def has(name):return bool(ins.get(name,{}).get('expression'))
        if kind=='NamedRerouteUsage':r=self.evaluate(n['declaration'])
        elif kind in ['NamedRerouteDeclaration','Reroute','FunctionOutput']:r=value()
        elif kind=='ScalarParameter':r=array(PARAMS.get(p['parameterName'],p['defaultValue']))
        elif kind=='VectorParameter':r=array(PARAMS.get(p['parameterName'],p['defaultValue']))
        elif kind=='Constant':r=array(p.get('r',0))
        elif kind=='Constant2Vector':r=array([p.get('r',0),p.get('g',0)])
        elif kind=='Constant3Vector':r=array(p['constant'])[:,:3]
        elif kind in ['StaticBoolParameter','StaticSwitchParameter']:
            b=PARAMS.get(p['parameterName'],p.get('defaultValue',False))
            r=value('True' if b else 'False') if kind=='StaticSwitchParameter' else array(b)
        elif kind=='StaticSwitch':r=value('True' if bool(value('Value',p.get('defaultValue',False))[0,0]) else 'False')
        elif kind=='QualitySwitch':r=value('Epic') if has('Epic') else value('Default')
        elif kind=='ShadingPathSwitch':r=value('Deferred') if has('Deferred') else value('Default')
        elif kind=='Time':r=array(self.time)
        elif kind=='WorldPosition':r=self.pos
        elif kind=='TextureCoordinate':r=self.uv * [p.get('uTiling',1),p.get('vTiling',1)]
        elif kind=='TextureObjectParameter':r=self.texnames[p['parameterName']]
        elif kind=='TextureProperty':
            im=self.images[value('Tex')];r=array([im.shape[1],im.shape[0]])
        elif kind in ['TextureSample','TextureSampleParameter2D']:
            name=self.texnames[p['parameterName']] if kind=='TextureSampleParameter2D' else p['texture']['refPath'].split('.')[-1]
            r=self.sample(name,value('UVs',self.uv))
        elif kind=='ComponentMask':
            a=value();idx=[i for i,c in enumerate(['r','g','b','a']) if p.get(c,False)]
            if a.shape[1]==1:r=np.repeat(a,len(idx),axis=1)
            else:r=a[:,idx]
        elif kind in ['Add','Subtract','Multiply','Divide','Max','Min']:
            a=value('A',p.get('constA',0));b=value('B',p.get('constB',1))
            if kind=='Add':r=a+b
            elif kind=='Subtract':r=a-b
            elif kind=='Multiply':r=a*b
            elif kind=='Divide':r=a/np.where(abs(b)<1.e-8,1.e-8,b)
            elif kind=='Max':r=np.maximum(a,b)
            else:r=np.minimum(a,b)
        elif kind=='AppendVector':
            a=value('A');b=value('B');cnt=max(len(a),len(b));r=np.concatenate([np.broadcast_to(a,(cnt,a.shape[1])),np.broadcast_to(b,(cnt,b.shape[1]))],axis=1)
        elif kind=='Frac':r=value()%1
        elif kind=='Abs':r=abs(value())
        elif kind=='OneMinus':r=1-value()
        elif kind=='Saturate':r=np.clip(value(),0,1)
        elif kind=='Clamp':r=np.clip(value(),value('Min',p.get('minDefault',0)),value('Max',p.get('maxDefault',1)))
        elif kind=='Normalize':
            a=value();r=a/np.maximum(np.linalg.norm(a,axis=1,keepdims=True),1.e-8)
        elif kind=='CrossProduct':r=np.cross(value('A'),value('B'))
        elif kind=='DotProduct':r=np.sum(value('A')*value('B'),axis=1,keepdims=True)
        elif kind=='Distance':r=np.linalg.norm(value('A')-value('B'),axis=1,keepdims=True)
        elif kind=='Power':r=np.maximum(value('Base'),0)**value('Exp',p.get('constExponent',2))
        elif kind=='Sine':r=np.sin(value()*2*np.pi/p.get('period',1))
        elif kind=='LinearInterpolate':
            a=value('A',p.get('constA',0));b=value('B',p.get('constB',1));alpha=value('Alpha',p.get('constAlpha',.5));r=a*(1-alpha)+b*alpha
        elif kind in ['Transform','TransformPosition']:r=value()
        elif kind=='RotateAboutAxis':
            axis=value('NormalizedRotationAxis');angle=value('RotationAngle')*2*np.pi/p.get('period',1)
            pos=value('Position');pivot=value('PivotPoint');v=pos-pivot
            r=v*np.cos(angle)+np.cross(axis,v)*np.sin(angle)+axis*np.sum(axis*v,axis=1,keepdims=True)*(1-np.cos(angle))-v
        elif kind=='CollectionParameter':
            values={'WindDirection_RGB Speed_A':[1,0,0,.004521000199019909],'WeatherWindDirectionAndStrength':[0,0,0,0]}
            if p['parameterName'] not in values:raise ValueError('Unknown collection parameter '+p['parameterName'])
            r=array(values[p['parameterName']])
        elif kind=='SamplePhysicsVectorField':r=array([0,0,0])
        elif kind=='MaterialFunctionCall':
            f=p['materialFunction']['refPath'].split('.')[-1]
            if f=='LocalPosition':r=self.pos
            elif f=='ObjectScale':r=array([1,1,1])
            elif f in ['MF_MakePrecise','MF_MakePreciseVec3']:r=value()
            elif f=='VectorLength':r=np.linalg.norm(value(),axis=1,keepdims=True)
            elif f in ['MF_StormMask','MF_WindGustsSystem']:r=array(0)
            else:raise NotImplementedError(f)
        else:raise NotImplementedError(kind+' '+ident)
        if not isinstance(r,str):
            r=array(r)
            channels={'R':[0],'G':[1],'B':[2],'A':[3],'RGB':[0,1,2],'RGBA':[0,1,2,3],'XYZ':[0,1,2]}
            if output in channels:
                idx=channels[output]
                if max(idx)<r.shape[1]:r=r[:,idx]
            elif output and kind != 'MaterialFunctionCall' and output not in ['Result','Output','Value','V3 Length','V2 Length']:raise NotImplementedError('Output '+output+' '+ident)
        self.cache[key]=r
        return r

    def deform(self, time):
        self.time=time;self.cache={}
        # The source material's final wind branch, before environmental destruction effects.
        offset=self.evaluate('MaterialExpressionShadingPathSwitch_0')
        if not np.isfinite(offset).all():raise ValueError('Non-finite deformation')
        return (self.pos+offset)*[1,-1,1]

def diagnose():
    obj=bpy.data.objects['SM_Hera_BeachPalm_A_Bake_LOD0'];g=WindGraph(obj)
    samples=[]
    for t in [0,1,5,10,20]:
        p=g.deform(t);d=np.linalg.norm(p-g.rest,axis=1)
        samples.append({'time':t,'max_offset_cm':float(d.max()),'mean_offset_cm':float(d.mean())})
    return {'samples':samples,'evaluated_nodes':len(g.used)}

def bake():
    obj=bpy.data.objects['SM_Hera_BeachPalm_A_Bake_LOD0']
    if obj.data.shape_keys: raise RuntimeError('Shape keys already exist; preserve existing animation')
    graph=WindGraph(obj)
    frames=list(range(1,602,2))
    poses=[graph.deform((f-1)/30).astype(np.float32) for f in frames]
    base=graph.rest[:,2]<100
    root_motion=max(float(np.linalg.norm(p-graph.rest,axis=1)[base].max()) for p in poses)
    assert root_motion<0.001, 'Unexpected motion at the roots'
    assert max(float(np.linalg.norm(p-poses[0],axis=1).max()) for p in poses)>1, 'No animation'
    basis=obj.shape_key_add(name='UEFN Rest Mesh',from_mix=False)
    keydata=obj.data.shape_keys
    keydata.use_relative=False
    for frame,pose in zip(frames,poses):
        key=obj.shape_key_add(name='UEFN Wind %05d'%frame,from_mix=False)
        key.data.foreach_set('co',pose.ravel())
        key.interpolation='KEY_LINEAR'
    keydata.eval_time=keydata.key_blocks[1].frame
    keydata.keyframe_insert(data_path='eval_time',frame=1)
    keydata.eval_time=keydata.key_blocks[-1].frame
    keydata.keyframe_insert(data_path='eval_time',frame=601)
    action=keydata.animation_data.action
    action.name='UEFN Hera Palm - Original Pivot Wind - 20 seconds'
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    for point in curve.keyframe_points: point.interpolation='LINEAR'
    scene=bpy.context.scene
    scene.render.fps=30;scene.frame_start=1;scene.frame_end=601
    scene.frame_set(1)
    obj['wind_source']='UEFN MF_TreeAnim_Apollo_Optimized; original pivot EXRs, waveform and instance settings'
    obj['wind_bake']='20 seconds at 30 fps; absolute shape keys sampled every two frames'
    obj['wind_environment']='Neutral storm, gust events and physics field; asset at origin; fixed captured wind collection'
    obj['wind_source_graph_nodes']=len(graph.used)
    return {'object':obj.name,'pose_samples':len(poses),'shape_keys':len(keydata.key_blocks),'frames':[1,601],'fps':30,'root_motion_cm':root_motion,'action':action.name}
