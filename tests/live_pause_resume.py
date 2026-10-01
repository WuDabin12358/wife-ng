"""Observe a real partial build, pause it, prove it stays still, then resume."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from WifeAgent import API_BASE, http_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    deadline = time.monotonic()+1200
    target = None
    while time.monotonic()<deadline:
        for task in http_json(API_BASE+"/v1/tasks"):
            if task.get("tool")=="build_blueprint" and task.get("state")=="RUNNING" and 4<task.get("stepIndex",0)<task.get("totalSteps",0):
                target=task
                break
        if target: break
        time.sleep(1)
    if not target: raise RuntimeError("No real partially completed build appeared")
    url=API_BASE+"/v1/tasks/"+target["id"]
    record={"task_id":target["id"],"before":target,"scope":"Real running construction, no synthetic executor"}
    http_json(url+"/pause","POST",{})
    try:
        time.sleep(1)
        paused=http_json(url)
        if paused.get("state")!="PAUSED": raise AssertionError("Build did not pause")
        scan_args={"from":target["arguments"]["operations"][0]["from"],"to":target["arguments"]["operations"][0]["to"]}
        operations=target["arguments"]["operations"]
        scan_args={"from":[min(op["from"][i] for op in operations) for i in range(3)],
                   "to":[max(op["to"][i] for op in operations) for i in range(3)]}
        def scan():
            accepted=http_json(API_BASE+"/v1/tools","POST",{"tool":"survey_region","arguments":scan_args})
            scan_url=API_BASE+"/v1/tasks/"+accepted["task"]["id"]
            for _ in range(30):
                result=http_json(scan_url)
                if result.get("state")=="SUCCEEDED": return result["result"]
                if result.get("state") in {"FAILED","CANCELLED"}: raise RuntimeError("Pause survey failed")
                time.sleep(0.2)
            raise RuntimeError("Pause survey timed out")
        before_scan=scan()
        time.sleep(12)
        after_scan=scan()
        still_paused=http_json(url)
        stable = before_scan["complete"] and after_scan["complete"] and all(before_scan[k]==after_scan[k] for k in ("rows","palette"))
        record.update(paused=paused,after_wait=still_paused,hold_seconds=12,geometry_stable=stable,
                      surveyed_cells=before_scan["cells"])
        if still_paused.get("state")!="PAUSED" or still_paused["stepIndex"]!=paused["stepIndex"]:
            raise AssertionError("Paused construction continued advancing")
        if not stable: raise AssertionError("World geometry changed during the pause")
        record["pause_passed"]=True
    finally:
        current=http_json(url)
        if current.get("state")=="PAUSED":
            http_json(url+"/resume","POST",{})
            record["resumed"]=http_json(url)
        output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"task_id":target["id"],"pause_passed":record["pause_passed"],
                      "stepIndex":paused["stepIndex"],"geometry_stable":stable},ensure_ascii=True),flush=True)


if __name__=="__main__":main()
