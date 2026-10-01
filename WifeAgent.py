import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict, deque
from pathlib import Path
from BuildingBlueprint import BlueprintStore
from BuildingContext import BUILDING_PROMPT, BuildingContext, world_key, output_budget, INPUT_BYTE_BUDGET


ROOT = Path(__file__).resolve().parent
MCC_ROOT = ROOT.parent
API_BASE = os.environ.get("WIFE_NG_API", "http://127.0.0.1:8766").rstrip("/")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
OWNER = os.environ.get("WIFE_NG_OWNER", "Wu_Dabin").strip() or "Wu_Dabin"
BOT_NAME = os.environ.get("WIFE_NG_BOT_NAME", "wife").strip() or "wife"
POLL_SECONDS = 0.8
MAX_TOOL_ROUNDS = 60


SYSTEM_PROMPT = """你是 Minecraft 里的 wife。你有点傲娇、会自然连发，但行动必须认真完成。
你不是只会聊天：你能读取自身状态、背包、附近实体和方块，并通过工具移动、跟随、采集、拾取、交付、合成、烧炼、放置、交互、使用物品和记忆容器。
行动规则：
1. 一轮只调用一个工具；等工具返回真实结果后再决定下一步。
2. 复杂目标要拆成可验证的小步骤。失败时根据 failure_code、failure_message 和最新状态调整方案，不要盲目重复原参数。
3. goto 使用世界整数坐标；物品和方块使用 minecraft: 命名空间；数量必须明确。
4. follow 是持续任务，主人说停止时调用 stop。
5. 需要资源时优先 acquire；已知更精确策略时可直接 mine/chop/craft/smelt。
6. 只执行 Wu_Dabin 提出的游戏动作；其他玩家只能聊天。
7. 不要声称未验证的动作已经成功，不要输出 MCC 原生命令或服务器斜杠命令。
8. 回复用中文，每条尽量不超过100字。可以调用 say 多次形成自然连发；不要发送“已向服务器提交命令”之类的系统播报。
9. 玩家明确要求发N条或说N句时，必须分N轮调用 say，每次只放一条，不能合并成一条。
10. OP 模式提供 server_command 时，可以按 Wu_Dabin 的要求执行服务器命令；command 参数不要带多余解释。执行后根据最新聊天反馈判断结果。
11. follow 是持续动作，但不妨碍聊天；主人说停止、取消、别跟了或重置时必须立即停止。
12. 生存流程优先使用 equip、use_item、place_block、interact_block、break_block_at 实际操作。除非主人明确要求作弊或服务器管理，否则不要用 server_command 代替正常生存操作。
13. 主人给出完整目标后持续推进所有必要步骤；仅在缺少材料、位置选择会显著改变结果或工具确实无法完成时才询问，不要每完成一步都问是否继续。
14. 生存模式建房、铺地基等多方块任务优先调用 build_structure，让执行器内部完成选址、材料预算、逐块验证和续建；创造模式使用下述自由蓝图工作流。find_build_site 可用于查看候选地点。
15. 生存模式 build_structure 完成的是房屋主体。必须根据返回 result 中的原点和尺寸继续收尾：正门下半格固定为 (x+width//2,y+1,z)，先 craft 对应木门再 place_block；窗洞可以保留，若有玻璃则补玻璃。门未安装前不得说“房子盖好了”。
16. 不要把思考草稿、工具选择过程或“让我先……”式自言自语发到聊天栏；只发送行动结果、简短进度或确实需要主人决定的问题。
17. 当前服务器是 Minecraft 1.16.5：铁矿掉落 minecraft:iron_ore，金矿掉落 minecraft:gold_ore；绝不能使用 1.17 才有的 raw_iron、raw_gold、raw_copper。
如果只是主动接话而没有动作请求，最多发两条消息，不要自行破坏或采集。"""
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("Wu_Dabin", OWNER).replace("里的 wife", "里的 " + BOT_NAME)
SYSTEM_PROMPT += "\n" + BUILDING_PROMPT


