import io
import json
import zipfile
from flask import Flask, request, send_file

# Import your compiler and SVG constants from api/compiler.py
try:
  from api.compiler import DEFAULT_SVG, FullPyToScratchCompiler, SVG_MD5
except ImportError:
  from compiler import DEFAULT_SVG, FullPyToScratchCompiler, SVG_MD5

app = Flask(__name__)


@app.route("/compile", methods=["POST"])
def compile_route():
  data = request.get_json()
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
    return {"error": str(e)}, 400

