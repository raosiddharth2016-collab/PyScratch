import io
import json
import traceback
import zipfile
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

# Safely import wrapper so any syntax error shows clearly instead of crashing
import_error = None
try:
  from api.wrapper import DEFAULT_SVG, FullPyToScratchCompiler, SVG_MD5
except Exception:
  try:
    from wrapper import DEFAULT_SVG, FullPyToScratchCompiler, SVG_MD5
  except Exception as e:
    import_error = traceback.format_exc()


@app.route("/", defaults={"path": ""}, methods=["POST", "GET"])
@app.route("/<path:path>", methods=["POST", "GET"])
def compile_route(path):
  if import_error:
    return jsonify({"error": f"Compiler startup error: {import_error}"}), 500

  if request.method == "GET":
    return jsonify({"status": "Compiler API is online!"})

  data = request.get_json(silent=True) or {}
  python_code = data.get("code", "")

  try:
    compiler = FullPyToScratchCompiler()
    project_data = compiler.compile(python_code)

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as sb3:
      sb3.writestr("project.json", json.dumps(project_data, indent=2))
      sb3.writestr(f"{SVG_MD5}.svg", DEFAULT_SVG)

    memory_file.seek(0)
    return send_file(
        memory_file,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name="scratch_project.sb3",
    )
  except Exception as e:
    return jsonify({"error": str(e)}), 400