TOOL_SCHEMAS = [
    ("recall_building", "搜索当前世界归档的建筑名称、风格、方案id或任务id；找回从上下文移出的旧记录，不修改世界", {
        "query": "string",
    }, ["query"]),
    ("inspect_blueprint", "读取已保存蓝图的原始设计与操作；按上下文容量自动分页，不修改世界", {
        "blueprint_id": "string", "operation_start": "integer",
    }, ["blueprint_id"]),
    ("review_blueprint", "审查保存蓝图的窗宽、正向和斜向视野、地板支撑及已知不稳定构件；返回坐标与未知项，不修改世界、不判定审美合格", {
        "blueprint_id": "string",
    }, ["blueprint_id"]),
    ("survey_region", "读取指定区域精确方块状态；每个坐标包括空气或未知标记，建议分块扫描", {
        "from": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
        "to": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
    }, ["from", "to"]),
    ("design_blueprint", "设计并保存自由建筑蓝图，验证规模和坐标，生成三视图；不修改世界。格式见建筑工作流", {
        "base_blueprint_id": "string",
        "blueprint": {"type": "object", "properties": {
            "name": {"type": "string"}, "style": {"type": "string"}, "design_intent": {"type": "string"},
            "origin": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
            "orientation": {"type": "string", "enum": ["north", "east", "south", "west"]},
            "palette": {"type": "object", "additionalProperties": {"type": "string"}},
            "operations": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["fill", "block", "hollow", "room", "frame", "line", "repeat", "gable"]},
                "material": {"type": "string"}, "label": {"type": "string"},
                "from": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "to": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "at": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "count": {"type": "integer", "minimum": 1, "maximum": 128},
                "step": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "operations": {"type": "array", "items": {"type": "object"}},
                "ridge_axis": {"type": "string", "enum": ["x", "z"]},
                "entry": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "floor_material": {"type": "string"}, "roof_material": {"type": "string"},
            }, "required": ["kind"], "additionalProperties": False}, "maxItems": 1024},
            "checkpoints": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
            "rooms": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "from": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "to": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                "entry": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
            }, "required": ["name", "from", "to", "entry"], "additionalProperties": False}, "maxItems": 24},
            "interiors": {"type":"array", "maxItems":24, "items":{"type":"object","properties":{
                "room":{"type":"string"},
                "zones":{"type":"array","maxItems":24,"items":{"type":"object","properties":{
                    "name":{"type":"string"},"purpose":{"type":"string","enum":["living","dining","kitchen","bedroom","study","storage","entry","bathroom","display","workshop","circulation"]},
                    "from":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                    "to":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                },"required":["name","purpose","from","to"],"additionalProperties":False}},
                "furniture":{"type":"array","maxItems":128,"items":{"type":"object","properties":{
                    "name":{"type":"string"},"zone":{"type":"string"},
                    "type":{"type":"string","enum":["sofa","armchair","chair","coffee_table","dining_table","desk","counter","cabinet","bookcase","bed","tv","plant","rug"]},
                    "facing":{"type":"string","enum":["north","east","south","west"]},
                    "faces":{"type":"string"},
                    "from":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                    "to":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                    "access":{"type":"array","items":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3}},
                },"required":["name","zone","type","facing","from","to","access"],"additionalProperties":False}},
                "circulation":{"type":"array","maxItems":32,"items":{"type":"object","properties":{
                    "name":{"type":"string"},
                    "from":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                    "to":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                },"required":["name","from","to"],"additionalProperties":False}},
            },"required":["room","zones","furniture","circulation"],"additionalProperties":False}},
            "windows":{"type":"array","maxItems":128,"items":{"type":"object","properties":{
                "name":{"type":"string"},"room":{"type":"string"},
                "facing":{"type":"string","enum":["north","east","south","west"]},
                "from":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                "to":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
            },"required":["name","room","facing","from","to"],"additionalProperties":False}},
            "site_layout":{"type":"object","properties":{
                "spaces":{"type":"array","maxItems":48,"items":{"type":"object","properties":{
                    "name":{"type":"string"},"purpose":{"type":"string","enum":["entry","courtyard","veranda","terrace","garden","utility"]},
                    "design_intent":{"type":"string"},"from":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},
                    "to":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3}
                },"required":["name","purpose","design_intent","from","to"],"additionalProperties":False}},
                "routes":{"type":"array","maxItems":96,"items":{"type":"object","properties":{
                    "name":{"type":"string"},"from_space":{"type":"string"},"to_space":{"type":"string"},
                    "purpose":{"type":"string","enum":["entry","main","secondary","service"]},"width":{"type":"integer","minimum":1,"maximum":16},
                    "path":{"type":"array","minItems":2,"maxItems":64,"items":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3}}
                },"required":["name","from_space","to_space","purpose","width","path"],"additionalProperties":False}}
            },"required":["spaces","routes"],"additionalProperties":False},
        }, "required": ["name", "style", "design_intent", "origin", "palette", "operations", "checkpoints", "rooms", "interiors", "windows"],
            "additionalProperties": False},
    }, ["blueprint"]),
    ("build_blueprint", "创造模式批量建造保存的蓝图；逐段执行并核对最终方块，包含传送加载区块", {
        "blueprint_id": "string", "dimension": "string",
    }, ["blueprint_id", "dimension"]),
    ("verify_blueprint", "完整核对保存蓝图的最终布局，返回实际不符坐标；必须在原维度", {
        "blueprint_id": "string", "dimension": "string",
    }, ["blueprint_id", "dimension"]),
    ("resume_build", "恢复当前世界中已暂停的建筑或核对任务，沿已保存步骤继续", {
        "task_id": "string",
    }, ["task_id"]),
    ("goto", "走到世界坐标", {"x": "integer", "y": "integer", "z": "integer", "radius": "integer"}, ["x", "y", "z"]),
    ("follow", "持续跟随玩家", {"player": "string"}, ["player"]),
    ("stop", "立即停止当前动作", {}, []),
    ("mine", "挖指定方块并以背包增量验证", {"block": "string", "amount": "integer", "collect_item": "string"}, ["block"]),
    ("chop", "采集原木", {"amount": "integer"}, []),
    ("pickup", "拾取掉落物", {"item": "string", "amount": "integer"}, []),
    ("deliver", "走近玩家并交付物品", {"player": "string", "item": "string", "amount": "integer"}, ["player", "item"]),
    ("craft", "使用玩家合成栏或附近工作台合成", {"item": "string", "amount": "integer"}, ["item"]),
    ("smelt", "使用附近熔炉烧炼", {"input": "string", "item": "string", "fuel": "string", "amount": "integer"}, ["input", "item"]),
    ("acquire", "自动选择采矿、伐木或合成策略获取物品", {"item": "string", "amount": "integer"}, ["item"]),
    ("inspect_container", "打开并记忆附近容器；可指定坐标", {"x": "integer", "y": "integer", "z": "integer"}, []),
    ("equip", "把背包物品切换到主手", {"item": "string"}, ["item"]),
    ("use_item", "使用主手物品，或先装备指定物品再使用", {"item": "string"}, []),
    ("place_block", "把背包中的方块放到指定世界坐标", {"item": "string", "x": "integer", "y": "integer", "z": "integer"}, ["item", "x", "y", "z"]),
    ("interact_block", "对指定坐标方块右键交互", {"x": "integer", "y": "integer", "z": "integer", "face": "string", "item": "string"}, ["x", "y", "z"]),
    ("break_block_at", "破坏指定世界坐标的方块", {"x": "integer", "y": "integer", "z": "integer"}, ["x", "y", "z"]),
    ("find_build_site", "寻找附近平坦、有支撑且上方无障碍的建筑区域", {"width": "integer", "depth": "integer", "clearance": "integer", "radius": "integer"}, []),
    ("build_structure", "自动选址并完整建造一座小屋；内部逐块执行、验证、保存进度", {"x": "integer", "y": "integer", "z": "integer", "width": "integer", "depth": "integer", "height": "integer", "search_radius": "integer", "floor_item": "string", "wall_item": "string", "roof_item": "string"}, []),
    ("say", "在游戏聊天栏发送一条消息", {"message": "string"}, ["message"]),
    ("server_command", "在 OP 模式下以 wife 身份执行一条服务器命令", {"command": "string"}, ["command"]),
]

