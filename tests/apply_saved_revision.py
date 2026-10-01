"""Apply a real model's archived design through the production world tools.

No new planner or game process is started. The existing chat planner remains
running. This bounded acceptance runner additionally observes owner stop events
so a stop between verification and construction cannot start later steps.
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from WifeAgent import API_BASE, OWNER, WifeAgent, http_json, is_cancel_request
from BuildingBlueprint import BlueprintStore


def validate_repair_scope(task, plan):
    """Every observed base difference must be covered by the genuine revision."""
    detail=task.get('result') or {}
    mismatches=detail.get('mismatches') or []
    valid_state=(task.get('state')=='SUCCEEDED' or
                 (task.get('state')=='FAILED' and task.get('failureCode')=='BLUEPRINT_VERIFICATION_FAILED'))
    if (not valid_state or detail.get('verified') is True
            or not detail.get('verification_complete') or not mismatches
            or detail.get('mismatch_count')!=len(mismatches)):
        raise RuntimeError('Base repair requires complete, explicit structural mismatches')
    for mismatch in mismatches:
        point=mismatch['at']
        covered=any(all(op['from'][axis]<=point[axis]<=op['to'][axis] for axis in range(3))
                    for op in plan['operations'])
        if not covered or '__unloaded__' in mismatch.get('actual',''):
            raise RuntimeError('Base mismatch is outside the intended repair; preserve the world')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--blueprint-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--new-build-on-empty-air',action='store_true',
                        help='Allow an unbuilt archived design only when its complete real-world bounds are empty air')
    parser.add_argument('--repair-base-mismatches',action='store_true',
                        help='Allow only completely enumerated base differences that the revision explicitly changes')
    args=parser.parse_args()
    if (args.output/'report.json').exists():
        args.output=args.output/time.strftime('run-%Y%m%d-%H%M%S')
    state=http_json(API_BASE+'/v1/state')
    if not state.get('connected') or not state.get('self',{}).get('creative'):
        raise RuntimeError('A connected creative world is required')
    os.environ['WIFE_NG_WORLD_ID']=state['world_id']
    agent=WifeAgent()
    source=json.loads(args.blueprint_file.read_text(encoding='utf-8'))
    blueprint_id=source['compiled']['id']
    base_id=source['blueprint'].get('base_blueprint_id')
    if not base_id and not args.new_build_on_empty_air:
        raise ValueError('New construction requires explicit --new-build-on-empty-air')
    if base_id and args.new_build_on_empty_air:
        raise ValueError('Empty-air construction cannot be combined with a revision')
    if not base_id and args.repair_base_mismatches:
        raise ValueError('Base mismatch repair requires a saved revision')
    source_store=BlueprintStore(args.blueprint_file.parent)
    source_store.load(blueprint_id,require_current_interiors=True)
    if base_id: agent.blueprints.load(base_id)
    active=[t for t in http_json(API_BASE+'/v1/tasks') if t['state'] in ('RUNNING','QUEUED')]
    if active: raise RuntimeError('Another world task is active; revision application deferred')
    args.output.mkdir(parents=True,exist_ok=True)
    seen={json.dumps(event,sort_keys=True) for event in state.get('recent_chat',[])}
    stopped=threading.Event()
    observer_failed=threading.Event()
    stop_observer=threading.Event()
    records=[]
    started=time.time()

    def observe_owner_stop():
        try:
            while not stop_observer.wait(0.5):
                latest=http_json(API_BASE+'/v1/state',timeout=10)
                for event in latest.get('recent_chat',[]):
                    key=json.dumps(event,sort_keys=True)
                    if key in seen: continue
                    seen.add(key)
                    if (event.get('player') or '').lower()==OWNER.lower() and is_cancel_request(event.get('message')):
                        stopped.set()
                        agent.handle_fast_control(event)
                        return
        except Exception:
            observer_failed.set()
            # Fail closed if control observation is lost during this run.
            stopped.set()
            agent.handle_fast_control({'player':OWNER,'message':'停止'})

    def guard():
        if stopped.is_set() or agent.cancel_generation:
            raise RuntimeError('OWNER_CANCELLED' if not observer_failed.is_set() else 'CONTROL_OBSERVATION_LOST')

    def submit(name,arguments,allow_base_mismatches=False):
        guard()
        result=agent.submit_tool(name,arguments,expected_generation=0)
        records.append({'tool':name,'result':result})
        (args.output/'task-results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        detail=result.get('result') or {}
        print(json.dumps({'tool':name,'state':result['state'],'verified':detail.get('verified'),
                          'mismatch_count':detail.get('mismatch_count')},ensure_ascii=True),flush=True)
        guard()
        if allow_base_mismatches and not detail.get('verified'):
            validate_repair_scope(result,source_store.execution_plan(blueprint_id))
            return result
        if result['state']!='SUCCEEDED': raise RuntimeError('World tool did not complete: '+result['state'])
        if name in ('build_blueprint','verify_blueprint') and not detail.get('verified'):
            raise RuntimeError('World geometry differs; preserve current blocks and inspect mismatches')
        agent.building_context.task(name,result)
        return result

    observer=threading.Thread(target=observe_owner_stop,daemon=True)
    observer.start()
    error=None
    try:
        if base_id:
            submit('verify_blueprint',{'blueprint_id':base_id,'dimension':state['dimension']},
                   allow_base_mismatches=args.repair_base_mismatches)
        else:
            bounds=source['compiled']['summary']['bounds']
            before=submit('survey_region',{'from':bounds['from'],'to':bounds['to']})['result']
            (args.output/'preflight-survey.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
            if (not before.get('complete') or before.get('unknown_cells')
                    or not before.get('rows') or before.get('palette')!=['minecraft:air']):
                raise RuntimeError('New-build bounds are not completely surveyed empty air; preserve the world')
        guard()
        shutil.copy2(args.blueprint_file,agent.blueprints.directory/(blueprint_id+'.json'))
        compiled=agent.blueprints.load(blueprint_id,require_current_interiors=True)
        agent.building_context.design({'blueprint_id':blueprint_id,**compiled['summary'],
                                      'interior_validation_version':source['interior_validation_version']})
        review=submit('review_blueprint',{'blueprint_id':blueprint_id})['result']
        if review['blocking_count']:
            raise RuntimeError('Design review has blocking findings; do not submit construction')
        active=[t for t in http_json(API_BASE+'/v1/tasks') if t['state'] in ('RUNNING','QUEUED')]
        if active: raise RuntimeError('Another world task became active; construction deferred')
        submit('build_blueprint',{'blueprint_id':blueprint_id,'dimension':state['dimension']})
        submit('verify_blueprint',{'blueprint_id':blueprint_id,'dimension':state['dimension']})
        bounds=compiled['summary']['bounds']
        submit('survey_region',{'from':bounds['from'],'to':bounds['to']})
    except Exception as exception:
        error=str(exception)
        raise
    finally:
        stop_observer.set()
        observer.join(timeout=12)
        report={'world_id':state['world_id'],'base_blueprint_id':base_id,'blueprint_id':blueprint_id,
                'seconds':time.time()-started,'error':error,'owner_stopped':stopped.is_set(),
                'tool_calls':len(records),'visual_acceptance':False,
                'mode':'revision' if base_id else 'new-build-after-empty-air-survey',
                'evidence_scope':'Real archived model design, current quality checks, existing world tools; no planner/game restart. '
                    +('New-build design originated in a synthetic flat fixture; real empty-air preflight is recorded separately. ' if not base_id else '')
                    +'Visual quality still needs game inspection.'}
        (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__': main()
