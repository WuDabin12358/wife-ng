"""Run a natural-language acceptance request through the production agent.

Uses a real connected Minecraft world and the real DeepSeek API. The normal
chat/control poller remains active, including the owner's immediate stop path.
This script never supplies a blueprint or substitutes model/tool responses.
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from WifeAgent import API_BASE, OWNER, WifeAgent, http_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--stay-running", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    state = http_json(API_BASE + "/v1/state")
    if not state.get("connected") or not state.get("self", {}).get("creative"):
        raise RuntimeError("A connected creative-mode Wife NG is required")
    os.environ["WIFE_NG_WORLD_ID"] = state["world_id"]
    agent = WifeAgent()
    real_submit, real_handle = agent.submit_tool, agent.handle
    records, lock = [], threading.Lock()
    started = time.time()

    def submit(name, arguments, progress_player=None, expected_generation=None):
        error = None
        try:
            result = real_submit(name, arguments, progress_player, expected_generation=expected_generation)
        except Exception as exception:
            error = exception
            result = {"state": "FAILED", "failure_code": "BRIDGE_ERROR", "failure_message": str(exception)}
        record = {"time": time.time(), "tool": name, "arguments": arguments, "result": result}
        with lock:
            records.append(record)
            with (output / "tool-results.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        detail = result.get("result") or {}
        print(json.dumps({"tool": name, "state": result.get("state"), "blueprint_id": detail.get("blueprint_id"),
                          "verified": detail.get("verified"), "mismatch_count": detail.get("mismatch_count"),
                          "failure": result.get("failureMessage", result.get("failure_message"))}, ensure_ascii=True), flush=True)
        if error is not None:
            raise error
        return result

    def handle(event):
        fixture = event.get("message") == args.request
        error = None
        try:
            return real_handle(event)
        except Exception as exception:
            error = str(exception)
            raise
        finally:
            if fixture:
                with lock:
                    final_records = list(records)
                report = {"request": args.request, "world_id": state["world_id"], "initial_state": state,
                          "seconds": time.time()-started, "error": error, "tool_calls": len(final_records),
                          "verified_blueprint_ids": [r["result"]["result"]["blueprint_id"] for r in final_records
                              if r["tool"] == "verify_blueprint" and (r["result"].get("result") or {}).get("verified")],
                          "visual_acceptance": False,
                          "evidence_scope": "Real production agent and world tools; visual quality requires separate game inspection."}
                (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print("LIVE_REQUEST_FINISHED " + json.dumps({k:v for k,v in report.items() if k != "initial_state"}, ensure_ascii=True), flush=True)
                if not args.stay_running:
                    agent.running = False

    agent.submit_tool, agent.handle = submit, handle
    agent.pending.put({"player": OWNER, "message": args.request, "kind": "chat"})
    agent.run()


if __name__ == "__main__":
    main()