CANCEL_PHRASES = (
    "停止", "取消", "停下", "别跟", "不要跟", "别做", "不要做", "重置",
    "暂停", "stop", "cancel", "pause",
)


def make_tools(names=None):
    allowed = None if names is None else set(names)
    result = []
    for name, description, properties, required in TOOL_SCHEMAS:
        if allowed is not None and name not in allowed:
            continue
        schema = {
            "type": "object",
            "properties": {key: value if isinstance(value, dict) else {"type": value} for key, value in properties.items()},
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        result.append({"type": "function", "function": {
            "name": name, "description": description, "parameters": schema,
        }})
    return result


def read_api_key():
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    # Ignore stale placeholders inherited from a launcher and fall back to the
    # user's ini file. Real DeepSeek keys are substantially longer than this.
    if len(value) >= 20:
        return value
    path = ROOT / "DEEPSEEK_API_KEY.ini"
    if not path.exists():
        path = MCC_ROOT / "DEEPSEEK_API_KEY.ini"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8-sig").strip()
    if "=" in text:
        key, candidate = text.split("=", 1)
        if key.strip().upper() == "DEEPSEEK_API_KEY":
            text = candidate.strip().strip('"').strip("'")
    return text


def http_json(url, method="GET", payload=None, timeout=20, headers=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    # Send the control token only to the configured game bridge.
    token = os.environ.get("WIFE_NG_TOKEN", "")
    if token and (url == API_BASE or url.startswith(API_BASE + "/")):
        request_headers["Authorization"] = "Bearer " + token
    if body is not None:
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read(16 * 1024).decode("utf-8", errors="replace")
        raise RuntimeError("HTTP {} from {}: {}".format(error.code, url, details)) from error


def model_completion(payload, api_key, cancelled=lambda: False):
    """Stream a potentially long design without a short total-generation timeout.

    Socket timeout is a no-data watchdog, not an input/output budget. Reasoning
    remains private protocol data; only a completed assistant message is used.
    """
    request_payload = dict(payload, stream=True, stream_options={"include_usage": True})
    request = urllib.request.Request(DEEPSEEK_URL, data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    content, reasoning, calls = [], [], {}
    usage, finish, model = {}, None, MODEL
    with urllib.request.urlopen(request, timeout=120) as response:
        for raw in response:
            if cancelled(): raise RuntimeError("OWNER_CANCELLED")
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": break
            chunk = json.loads(data)
            model = chunk.get("model", model)
            if chunk.get("usage"): usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("finish_reason"): finish = choice["finish_reason"]
                delta = choice.get("delta", {})
                if delta.get("content"): content.append(delta["content"])
                if delta.get("reasoning_content"): reasoning.append(delta["reasoning_content"])
                for part in delta.get("tool_calls", []):
                    index = part.get("index", 0)
                    call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if part.get("id"): call["id"] = part["id"]
                    for key in ("name", "arguments"):
                        if part.get("function", {}).get(key): call["function"][key] += part["function"][key]
    if finish not in {"stop", "tool_calls", "length"}:
        raise RuntimeError("Incomplete model stream: " + str(finish))
    message = {"role": "assistant", "content": "".join(content), "reasoning_content": "".join(reasoning)}
    if calls: message["tool_calls"] = [calls[k] for k in sorted(calls)]
    return {"model": model, "usage": usage, "choices": [{"finish_reason": finish, "message": message}]}


def compact_state(state):
    blocks = Counter(item.get("block", "unknown") for item in state.get("nearby_blocks", []))
    return {
        "world_id": state.get("world_id"),
        "connected": state.get("connected"),
        "dimension": state.get("dimension"),
        "self": state.get("self"),
        "inventory": state.get("inventory", []),
        "nearby_entities": state.get("nearby_entities", []),
        "nearby_block_counts": dict(blocks.most_common(30)),
        "local_layout": state.get("local_layout", {}),
        "recent_chat": state.get("recent_chat", [])[-12:],
    }


def recent_task_memory():
    """Carry durable high-level task results into later chat turns."""
    try:
        tasks = http_json(API_BASE + "/v1/tasks", timeout=15)
    except Exception:
        return []
    remembered = []
    for task in reversed(tasks):
        if task.get("tool") not in ("build_structure", "find_build_site", "build_blueprint", "verify_blueprint"):
            continue
        result = dict(task.get("result") or {})
        if task.get("tool") == "build_structure" and all(key in result for key in ("x", "y", "z", "width")):
            result["front_door_lower"] = {
                "x": int(result["x"]) + int(result["width"]) // 2,
                "y": int(result["y"]) + 1,
                "z": int(result["z"]),
            }
        remembered.append({
            "id": task.get("id"),
            "tool": task.get("tool"),
            "state": task.get("state"),
            "progress": task.get("progress"),
            "step_index": task.get("stepIndex"),
            "total_steps": task.get("totalSteps"),
            "result": result,
            "failure_code": task.get("failureCode", ""),
            "failure_message": task.get("failureMessage", ""),
        })
        if len(remembered) >= 4:
            break
    return remembered


ACTION_WORDS = (
    "做", "建", "放", "装", "补", "挖", "砍", "合成", "烧", "拿", "给", "送",
    "跟", "走", "来", "去", "继续", "停止", "取消", "重置", "门", "窗", "屋顶",
    "craft", "build", "place", "mine", "chop", "follow", "stop",
)


def looks_like_action_request(message):
    lowered = (message or "").lower()
    return any(word in lowered for word in ACTION_WORDS)


def looks_like_architecture_read_request(message):
    value = (message or "").lower()
    reads = ("查看", "检查", "介绍", "说明", "展示", "查找", "看看", "查询", "回忆", "inspect", "show", "recall", "verify")
    changes = ("建造", "建房", "盖房", "改建", "改造", "修建", "建一", "造一", "盖一", "搭建", "造个", "建个", "扩建", "重建", "翻修", "修复", "修补", "修改", "改成", "换成", "build", "repair", "modify")
    return any(word in value for word in reads) and not any(word in value for word in changes)


def looks_like_build_request(message):
    value = (message or "").lower()
    if looks_like_architecture_read_request(value):
        return False
    return any(word in value for word in ("建造", "建筑", "建房", "盖房", "小屋", "别墅", "教堂", "城堡", "改建", "改造", "修建", "build", "house"))


def looks_like_internal_monologue(text):
    value = text or ""
    return value.count("让我") >= 2 or value.count("我需要") >= 2 or value.count("我决定") >= 2


def is_cancel_request(message):
    normalized = (message or "").strip().lower()
    return any(phrase in normalized for phrase in CANCEL_PHRASES)


class WifeAgent:
    def __init__(self):
        self.building_directory = ROOT / "headlessmc" / "architecture" / world_key()
        self.blueprints = BlueprintStore(self.building_directory / "blueprints")
        self.building_context = BuildingContext(self.building_directory)
        self.cancel_generation = 0
        self.submission_lock = threading.RLock()
        self.api_key = read_api_key()
        self.pending = queue.Queue(maxsize=20)
        self.seen = deque(maxlen=400)
        self.recent_texts = {}
        self.histories = defaultdict(lambda: deque(maxlen=12))
        self.active_request_text = ""
        self.running = True

    def call_model(self, messages, tools):
        architecture = any(t["function"]["name"] == "design_blueprint" for t in tools)
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.7,
            "max_tokens": output_budget(messages, tools),
            "thinking": {"type": "enabled" if architecture else "disabled"},
            "stream": False,
        }
        if architecture:
            payload.pop("tool_choice", None)
            payload.pop("temperature", None)
            payload["reasoning_effort"] = "high"
        generation = self.cancel_generation
        response = model_completion(payload, self.api_key, lambda: generation != self.cancel_generation)
        self.building_directory.mkdir(parents=True, exist_ok=True)
        with (self.building_directory / "model-usage.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"time": time.time(), "model": MODEL, "usage": response.get("usage", {}),
                                  "finish_reason": response["choices"][0].get("finish_reason")}) + "\n")
        if response["choices"][0].get("finish_reason") == "length":
            raise RuntimeError("Model output was truncated; divide the blueprint into smaller phases")
        return response["choices"][0]["message"]

    def submit_tool(self, name, arguments, progress_player=None, expected_generation=None):
        generation = self.cancel_generation if expected_generation is None else expected_generation
        if generation!=self.cancel_generation:
            return {"state":"CANCELLED","failure_code":"OWNER_CANCELLED"}
        execution_metadata=None
        world_tool=name
        if name == "recall_building":
            return {"state": "SUCCEEDED", "result": self.building_context.recall(arguments.get("query", ""))}
        if name == "inspect_blueprint":
            return {"state": "SUCCEEDED", "result": self.blueprints.inspect(arguments["blueprint_id"],
                    arguments.get("operation_start", 0), max_bytes=INPUT_BYTE_BUDGET // 2)}
        if name == "review_blueprint":
            return {"state":"SUCCEEDED","result":self.blueprints.review(arguments['blueprint_id'])}
        if name == "design_blueprint":
            if "interiors" not in arguments["blueprint"] or "windows" not in arguments["blueprint"]:
                raise ValueError("New architectural designs must declare interiors and outward windows; legacy designs remain readable")
            summary = self.blueprints.save(arguments["blueprint"], arguments.get("base_blueprint_id"), require_interiors=True, require_site_layout=True)
            self.building_context.design(summary)
            return {"state": "SUCCEEDED", "result": summary}
        if name in {"build_blueprint", "verify_blueprint"}:
            if name=='build_blueprint':
                review=self.blueprints.review(arguments['blueprint_id'])
                if review['blocking_count']:
                    raise ValueError('Blueprint has blocking design findings; call review_blueprint and revise: '
                                     +json.dumps([f for f in review['findings'] if f['severity']=='blocking'],ensure_ascii=False))
            plan=self.blueprints.execution_plan(arguments["blueprint_id"]) if name=="build_blueprint" else self.blueprints.verification_plan(arguments["blueprint_id"])
            if not plan["operations"]:
                plan={**self.blueprints.verification_plan(arguments["blueprint_id"]),"mode":"unchanged_revision","changed_cells":0}
                world_tool="verify_blueprint"
            execution_metadata={key:value for key,value in plan.items() if key not in {"operations","blueprint_id"}}
            arguments = {"blueprint_id": plan["blueprint_id"], "operations": plan["operations"],
                         "dimension": arguments["dimension"]}
        if name == "resume_build":
            previous = http_json(API_BASE + "/v1/tasks/" + arguments["task_id"], timeout=10)
            if previous.get("tool") not in {"build_blueprint", "verify_blueprint"} or previous.get("state") != "PAUSED":
                raise ValueError("Only paused building tasks can be resumed")
            with self.submission_lock:
                if generation!=self.cancel_generation:
                    return {"state":"CANCELLED","failure_code":"OWNER_CANCELLED"}
                accepted = http_json(API_BASE + "/v1/tasks/" + arguments["task_id"] + "/resume", "POST", {}, 10)
            name = previous["tool"]
        else:
            with self.submission_lock:
                if generation!=self.cancel_generation:
                    return {"state":"CANCELLED","failure_code":"OWNER_CANCELLED"}
                accepted = http_json(API_BASE + "/v1/tools", "POST", {"tool": world_tool, "arguments": arguments}, 10)
        task = accepted["task"]
        task_id = task["id"]
        if name == "follow":
            time.sleep(2)
            return http_json(API_BASE + "/v1/tasks/" + task_id, timeout=10)
        long_running = name in {"mine", "chop", "acquire", "smelt", "build_structure", "build_blueprint", "verify_blueprint"}
        deadline = time.time() + (7200 if name in {"build_blueprint", "verify_blueprint"} else 1200 if long_running else 240)
        next_progress = time.time() + 25
        while time.time() < deadline:
            if generation != self.cancel_generation and name not in {"stop", "say"}:
                try:
                    endpoint="/pause" if name in {"build_blueprint","verify_blueprint"} else "/cancel"
                    http_json(API_BASE + "/v1/tasks/" + task_id + endpoint, "POST", {}, 10)
                except Exception:
                    pass
                return {"id": task_id, "state": "PAUSED" if name in {"build_blueprint","verify_blueprint"} else "CANCELLED", "failure_code": "OWNER_CANCELLED"}
            task = http_json(API_BASE + "/v1/tasks/" + task_id, timeout=10)
            if task.get("state") in ("SUCCEEDED", "FAILED", "CANCELLED", "PAUSED"):
                if execution_metadata is not None: task.setdefault("result",{}).update(execution_metadata)
                return task
            if long_running and progress_player and time.time() >= next_progress:
                activity = {
                    "mine": "我还在挖矿，任务没有卡住。",
                    "chop": "我还在砍树，任务没有卡住。",
                    "acquire": "我还在收集需要的资源，任务没有卡住。",
                    "smelt": "我还在烧炼，任务没有卡住。",
                    "build_structure": "我还在施工，任务没有卡住。",
                    "build_blueprint": "我还在按设计施工，" + task.get("progress", ""),
                    "verify_blueprint": "我正在核对建筑的实际方块布局。",
                }[name]
                try:
                    http_json(API_BASE + "/v1/tools", "POST", {
                        "tool": "say",
                        "arguments": {"message": "@" + progress_player + " " + activity},
                    }, 10)
                except Exception:
                    pass
                next_progress = time.time() + 60
            time.sleep(1)
        # Do not leave an unobserved construction task running after timeout.
        http_json(API_BASE + "/v1/tasks/" + task_id + "/pause", "POST", {}, 10)
        return {"id": task_id, "state": "PAUSED", "failure_code": "PLANNER_TIMEOUT"}

    def say_text(self, player, text):
        text = " ".join((text or "").split()).strip()
        if not text:
            return
        prefix = "@" + player + " "
        while text:
            cut = min(190, len(text))
            if cut < len(text):
                punctuation = max(text.rfind(mark, 0, cut) for mark in "。！？；，")
                if punctuation >= 50:
                    cut = punctuation + 1
            self.submit_tool("say", {"message": prefix + text[:cut]})
            text = text[cut:].lstrip()
            time.sleep(1.1)

    def handle(self, event):
        generation = self.cancel_generation
        player = event.get("player") or "玩家"
        message = event.get("message") or event.get("text") or ""
        owner = player.lower() == OWNER.lower()
        explicit = "wife" in message.lower()
        proactive = not explicit
        state = compact_state(http_json(API_BASE + "/v1/state", timeout=15))
        context = {
            "player": player,
            "owner": owner,
            "proactive": proactive,
            "message": message,
            "state": state,
            "recent_high_level_tasks": recent_task_memory(),
            "building_checkpoint": self.building_context.checkpoint(),
        }
        history = list(self.histories[player])
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": json.dumps(context, ensure_ascii=False)})
        if owner:
            capabilities = http_json(API_BASE + "/v1/capabilities", timeout=10)
            allowed_tools = {item.get("name") for item in capabilities.get("tools", [])}
            allowed_tools |= {"recall_building", "inspect_blueprint", "review_blueprint"}
            if "build_blueprint" in allowed_tools and state.get("self", {}).get("creative"):
                allowed_tools.add("design_blueprint")
                allowed_tools.add("resume_build")
                if looks_like_build_request(message):
                    allowed_tools &= {"goto", "stop", "say", "survey_region", "design_blueprint",
                                      "build_blueprint", "verify_blueprint", "resume_build", "recall_building", "inspect_blueprint", "review_blueprint"}
            else:
                allowed_tools -= {"build_blueprint", "verify_blueprint"}
            if looks_like_architecture_read_request(message):
                allowed_tools &= {"say", "survey_region", "verify_blueprint", "recall_building", "inspect_blueprint", "review_blueprint"}
            tools = make_tools(allowed_tools)
        else:
            tools = make_tools({"say"})
        did_say = False
        did_action = False
        repair_attempts = 0
        structure_needs_door = False
        requires_creative_build = owner and bool((state.get("self") or {}).get("creative")) and looks_like_build_request(message)
        designed_ids = set()
        built_ids = set()
        verified_ids = set()
        reviewed_ids = set()

        for _ in range(MAX_TOOL_ROUNDS):
            if generation != self.cancel_generation:
                return
            messages = self.building_context.compact_messages(messages, message, state, tools=tools)
            answer = self.call_model(messages, tools)
            if generation != self.cancel_generation:
                return
            assistant_message = {
                "role": "assistant",
                "content": answer.get("content"),
            }
            if answer.get("reasoning_content") is not None:
                assistant_message["reasoning_content"] = answer.get("reasoning_content")
            answer_calls = answer.get("tool_calls") or []
            # DeepSeek may propose several calls at once. Wife NG intentionally
            # executes one verified step per round, so only preserve the call
            # that will receive a tool response in the conversation transcript.
            if answer_calls:
                assistant_message["tool_calls"] = [answer_calls[0]]
            messages.append(assistant_message)
            calls = answer_calls
            if not calls:
                content = answer.get("content") or ""
                must_act = owner and looks_like_action_request(message) and not looks_like_architecture_read_request(message) and not did_action
                if requires_creative_build and (not built_ids or not built_ids.issubset(verified_ids) or not designed_ids.issubset(built_ids)):
                    must_act = True
                if structure_needs_door:
                    must_act = True
                if (must_act or looks_like_internal_monologue(content)) and repair_attempts < 2:
                    repair_attempts += 1
                    messages.append({
                        "role": "system",
                        "content": "不要输出思考过程。该请求还未完成：创造建筑需要实际build_blueprint、verify_blueprint及细节检查，design_blueprint/survey不算建成。立即调用下一工具；若确实无法行动，只说明明确的failure_code。",
                    })
                    continue
                if must_act and requires_creative_build:
                    content = "建筑还没有通过完整施工与复核，我需要先处理未完成的步骤。"
                if not did_say:
                    if looks_like_internal_monologue(content):
                        content = "我还没能确定下一步的可靠动作，先停下来检查任务记录。"
                    self.say_text(player, content)
                self.histories[player].append({"role": "user", "content": message})
                self.histories[player].append({"role": "assistant", "content": answer.get("content") or "",
                                               "reasoning_content": answer.get("reasoning_content") or ""})
                return

            call = calls[0]
            name = call.get("function", {}).get("name", "")
            try:
                if name not in {tool["function"]["name"] for tool in tools}:
                    raise ValueError("Tool is not available for this player/mode: " + name)
                arguments = json.loads(call.get("function", {}).get("arguments") or "{}")
                if name=='build_blueprint' and arguments.get('blueprint_id') not in reviewed_ids:
                    raise ValueError('Call review_blueprint for this exact saved blueprint before construction; evaluate its findings and correct blocking issues.')
                result = self.submit_tool(name, arguments, player, expected_generation=generation)
                if generation != self.cancel_generation:
                    return
                self.building_context.task(name, result)
                blueprint_id = (result.get("result") or {}).get("blueprint_id")
                if name == "design_blueprint" and result.get("state") == "SUCCEEDED":
                    supersedes = (result.get("result") or {}).get("supersedes_id")
                    designed_ids.discard(supersedes); built_ids.discard(supersedes); verified_ids.discard(supersedes)
                    reviewed_ids.discard(supersedes)
                    designed_ids.add(blueprint_id)
                if name=='review_blueprint' and result.get('state')=='SUCCEEDED' and (result.get('result') or {}).get('blocking_count')==0:
                    reviewed_ids.add(blueprint_id)
                if name in {"build_blueprint", "resume_build"} and (result.get("result") or {}).get("verified"):
                    built_ids.add(blueprint_id)
                if name == "verify_blueprint" and (result.get("result") or {}).get("verified"):
                    verified_ids.add(blueprint_id)
                # The executor echoes arguments for API consumers; the model
                # already owns the design id and need not receive all cuboids.
                result.pop("arguments", None)
                if name == "survey_region" and len(json.dumps(result.get("result", {}), ensure_ascii=False).encode("utf-8")) > INPUT_BYTE_BUDGET // 2:
                    result["result"] = {k: v for k, v in result["result"].items() if k not in {"rows", "palette"}}
                    result["result"]["detail"] = "Exact scan archived on disk; this summary does not specify individual cells. Query smaller survey_region tiles."
                # Refresh perception after every real action. This is
                # especially important for server_command, whose success or
                # error is reported by Minecraft chat rather than the HTTP API.
                time.sleep(0.25)
                result["state_after"] = compact_state(http_json(API_BASE + "/v1/state", timeout=15))
                state = result["state_after"]
                if name == "say" and result.get("state") == "SUCCEEDED":
                    did_say = True
                if name not in {"say", "recall_building", "inspect_blueprint", "review_blueprint"} and result.get("state") == "SUCCEEDED":
                    did_action = True
                if name == "build_structure" and result.get("state") == "SUCCEEDED":
                    structure_needs_door = True
                    plan = result.get("result") or {}
                    if all(key in plan for key in ("x", "y", "z", "width")):
                        door = {
                            "x": int(plan["x"]) + int(plan["width"]) // 2,
                            "y": int(plan["y"]) + 1,
                            "z": int(plan["z"]),
                        }
                        messages.append({
                            "role": "system",
                            "content": "房屋主体已完成，但任务尚未收尾。门洞下半格为 {}。继续合成并放置与墙材对应的木门；完成前不要宣布整栋房子完成。".format(json.dumps(door, ensure_ascii=False)),
                        })
                if name == "place_block" and str(arguments.get("item", "")).endswith("_door") \
                        and result.get("state") == "SUCCEEDED":
                    structure_needs_door = False
            except Exception as error:
                result = {"state": "FAILED", "failure_code": "BRIDGE_ERROR", "failure_message": str(error)}
                self.building_context.task(name, result)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result, ensure_ascii=False),
            })
            if result.get("state")=="PAUSED":
                if generation==self.cancel_generation:
                    self.say_text(player,"施工已暂停，等你说继续再恢复。")
                return
            messages = self.building_context.compact_messages(messages, message, state, tools=tools)
        self.say_text(player, "这件事步骤太多了，我先停一下重新想想。")

    def handle_fast_control(self, event):
        with self.submission_lock:
            self.cancel_generation += 1
        # Pending commands predate the owner's stop and must not restart work.
        while True:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                break
        player = event.get("player") or OWNER
        message = event.get("message") or ""
        try:
            if "重置" in message:
                self.histories[player].clear()
            result = self.submit_tool("stop", {})
            if result.get("state") == "SUCCEEDED":
                self.say_text(player, "已经停下来了。")
            else:
                self.say_text(player, "停止失败了：" + (result.get("failureMessage") or result.get("failure_message") or "未知原因"))
        except Exception as error:
            print("[wife-agent] 紧急停止失败：" + str(error), flush=True)

    def worker(self):
        while self.running:
            event = self.pending.get()
            self.active_request_text = (event.get("message") or "").strip()
            try:
                self.handle(event)
            except Exception as error:
                print("[wife-agent] 处理消息失败：" + str(error), flush=True)
            finally:
                self.active_request_text = ""

    def run(self):
        if len(self.api_key) < 20:
            raise RuntimeError("DEEPSEEK_API_KEY.ini 中没有可用的 Key")
        print("[wife-agent] DeepSeek 规划器已启动，等待 Wife NG 登录。", flush=True)
        threading.Thread(target=self.worker, name="wife-agent-worker", daemon=True).start()
        initialized = False
        while self.running:
            try:
                state = http_json(API_BASE + "/v1/state", timeout=10)
                events = state.get("recent_chat", [])
                if not initialized:
                    for event in events:
                        self.seen.append((event.get("captured_at"), event.get("text")))
                        self.recent_texts[event.get("text", "")] = time.monotonic()
                    initialized = True
                for event in events:
                    key = (event.get("captured_at"), event.get("text"))
                    if key in self.seen:
                        continue
                    self.seen.append(key)
                    player = event.get("player", "")
                    text = event.get("text", "")
                    message = event.get("message", "")
                    # Server command, teleport, join/leave and other system
                    # lines can mention Wu_Dabin, but they are observations,
                    # not new owner requests. Never start a planning turn from
                    # them.
                    if event.get("kind") != "chat" or not player:
                        continue
                    now = time.monotonic()
                    if now - self.recent_texts.get(text, -1000) < 8:
                        continue
                    self.recent_texts[text] = now
                    if len(self.recent_texts) > 300:
                        cutoff = now - 60
                        self.recent_texts = {item: seen_at for item, seen_at in self.recent_texts.items()
                                             if seen_at >= cutoff}
                    if player.lower() == BOT_NAME.lower():
                        continue
                    if BOT_NAME.lower() not in message.lower() and OWNER not in text:
                        continue
                    if player.lower() == OWNER.lower() and is_cancel_request(message):
                        threading.Thread(target=self.handle_fast_control, args=(event,),
                                         name="wife-agent-control", daemon=True).start()
                        continue
                    if player.lower() == OWNER.lower() and message.strip() \
                            and message.strip() == self.active_request_text:
                        # The owner may repeat a command because a long action
                        # is quiet. The active plan will emit progress; do not
                        # enqueue the same destructive/resource task twice.
                        continue
                    try:
                        self.pending.put_nowait(event)
                    except queue.Full:
                        print("[wife-agent] 消息队列已满，丢弃一条旧请求。", flush=True)
                time.sleep(POLL_SECONDS)
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(2)


if __name__ == "__main__":
    WifeAgent().run()
