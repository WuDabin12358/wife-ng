"""Ask the real model to design against a synthetic flat-world survey.

Never connects to Minecraft or writes game state. Output is an inspectable
blueprint and exact projections, not evidence of completed world construction.
"""
import argparse
import json
import sys
import time
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from BuildingBlueprint import BlueprintStore
from BuildingContext import BuildingContext, output_budget
from WifeAgent import MODEL, SYSTEM_PROMPT, make_tools, model_completion, read_api_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='headlessmc/architecture/offline-acceptance')
    parser.add_argument('--request', default='在创造模式设计一栋现代庭院别墅，约25×25格范围，有主次体量、落地窗、入口步道、露台、室内家具、照明和景观。请自主设计完整细节，不要固定方盒小屋。')
    parser.add_argument('--base-blueprint-file', type=Path)
    parser.add_argument('--survey-file', type=Path)
    parser.add_argument('--resume',action='store_true',help='Continue recorded failed responses without repeating the first model generation')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    survey = {"encoding": "world-x-runs-v2", "world_id": "synthetic-flat-fixture", "dimension": "minecraft:overworld",
              "captured_at": "synthetic-offline", "complete": True, "unknown_cells": 0,
              "from": [8, 3, 8], "to": [40, 22, 40], "palette": ["minecraft:grass_block", "minecraft:air"],
              "legend": "Inclusive y..y_to,z..z_to rectangle repeats runs:[x_start,x_end,palette_index]. Every cell is covered.",
              "rows": [{"y":3,"y_to":3,"z":8,"z_to":40,"runs":[[8,40,0]]},
                       {"y":4,"y_to":22,"z":8,"z_to":40,"runs":[[8,40,1]]}]}
    store=BlueprintStore(output / 'blueprints')
    base_id=None
    if args.survey_file:
        survey=json.loads(args.survey_file.read_text(encoding='utf-8'))
        if not survey.get('complete') or survey.get('unknown_cells'):
            raise ValueError('A complete exact region snapshot is required for offline revision acceptance')
    if args.base_blueprint_file:
        source=json.loads(args.base_blueprint_file.read_text(encoding='utf-8'))
        base_id=source['compiled']['id']
        store.directory.mkdir(parents=True,exist_ok=True)
        shutil.copy2(args.base_blueprint_file,store.directory/(base_id+'.json'))
        store.load(base_id)
    context = {"player": "Wu_Dabin", "owner": True, "message": args.request,
               "state": {"world_id": "synthetic-flat-fixture", "dimension": "minecraft:overworld", "self": {"name": "wife", "creative": True, "x": 3, "y": 4, "z": 3}},
               "survey": survey, "offline_test": "This is a synthetic surveyed flat region, not a running game. Only design_blueprint is available. Return one complete blueprint tool call."}
    if args.survey_file:
        context['state']['world_id']=survey['world_id']
        context['offline_test']='Read-only design acceptance against an archived exact real-world snapshot. Only design_blueprint is available. No construction or world writes occur in this test.'
    if base_id:
        context['saved_base']=store.inspect(base_id,max_bytes=300000)
        context['base_design_review']=store.review(base_id)
        context['offline_test']+=' Return a delta revision using this base_blueprint_id, preserving origin/orientation. Do not rebuild the entire source operation list.'
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    tools = make_tools({'design_blueprint'})
    memory = BuildingContext(output / 'context')
    attempts = []
    if args.resume:
        recorded=json.loads((output/'report.json').read_text(encoding='utf-8'))
        attempts=recorded['attempts']
        if not attempts or any(a.get('valid') for a in attempts):
            raise ValueError('Resume requires recorded validation failures, not a successful design')
        for attempt in attempts:
            response=json.loads((output/('raw-response-'+str(attempt['attempt'])+'.json')).read_text(encoding='utf-8'))
            answer=response['choices'][0]['message']
            call=answer['tool_calls'][0]
            answer['tool_calls']=[call]
            messages.append(answer)
            messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps({'state':'FAILED','failure_code':'INVALID_BLUEPRINT','failure_message':attempt['error']})})
    planning_state={**context['state'],'survey':survey,'offline_test':context['offline_test']}
    for key in ('saved_base','base_design_review'):
        if key in context:planning_state[key]=context[key]
    for attempt in range(len(attempts),6):
        started = time.time()
        messages = memory.compact_messages(messages,args.request,planning_state,tools=tools)
        print('Design attempt ' + str(attempt + 1) + ' started; streaming with production API path.', flush=True)
        response = model_completion({"model": MODEL, "messages": messages, "tools": tools,
                                     "thinking": {"type": "enabled"}, "reasoning_effort": "high",
                                     "max_tokens": output_budget(messages, tools)}, read_api_key())
        choice = response['choices'][0]
        answer = choice['message']
        calls = answer.get('tool_calls') or []
        (output / ('raw-response-' + str(attempt + 1) + '.json')).write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
        if choice.get('finish_reason') == 'length' or not calls:
            raise RuntimeError('Incomplete model design response')
        call = calls[0]
        answer['tool_calls'] = [call]
        messages.append(answer)
        try:
            arguments=json.loads(call['function']['arguments'])
            blueprint = arguments['blueprint']
            if base_id and arguments.get('base_blueprint_id')!=base_id:
                raise ValueError('This revision test must use its specified base_blueprint_id')
            summary = store.save(blueprint, arguments.get('base_blueprint_id'), require_interiors=True, require_site_layout=True)
            review=store.review(summary['blueprint_id'])
            (output/'design-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
            if review['blocking_count']:
                raise ValueError('Blocking geometric review findings: '+json.dumps([f for f in review['findings'] if f['severity']=='blocking'],ensure_ascii=False))
            (output / 'model-design.json').write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding='utf-8')
            attempts.append({"attempt": attempt + 1, "seconds": time.time() - started, "usage": response.get('usage'), "valid": True})
            report = {"model": response.get('model'), "fixture": ('real-world snapshot; OFFLINE REVISION ONLY' if args.survey_file else 'synthetic-flat; NO WORLD CONSTRUCTION'),
                      "base_blueprint_id":base_id,"survey_captured_at":survey['captured_at'],"attempts": attempts, "summary": summary,
                      'design_review':{'blocking_count':review['blocking_count'],'review_count':review['review_count'],'scope':review['scope']}}
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=True))
            return
        except (ValueError, KeyError, TypeError) as error:
            attempts.append({"attempt": attempt + 1, "seconds": time.time() - started, "usage": response.get('usage'), "valid": False, "error": str(error)})
            (output / 'report.json').write_text(json.dumps({"attempts": attempts, "valid": False}, ensure_ascii=False, indent=2), encoding='utf-8')
            print('Design validation failed: ' + str(error), flush=True)
            messages.append({"role": "tool", "tool_call_id": call['id'], "content": json.dumps({"state": "FAILED", "failure_code": "INVALID_BLUEPRINT", "failure_message": str(error)})})
            messages = memory.compact_messages(messages,args.request,planning_state,tools=tools)
    (output / 'report.json').write_text(json.dumps({"attempts": attempts, "valid": False}, ensure_ascii=False, indent=2), encoding='utf-8')
    raise RuntimeError('Model failed to generate a valid blueprint after six repairs')


if __name__ == '__main__': main()
