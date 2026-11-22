import sys, json, tempfile
sys.path.insert(0, r"D:\Repo\pipeforge")
sys.path.insert(0, r"D:\Repo\pipeforge\examples")
from pipeforge import LocalStorage, WorkerContext
from pipeforge.core import TaskRepository
from file_worker import FileWorker, publish

td = tempfile.TemporaryDirectory()
st = LocalStorage(td.name)
repo = TaskRepository(st)
task = json.loads(open(r"D:\Repo\pipeforge\pipeforge\schemas\examples\task.example.json", encoding="utf-8").read())
for k in task["stages"]: task["stages"][k] = {}
st.write_text("inbox_ref/input.txt", "hello pipeforge")
sha = st.sha256("inbox_ref/input.txt")
for r_ in task["source"]["reference"]:
    r_["path"] = "input.txt"; r_["sha256"] = sha; r_["width"] = 8; r_["height"] = 8
repo.submit(task)
w = FileWorker()
ctx = WorkerContext(st, task_dir="inbox_ref", workspace="tasks/work/TASK_demo")
w.validate_input(task, ctx)
res = w.execute(task, ctx)
w.validate_output(task, res, ctx)
print("execute:", res.success, "| sha:", res.output_hash[:12], "| metrics:", res.metrics)
marker = publish(st, task["task_id"], "tasks/work/TASK_demo/staging", "library/TASK_demo_v001")
print("published:", marker["status"], "| manifest sha:", marker["manifest_sha256"][:12])
print("PUBLISHED exists:", st.exists("library/TASK_demo_v001/PUBLISHED"))
# idempotency: second publish must be rejected
try:
    publish(st, task["task_id"], "tasks/work/TASK_demo/staging", "library/TASK_demo_v001")
    print("BUG: double publish allowed")
except FileExistsError:
    print("double publish: correctly rejected")
td.cleanup()