import ast
import hashlib
import uuid

DEFAULT_SVG = '<svg xmlns="http://w3.org" width="48" height="48"><circle cx="24" cy="24" r="20" fill="#4C97FF"/></svg>'
SVG_MD5 = hashlib.md5(DEFAULT_SVG.encode("utf-8")).hexdigest()


class FullPyToScratchCompiler(ast.NodeVisitor):

  def __init__(self):
    self.blocks = {}
    self.variables = {}
    self.extensions = set()
    self.last_id = None

  def _uid(self):
    return str(uuid.uuid4())[:16]

  def _var(self, name):
    if name not in self.variables:
      self.variables[name] = [self._uid(), 0]
    return self.variables[name][0]

  def _add(self, opcode, inputs=None, fields=None):
    bid = self._uid()
    self.blocks[bid] = {
        "opcode": opcode,
        "next": None,
        "parent": self.last_id,
        "inputs": inputs or {},
        "fields": fields or {},
        "shadow": False,
        "topLevel": self.last_id is None,
    }
    if self.last_id is None:
      self.blocks[bid]["x"] = 50
      self.blocks[bid]["y"] = 50
    else:
      self.blocks[self.last_id]["next"] = bid
    self.last_id = bid
    return bid

  def _sub(self, opcode, parent, inputs=None, fields=None, shadow=False):
    bid = self._uid()
    self.blocks[bid] = {
        "opcode": opcode,
        "next": None,
        "parent": parent,
        "inputs": inputs or {},
        "fields": fields or {},
        "shadow": shadow,
        "topLevel": False,
    }
    return bid

  def _expr(self, node, parent):
    if isinstance(node, ast.Constant):
      t = 4 if isinstance(node.value, (int, float)) else 10
      return [1, [t, str(node.value)]]
    if isinstance(node, ast.Name):
      return [3, [12, node.id, self._var(node.id)], [10, ""]]
    if isinstance(node, ast.BinOp):
      ops = {
          ast.Add: "operator_add",
          ast.Sub: "operator_subtract",
          ast.Mult: "operator_multiply",
          ast.Div: "operator_divide",
      }
      op = ops.get(type(node.op))
      if op:
        oid = self._sub(op, parent)
        self.blocks[oid]["inputs"] = {
            "NUM1": self._expr(node.left, oid),
            "NUM2": self._expr(node.right, oid),
        }
        return [3, oid, [4, ""]]
    if isinstance(node, ast.Compare):
      ops = {
          ast.Eq: "operator_equals",
          ast.Lt: "operator_lt",
          ast.Gt: "operator_gt",
      }
      op = ops.get(type(node.ops[0]))
      if op:
        cid = self._sub(op, parent)
        self.blocks[cid]["inputs"] = {
            "OPERAND1": self._expr(node.left, cid),
            "OPERAND2": self._expr(node.comparators[0], cid),
        }
        return [2, cid]
    return [1, [10, ""]]

  def _body(self, stmts, parent):
    if not stmts:
      return None
    saved = self.last_id
    self.last_id = None
    first = None
    for s in stmts:
      self.visit(s)
      if not first and self.last_id:
        curr = self.last_id
        while self.blocks[curr]["parent"]:
          curr = self.blocks[curr]["parent"]
        first = curr
        self.blocks[first]["parent"] = parent
        self.blocks[first]["topLevel"] = False
        self.blocks[first].pop("x", None)
        self.blocks[first].pop("y", None)
    self.last_id = saved
    return [2, first] if first else None

  def visit_Assign(self, node):
    if isinstance(node.targets[0], ast.Name):
      name = node.targets[0].id
      bid = self._add(
          "data_setvariableto", fields={"VARIABLE": [name, self._var(name)]}
      )
      self.blocks[bid]["inputs"]["VALUE"] = self._expr(node.value, bid)

  def visit_AugAssign(self, node):
    if isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
      name = node.target.id
      bid = self._add(
          "data_changevariableby", fields={"VARIABLE": [name, self._var(name)]}
      )
      self.blocks[bid]["inputs"]["VALUE"] = self._expr(node.value, bid)

  def visit_For(self, node):
    if (
        isinstance(node.iter, ast.Call)
        and getattr(node.iter.func, "id", "") == "range"
    ):
      bid = self._add("control_repeat")
      self.blocks[bid]["inputs"]["TIMES"] = self._expr(node.iter.args[0], bid)
      sub = self._body(node.body, bid)
      if sub:
        self.blocks[bid]["inputs"]["SUBSTACK"] = sub

  def visit_While(self, node):
    bid = self._add("control_repeat_until")
    nid = self._sub("operator_not", bid)
    self.blocks[nid]["inputs"]["OPERAND"] = self._expr(node.test, nid)
    self.blocks[bid]["inputs"]["CONDITION"] = [2, nid]
    sub = self._body(node.body, bid)
    if sub:
      self.blocks[bid]["inputs"]["SUBSTACK"] = sub

  def visit_If(self, node):
    bid = self._add("control_if_else" if node.orelse else "control_if")
    self.blocks[bid]["inputs"]["CONDITION"] = self._expr(node.test, bid)
    s1 = self._body(node.body, bid)
    if s1:
      self.blocks[bid]["inputs"]["SUBSTACK"] = s1
    if node.orelse:
      s2 = self._body(node.orelse, bid)
      if s2:
        self.blocks[bid]["inputs"]["SUBSTACK2"] = s2

  def visit_Expr(self, node):
    if not isinstance(node.value, ast.Call):
      return
    fn = node.value.func
    args = node.value.args
    name = (
        fn.id
        if isinstance(fn, ast.Name)
        else (
            f"{fn.value.id}.{fn.attr}"
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
            else ""
        )
    )

    if name == "print" and args:
      bid = self._add("looks_sayforsecs", {"SECS": [1, [4, "2"]]})
      self.blocks[bid]["inputs"]["MESSAGE"] = self._expr(args[0], bid)
    elif name == "move" and args:
      bid = self._add("motion_movesteps")
      self.blocks[bid]["inputs"]["STEPS"] = self._expr(args[0], bid)
    elif name == "turn_right" and args:
      bid = self._add("motion_turnright")
      self.blocks[bid]["inputs"]["DEGREES"] = self._expr(args[0], bid)
    elif name == "turn_left" and args:
      bid = self._add("motion_turnleft")
      self.blocks[bid]["inputs"]["DEGREES"] = self._expr(args[0], bid)
    elif name == "wait" and args:
      bid = self._add("control_wait")
      self.blocks[bid]["inputs"]["DURATION"] = self._expr(args[0], bid)
    elif name == "pen.clear":
      self.extensions.add("pen")
      self._add("pen_clear")
    elif name == "pen.down":
      self.extensions.add("pen")
      self._add("pen_penDown")
    elif name == "pen.up":
      self.extensions.add("pen")
      self._add("pen_penUp")
    elif name == "pen.set_color" and args:
      self.extensions.add("pen")
      self._add("pen_setPenColorToColor", {"COLOR": [1, [9, str(args[0].value)]]})
    elif name == "pen.set_size" and args:
      self.extensions.add("pen")
      bid = self._add("pen_setPenSizeTo")
      self.blocks[bid]["inputs"]["SIZE"] = self._expr(args[0], bid)
    elif name == "tts.speak" and args:
      self.extensions.add("text2speech")
      bid = self._add("text2speech_speakAndWait")
      self.blocks[bid]["inputs"]["WORDS"] = self._expr(args[0], bid)
    elif name == "music.play_note" and len(args) >= 2:
      self.extensions.add("music")
      bid = self._add("music_playNoteForBeats")
      self.blocks[bid]["inputs"]["NOTE"] = self._expr(args[0], bid)
      self.blocks[bid]["inputs"]["BEATS"] = self._expr(args[1], bid)

  def compile(self, code):
    self._add("event_whenflagclicked")
    self.visit(ast.parse(code))
    stage_vars = {vid: [k, v] for k, (vid, v) in self.variables.items()}
    costume = {
        "name": "c1",
        "dataFormat": "svg",
        "assetId": SVG_MD5,
        "md5ext": f"{SVG_MD5}.svg",
        "rotationCenterX": 24,
        "rotationCenterY": 24,
    }
    return {
        "targets": [
            {
                "isStage": True,
                "name": "Stage",
                "variables": stage_vars,
                "lists": {},
                "broadcasts": {},
                "blocks": {},
                "comments": {},
                "currentCostume": 0,
                "costumes": [costume],
                "sounds": [],
                "volume": 100,
                "layerOrder": 0,
            },
            {
                "isStage": False,
                "name": "Sprite1",
                "variables": {},
                "lists": {},
                "broadcasts": {},
                "blocks": self.blocks,
                "comments": {},
                "currentCostume": 0,
                "costumes": [costume],
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
        "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": "PyScratch/1.0"},
    }
