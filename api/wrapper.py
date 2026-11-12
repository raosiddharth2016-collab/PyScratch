import ast
import hashlib
import json
import uuid
import zipfile

DEFAULT_SVG = """<svg version="1.1" xmlns="http://w3.org" width="48" height="48" viewBox="0 0 48 48">
  <circle cx="24" cy="24" r="20" fill="#4C97FF" stroke="#3373CC" stroke-width="3"/>
</svg>"""
SVG_MD5 = hashlib.md5(DEFAULT_SVG.encode("utf-8")).hexdigest()


class FullPyToScratchCompiler(ast.NodeVisitor):

  def __init__(self):
    self.blocks = {}
    self.variables = {}  # name -> [var_id, default_val]
    self.lists = {}  # name -> [list_id, default_list]
    self.extensions = set()  # Tracks 'pen', 'text2speech', 'music', etc.
    self.procedures = {}  # func_name -> proccode
    self.last_block_id = None
    self.script_roots = []

  def _new_id(self):
    return str(uuid.uuid4())[:16]

  def _get_var_id(self, name):
    if name not in self.variables:
      self.variables[name] = [self._new_id(), 0]
    return self.variables[name][0]

  def _get_list_id(self, name):
    if name not in self.lists:
      self.lists[name] = [self._new_id(), []]
    return self.lists[name][0]

  def _append_block(
      self, opcode, inputs=None, fields=None, shadow=False, mutation=None
  ):
    block_id = self._new_id()
    is_top = self.last_block_id is None
    block_data = {
        "opcode": opcode,
        "next": None,
        "parent": self.last_block_id,
        "inputs": inputs or {},
        "fields": fields or {},
        "shadow": shadow,
        "topLevel": is_top,
    }
    if mutation:
      block_data["mutation"] = mutation

    if is_top:
      block_data["x"] = 50 + (len(self.script_roots) * 320)
      block_data["y"] = 50
      self.script_roots.append(block_id)
    else:
      self.blocks[self.last_block_id]["next"] = block_id

    self.blocks[block_id] = block_data
    self.last_block_id = block_id
    return block_id

  def _create_standalone_block(
      self, opcode, parent_id, inputs=None, fields=None, shadow=False
  ):
    block_id = self._new_id()
    self.blocks[block_id] = {
        "opcode": opcode,
        "next": None,
        "parent": parent_id,
        "inputs": inputs or {},
        "fields": fields or {},
        "shadow": shadow,
        "topLevel": False,
    }
    return block_id

  def _compile_substack(self, stmts, parent_id):
    """Compiles an indented block of statements (for if, else, while, for, def)."""
    if not stmts:
      return None
    saved_last = self.last_block_id
    self.last_block_id = None
    first_sub_id = None

    for stmt in stmts:
      prev_last = self.last_block_id
      self.visit(stmt)
      if first_sub_id is None and self.last_block_id is not None:
        # Grab the root of this sub-chain
        curr = self.last_block_id
        while self.blocks[curr]["parent"] is not None:
          curr = self.blocks[curr]["parent"]
        first_sub_id = curr
        # Remove from top-level script roots
        if first_sub_id in self.script_roots:
          self.script_roots.remove(first_sub_id)
        self.blocks[first_sub_id]["parent"] = parent_id
        self.blocks[first_sub_id]["topLevel"] = False
        self.blocks[first_sub_id].pop("x", None)
        self.blocks[first_sub_id].pop("y", None)

    self.last_block_id = saved_last
    return [2, first_sub_id] if first_sub_id else None

  # ==========================================
  # EXPRESSIONS (Values, Math, Booleans, Reporters)
  # ==========================================
  def _parse_expr(self, node, parent_id):
    if node is None:
      return [1, [10, ""]]

    # Numbers, Strings, Booleans
    if isinstance(node, ast.Constant):
      if isinstance(node.value, bool):
        op = "operator_not" if not node.value else "operator_equals"
        if node.value:
          b_id = self._create_standalone_block(
              "operator_equals",
              parent_id,
              {"OPERAND1": [1, [10, "1"]], "OPERAND2": [1, [10, "1"]]},
          )
        else:
          b_id = self._create_standalone_block("operator_not", parent_id)
        return [2, b_id]
      elif isinstance(node.value, (int, float)):
        return [1, [4, str(node.value)]]
      return [1, [10, str(node.value)]]

    # Variables
    elif isinstance(node, ast.Name):
      if node.id == "answer":
        b_id = self._create_standalone_block("sensing_answer", parent_id)
        return [3, b_id, [10, ""]]
      elif node.id == "mouse_x":
        b_id = self._create_standalone_block("sensing_mousex", parent_id)
        return [3, b_id, [4, ""]]
      elif node.id == "mouse_y":
        b_id = self._create_standalone_block("sensing_mousey", parent_id)
        return [3, b_id, [4, ""]]
      var_id = self._get_var_id(node.id)
      return [3, [12, node.id, var_id], [10, ""]]

    # List Indexing: my_list[0] -> Scratch is 1-indexed!
    elif isinstance(node, ast.Subscript):
      if isinstance(node.value, ast.Name):
        list_name = node.value.id
        list_id = self._get_list_id(list_name)
        b_id = self._create_standalone_block(
            "data_itemoflist", parent_id, fields={"LIST": [list_name, list_id]}
        )
        # Add 1 to Python 0-based index
        add_id = self._create_standalone_block(
            "operator_add",
            b_id,
            {
                "NUM1": self._parse_expr(node.slice, b_id),
                "NUM2": [1, [4, "1"]],
            },
        )
        self.blocks[b_id]["inputs"]["INDEX"] = [3, add_id, [7, "1"]]
        return [3, b_id, [10, ""]]

    # Math Operations (+, -, *, /, %)
    elif isinstance(node, ast.BinOp):
      op_map = {
          ast.Add: "operator_add",
          ast.Sub: "operator_subtract",
          ast.Mult: "operator_multiply",
          ast.Div: "operator_divide",
          ast.Mod: "operator_mod",
      }
      opcode = op_map.get(type(node.op))
      if opcode:
        op_id = self._create_standalone_block(opcode, parent_id)
        self.blocks[op_id]["inputs"] = {
            "NUM1": self._parse_expr(node.left, op_id),
            "NUM2": self._parse_expr(node.right, op_id),
        }
        return [3, op_id, [4, ""]]

    # Comparisons (==, <, >, !=, <=, >=)
    elif isinstance(node, ast.Compare):
      left = node.left
      op = node.ops[0]
      right = node.comparators[0]

      if isinstance(op, ast.Eq):
        c_id = self._create_standalone_block("operator_equals", parent_id)
        self.blocks[c_id]["inputs"] = {
            "OPERAND1": self._parse_expr(left, c_id),
            "OPERAND2": self._parse_expr(right, c_id),
        }
        return [2, c_id]
      elif isinstance(op, ast.Lt):
        c_id = self._create_standalone_block("operator_lt", parent_id)
        self.blocks[c_id]["inputs"] = {
            "OPERAND1": self._parse_expr(left, c_id),
            "OPERAND2": self._parse_expr(right, c_id),
        }
        return [2, c_id]
      elif isinstance(op, ast.Gt):
        c_id = self._create_standalone_block("operator_gt", parent_id)
        self.blocks[c_id]["inputs"] = {
            "OPERAND1": self._parse_expr(left, c_id),
            "OPERAND2": self._parse_expr(right, c_id),
        }
        return [2, c_id]
      elif isinstance(op, ast.NotEq):
        not_id = self._create_standalone_block("operator_not", parent_id)
        eq_id = self._create_standalone_block("operator_equals", not_id)
        self.blocks[eq_id]["inputs"] = {
            "OPERAND1": self._parse_expr(left, eq_id),
            "OPERAND2": self._parse_expr(right, eq_id),
        }
        self.blocks[not_id]["inputs"] = {"OPERAND": [2, eq_id]}
        return [2, not_id]

    # Boolean Logic (and, or)
    elif isinstance(node, ast.BoolOp):
      opcode = (
          "operator_and" if isinstance(node.op, ast.And) else "operator_or"
      )
      b_id = self._create_standalone_block(opcode, parent_id)
      self.blocks[b_id]["inputs"] = {
          "OPERAND1": self._parse_expr(node.values[0], b_id),
          "OPERAND2": self._parse_expr(node.values[1], b_id),
      }
      return [2, b_id]

    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
      not_id = self._create_standalone_block("operator_not", parent_id)
      self.blocks[not_id]["inputs"] = {
          "OPERAND": self._parse_expr(node.operand, not_id)
      }
      return [2, not_id]

    # Reporter Function Calls (len, random, key_pressed, touching)
    elif isinstance(node, ast.Call):
      func_name = self._get_call_name(node.func)
      if func_name == "len" and node.args:
        if isinstance(node.args[0], ast.Name):
          l_name = node.args[0].id
          l_id = self._get_list_id(l_name)
          b_id = self._create_standalone_block(
              "data_lengthoflist", parent_id, fields={"LIST": [l_name, l_id]}
          )
          return [3, b_id, [4, ""]]
      elif func_name in ("random", "randint") and len(node.args) >= 2:
        b_id = self._create_standalone_block("operator_random", parent_id)
        self.blocks[b_id]["inputs"] = {
            "FROM": self._parse_expr(node.args[0], b_id),
            "TO": self._parse_expr(node.args[1], b_id),
        }
        return [3, b_id, [4, ""]]
      elif func_name == "key_pressed" and node.args:
        b_id = self._create_standalone_block("sensing_keypressed", parent_id)
        menu_id = self._create_standalone_block(
            "sensing_keyoptions",
            b_id,
            fields={"KEY_OPTION": [str(node.args[0].value), None]},
            shadow=True,
        )
        self.blocks[b_id]["inputs"] = {"KEY_OPTION": [1, menu_id]}
        return [2, b_id]

    return [1, [10, ""]]

  def _get_call_name(self, func_node):
    if isinstance(func_node, ast.Name):
      return func_node.id
    elif isinstance(func_node, ast.Attribute):
      if isinstance(func_node.value, ast.Name):
        return f"{func_node.value.id}.{func_node.attr}"
    return ""

  # ==========================================
  # STATEMENTS (Variables, Lists, Control, Extensions)
  # ==========================================
  def visit_Assign(self, node):
