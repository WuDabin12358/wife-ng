"""Render a measured horizontal layout slice, with room and route annotations.

This is a geometry diagram, not a Minecraft screenshot or a quality grade.
"""
import argparse
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from preview_font import load_font

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from BuildingBlueprint import compile_blueprint


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--blueprint-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--survey-file',type=Path)
    parser.add_argument('--foot-y',type=int,default=5)
    args=parser.parse_args()
    saved=json.loads(args.blueprint_file.read_text(encoding='utf-8'))
    compiled,voxels=compile_blueprint(saved['blueprint'],interior_validation_version=saved.get('interior_validation_version',1))
    scope='保存方案；斜线区域为该层未指定格'
    if args.survey_file:
        survey=json.loads(args.survey_file.read_text(encoding='utf-8'))
        if not survey.get('complete') or survey.get('unknown_cells'):
            raise ValueError('Diagram requires a complete exact survey')
        voxels={}
        for row in survey['rows']:
            for y in range(row['y'],row.get('y_to',row['y'])+1):
                for z in range(row['z'],row.get('z_to',row['z'])+1):
                    for start,end,index in row['runs']:
                        for x in range(start,end+1): voxels[x,y,z]=survey['palette'][index]
        scope='实机精确扫描 '+survey['captured_at']+'；房间标注取自保存方案'
    lo,hi=compiled['summary']['bounds']['from'],compiled['summary']['bounds']['to']
    scale=22; margin=42; top=124
    width=max(960,(hi[0]-lo[0]+1)*scale+margin*2)
    height=(hi[2]-lo[2]+1)*scale+top+margin+130
    canvas=Image.new('RGB',(width,height),'#faf8f4'); draw=ImageDraw.Draw(canvas)
    font=load_font(14)
    titlefont=load_font(20)
    def color(state):
        if not state:return '#eeeae4'
        block=state.split('[',1)[0]
        if block=='minecraft:air':return '#f3f0e9'
        if 'glass' in block:return '#79c6d3'
        if block.endswith(('_log','_wood')):return '#584531'
        if 'terracotta' in block or 'concrete' in block:return '#d8c1ac'
        if 'planks' in block:return '#bb9169'
        if 'leaves' in block or 'grass' in block:return '#719864'
        if 'water' in block:return '#75a6cc'
        if any(t in block for t in ('stairs','slab','bed','barrel','bookshelf','lantern','bamboo')):return '#bd8050'
        return '#acb3b6'
    def screen(x,z):return margin+(x-lo[0])*scale,top+(z-lo[2])*scale
    for z in range(lo[2],hi[2]+1):
        for x in range(lo[0],hi[0]+1):
            state=voxels.get((x,args.foot_y,z))
            floor=voxels.get((x,args.foot_y-1,z))
            tile=floor if state=='minecraft:air' else state
            sx,sz=screen(x,z)
            tile_color='#735233' if state and state!='minecraft:air' and 'planks' in state else color(tile)
            draw.rectangle((sx,sz,sx+scale-1,sz+scale-1),fill=tile_color,outline='#ddd8cf')
            if state is None:
                draw.line((sx,sz+scale-1,sx+scale-1,sz),fill='#b7aea1')
    for room in compiled['summary'].get('rooms',[]):
        if room['from'][1]!=args.foot_y:continue
        a,b=room['from'],room['to']; sx,sz=screen(a[0],a[2]); ex,ez=screen(b[0]+1,b[2]+1)
        draw.rectangle((sx,sz,ex-1,ez-1),outline='#246790',width=3)
        draw.text((sx+3,sz+3),room['name'],fill='#153749',font=font,stroke_width=1,stroke_fill='#faf8f4')
        ex,ez=screen(room['entry_world'][0],room['entry_world'][2])
        draw.rectangle((ex+3,ez+3,ex+scale-4,ez+scale-4),outline='#f4db48',width=3)
    for route in (compiled['summary'].get('site_layout') or {}).get('routes',[]):
        points=[(screen(p[0],p[2])[0]+scale//2,screen(p[0],p[2])[1]+scale//2) for p in route['path_world']]
        draw.line(points,fill='#bf364c',width=3)
    draw.text((margin,20),compiled['summary']['name']+' · 平面切片 y='+str(args.foot_y),fill='#26343b',font=titlefont)
    draw.text((margin,54),scope,fill='#58616a',font=font)
    draw.text((margin,76),'北 ↑；横轴为世界 x，纵轴为世界 z；每格一个方块。',fill='#58616a',font=font)
    for x in range(lo[0],hi[0]+1,5):draw.text((screen(x,lo[2])[0],top-20),str(x),fill='#58616a',font=font)
    for z in range(lo[2],hi[2]+1,5):draw.text((8,screen(lo[0],z)[1]),str(z),fill='#58616a',font=font)
    bottom=top+(hi[2]-lo[2]+1)*scale+20
    for i,text in enumerate(['蓝框：室内占地；黄框：声明入口；红线：已校验路线。',
                             '白/米：墙体；深棕：柱木；浅棕：木地板；灰：石铺地；青：玻璃。',
                             '该图呈现真实格点关系，不能代替游戏视角的风格与比例验收。']):
        draw.text((margin,bottom+i*24),text,fill='#58616a',font=font)
    args.output.parent.mkdir(parents=True,exist_ok=True);canvas.save(args.output)
    print(args.output.resolve())


if __name__=='__main__':main()