if isinstance(node.targets[0], ast.Name):
var_name = node.targets[0].id
# List initialization: my_list = [1, 2, 3]
if isinstance(node.value, ast.List):
list_id = self._get_list_id(var_name)
del_id = self._append_block(
"data_deletealloflist", fields={"LIST": [var_name, list_id]}
)
for elt in node.value.elts:
add_id = self._append_block(
"data_addtolist", fields={"LIST": [var_name, list_id]}
)
self.blocks[add_id]["inputs"]["ITEM"] = self._parse_expr(elt, add_id)
else:
var_id = self._get_var_id(var_name)
b_id = self._append_block(
"data_setvariableto", fields={"VARIABLE": [var_name, var_id]}
)
self.blocks[b_id]["inputs"]["VALUE"] = self._parse_expr(
node.value, b_id
)
def visit_AugAssign(self, node):
# Handles x += 5 or x -= 2
if isinstance(node.target, ast.Name) and isinstance(
node.op, (ast.Add, ast.Sub)
):
var_name = node.target.id
var_id = self._get_var_id(var_name)
b_id = self._append_block(
"data_changevariableby", fields={"VARIABLE": [var_name, var_id]}
)
if isinstance(node.op, ast.Add):
self.blocks[b_id]["inputs"]["VALUE"] = self._parse_expr(
node.value, b_id
)
else:
# Multiply by -1 for -=
mul_id = self._create_standalone_block(
"operator_multiply",
b_id,
{
"NUM1": self._parse_expr(node.value, b_id),
"NUM2": [1, [4, "-1"]],
},
)
self.blocks[b_id]["inputs"]["VALUE"] = [3, mul_id, [4, ""]]
def visit_If(self, node):
if node.orelse:
b_id = self._append_block("control_if_else")
self.blocks[b_id]["inputs"]["CONDITION"] = self._parse_expr(
node.test, b_id
)
sub1 = self._compile_substack(node.body, b_id)
sub2 = self._compile_substack(node.orelse, b_id)
if sub1:
self.blocks[b_id]["inputs"]["SUBSTACK"] = sub1
if sub2:
self.blocks[b_id]["inputs"]["SUBSTACK2"] = sub2
else:
b_id = self._append_block("control_if")
self.blocks[b_id]["inputs"]["CONDITION"] = self._parse_expr(
node.test, b_id
)
sub1 = self._compile_substack(node.body, b_id)
if sub1:
self.blocks[b_id]["inputs"]["SUBSTACK"] = sub1
def visit_While(self, node):
# while True: -> control_forever
if isinstance(node.test, ast.Constant) and node.test.value is True:
b_id = self._append_block("control_forever")
sub = self._compile_substack(node.body, b_id)
if sub:
self.blocks[b_id]["inputs"]["SUBSTACK"] = sub
else:
# while condition: -> repeat until not(condition)
b_id = self._append_block("control_repeat_until")
not_id = self._create_standalone_block("operator_not", b_id)
self.blocks[not_id]["inputs"]["OPERAND"] = self._parse_expr(
node.test, not_id
)
self.blocks[b_id]["inputs"]["CONDITION"] = [2, not_id]
sub = self._compile_substack(node.body, b_id)
if sub:
self.blocks[b_id]["inputs"]["SUBSTACK"] = sub
def visit_For(self, node):
if (
isinstance(node.iter, ast.Call)
and getattr(node.iter.func, "id", "") == "range"
):
b_id = self._append_block("control_repeat")
self.blocks[b_id]["inputs"]["TIMES"] = self._parse_expr(
node.iter.args[0], b_id
)
sub = self._compile_substack(node.body, b_id)
if sub:
self.blocks[b_id]["inputs"]["SUBSTACK"] = sub
def visit_FunctionDef(self, node):
# Compiles Python def my_func(): into Scratch "My Blocks"
func_name = node.name
self.procedures[func_name] = func_name
saved_last = self.last_block_id
self.last_block_id = None
def_id = self._append_block("procedures_definition")
proto_id = self._create_standalone_block(
"procedures_prototype",
def_id,
shadow=True,
mutation={
"tagName": "mutation",
"children": [],
"proccode": func_name,
"argumentids": "[]",
"argumentnames": "[]",
"argumentdefaults": "[]",
"warp": "false",
},
)
self.blocks[def_id]["inputs"]["custom_block"] = [1, proto_id]
for stmt in node.body:
self.visit(stmt)
self.last_block_id = saved_last
def visit_Expr(self, node):
if not isinstance(node.value, ast.Call):
return
call = node.value
func_name = self._get_call_name(call.func)
args = call.args
# --- CUSTOM FUNCTIONS ---
if func_name in self.procedures:
self._append_block(
"procedures_call",
mutation={
"tagName": "mutation",
"children": [],
"proccode": func_name,
"argumentids": "[]",
"warp": "false",
},
)
# --- LIST METHODS (my_list.append(x), my_list.clear()) ---
elif isinstance(call.func, ast.Attribute) and isinstance(
call.func.value, ast.Name
):
obj_name = call.func.value.id
method = call.func.attr
if method == "append" and args:
l_id = self._get_list_id(obj_name)
b_id = self._append_block(
"data_addtolist", fields={"LIST": [obj_name, l_id]}
)
self.blocks[b_id]["inputs"]["ITEM"] = self._parse_expr(args[0], b_id)
return
elif method == "clear":
l_id = self._get_list_id(obj_name)
self._append_block(
"data_deletealloflist", fields={"LIST": [obj_name, l_id]}
)
return
# --- LOOKS & SENSING ---
if func_name == "print" and args:
b_id = self._append_block("looks_sayforsecs")
self.blocks[b_id]["inputs"]["MESSAGE"] = self._parse_expr(args[0], b_id)
self.blocks[b_id]["inputs"]["SECS"] = [1, [4, "2"]]
elif func_name == "say" and args:
b_id = self._append_block("looks_say")
self.blocks[b_id]["inputs"]["MESSAGE"] = self._parse_expr(args[0], b_id)
elif func_name == "ask" and args:
b_id = self._append_block("sensing_askandwait")
self.blocks[b_id]["inputs"]["QUESTION"] = self._parse_expr(args[0], b_id)
elif func_name == "wait" and args:
b_id = self._append_block("control_wait")
self.blocks[b_id]["inputs"]["DURATION"] = self._parse_expr(args[0], b_id)
elif func_name == "broadcast" and args:
b_id = self._append_block("event_broadcast")
bcast_name = str(args[0].value)
self.blocks[b_id]["inputs"]["BROADCAST_INPUT"] = [
1,
[11, bcast_name, self._new_id()],
]
# --- MOTION ---
elif func_name == "move" and args:
b_id = self._append_block("motion_movesteps")
self.blocks[b_id]["inputs"]["STEPS"] = self._parse_expr(args[0], b_id)
elif func_name == "turn_right" and args:
b_id = self._append_block("motion_turnright")
self.blocks[b_id]["inputs"]["DEGREES"] = self._parse_expr(args[0], b_id)
elif func_name == "turn_left" and args:
b_id = self._append_block("motion_turnleft")
self.blocks[b_id]["inputs"]["DEGREES"] = self._parse_expr(args[0], b_id)
elif func_name == "goto" and len(args) >= 2:
b_id = self._append_block("motion_gotoxy")
self.blocks[b_id]["inputs"]["X"] = self._parse_expr(args[0], b_id)
self.blocks[b_id]["inputs"]["Y"] = self._parse_expr(args[1], b_id)
# --- EXTENSION 1: PEN ---
elif func_name.startswith("pen."):
self.extensions.add("pen")
action = func_name.split(".")[1]
if action == "clear":
self._append_block("pen_clear")
elif action == "stamp":
self._append_block("pen_stamp")
elif action == "down":
self._append_block("pen_penDown")
elif action == "up":
self._append_block("pen_penUp")
elif action == "set_color" and args:
b_id = self._append_block("pen_setPenColorToColor")
self.blocks[b_id]["inputs"]["COLOR"] = [1, [9, str(args[0].value)]]
elif action == "set_size" and args:
b_id = self._append_block("pen_setPenSizeTo")
self.blocks[b_id]["inputs"]["SIZE"] = self._parse_expr(args[0], b_id)
elif action == "change_size" and args:
b_id = self._append_block("pen_changePenSizeBy")
self.blocks[b_id]["inputs"]["SIZE"] = self._parse_expr(args[0], b_id)
# --- EXTENSION 2: TEXT TO SPEECH ---
elif func_name.startswith("tts."):
self.extensions.add("text2speech")
action = func_name.split(".")[1]
if action == "speak" and args:
b_id = self._append_block("text2speech_speakAndWait")
self.blocks[b_id]["inputs"]["WORDS"] = self._parse_expr(args[0], b_id)
elif action == "set_voice" and args:
b_id = self._append_block("text2speech_setVoice")
menu_id = self._create_standalone_block(
"text2speech_menu_voices",
b_id,
fields={"voices": [str(args[0].value).upper(), None]},
shadow=True,
)
self.blocks[b_id]["inputs"]["VOICE"] = [1, menu_id]
elif action == "set_language" and args:
b_id = self._append_block("text2speech_setLanguage")
menu_id = self._create_standalone_block(
"text2speech_menu_languages",
b_id,
fields={"languages": [str(args[0].value), None]},
shadow=True,
)
self.blocks[b_id]["inputs"]["LANGUAGE"] = [1, menu_id]
# --- EXTENSION 3: MUSIC ---
elif func_name.startswith("music."):
self.extensions.add("music")
action = func_name.split(".")[1]
if action == "play_note" and len(args) >= 2:
b_id = self._append_block("music_playNoteForBeats")
self.blocks[b_id]["inputs"]["NOTE"] = self._parse_expr(args[0], b_id)
self.blocks[b_id]["inputs"]["BEATS"] = self._parse_expr(args[1], b_id)
elif action == "play_drum" and len(args) >= 2:
b_id = self._append_block("music_playDrumForBeats")
menu_id = self._create_standalone_block(
"music_menu_DRUM",
b_id,
fields={"DRUM": [str(args[0].value), None]},
shadow=True,
)
self.blocks[b_id]["inputs"]["DRUM"] = [1, menu_id]
self.blocks[b_id]["inputs"]["BEATS"] = self._parse_expr(args[1], b_id)
elif action == "set_instrument" and args:
b_id = self._append_block("music_setInstrument")
menu_id = self._create_standalone_block(
"music_menu_INSTRUMENT",
b_id,
fields={"INSTRUMENT": [str(args[0].value), None]},
shadow=True,
)
self.blocks[b_id]["inputs"]["INSTRUMENT"] = [1, menu_id]
elif action == "set_tempo" and args:
b_id = self._append_block("music_setTempo")
self.blocks[b_id]["inputs"]["TEMPO"] = self._parse_expr(args[0], b_id)

==========================================


COMPILER ENTRY & JSON BUILDER


==========================================

def compile(self, python_code):
tree = ast.parse(python_code)
# First pass: compile top-level function definitions separately
main_body = []
for node in tree.body:
if isinstance(node, ast.FunctionDef):
self.visit(node)
else:
main_body.append(node)
# Second pass: attach main script to Green Flag
self.last_block_id = None
self._append_block("event_whenflagclicked")
for node in main_body:
self.visit(node)
return self._build_project_json()
def _build_project_json(self):
stage_vars = {
v_id: [name, default] for name, (v_id, default) in self.variables.items()
}
stage_lists = {
l_id: [name, default] for name, (l_id, default) in self.lists.items()
}
return {
"targets": [
{
"isStage": True,
"name": "Stage",
"variables": stage_vars,
"lists": stage_lists,
"broadcasts": {},
"blocks": {},
"comments": {},
"currentCostume": 0,
"costumes": [{
"name": "backdrop1",
"dataFormat": "svg",
"assetId": SVG_MD5,
"md5ext": f"{SVG_MD5}.svg",
"rotationCenterX": 240,
"rotationCenterY": 180,
}],
"sounds": [],
"volume": 100,
"layerOrder": 0,
},
{
"isStage": False,
"name": "PythonSprite",
"variables": {},
"lists": {},
"broadcasts": {},
"blocks": self.blocks,
"comments": {},
"currentCostume": 0,
"costumes": [{
"name": "costume1",
"bitmapResolution": 1,
"dataFormat": "svg",
"assetId": SVG_MD5,
"md5ext": f"{SVG_MD5}.svg",
"rotationCenterX": 24,
"rotationCenterY": 24,
}],
"sounds": [],
"volume": 100,
"layerOrder": 1,
"visible": True,
"x": 0,
"y": 0,
"size": 100,
"direction": 90,
"draggable": False,
"rotationStyle": "all around",
},
],
"monitors": [],
"extensions": sorted(list(self.extensions)),
"meta": {
"semver": "3.0.0",
"vm": "0.2.0",
"agent": "FullPyToScratchCompiler/2.0",
},
}
def compile_python_to_sb3(python_code, output_filename="full_project.sb3"):
compiler = FullPyToScratchCompiler()
project_data = compiler.compile(python_code)
with zipfile.ZipFile(output_filename, "w", zipfile.ZIP_DEFLATED) as sb3:
sb3.writestr("project.json", json.dumps(project_data, indent=2))
sb3.writestr(f"{SVG_MD5}.svg", DEFAULT_SVG)
print(f"Compiled successfully -> {output_filename}")
print(f"Active Scratch Extensions loaded: {list(compiler.extensions)}")

==========================================


FULL DEMO SCRIPT


==========================================

if name == "main":
demo_code = """
def draw_square():
pen.down()
for i in range(4):
move(60)
turn_right(90)
music.play_note(60, 0.25)
pen.up()

Setup extensions

pen.clear()
pen.set_color("#FF3366")
pen.set_size(4)
tts.set_voice("TENOR")
music.set_instrument(1)

Lists & Variables

notes = [60, 64, 67]
notes.append(72)
count = 0
tts.speak("Hello! Let us draw and play music.")
while count < 3:
draw_square()
turn_right(30)
count += 1
if len(notes) > 3:
print("Finished drawing with 4 notes!")
else:
print("Not enough notes.")
"""
compile_python_to_sb3(demo_code, "full_project.sb3")
