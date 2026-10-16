from pathlib import Path
from smith.models.task import MBPPTaskInput, SWEBenchTaskInput

mbpp = MBPPTaskInput.model_validate_json(
    Path("cache/mbpp_task.json").read_text()
)
print(mbpp.model_dump_json(indent=2))

swe = SWEBenchTaskInput.model_validate_json(
    Path("cache/swebench_task.json").read_text()
)
print(swe.instance_id, swe.docker_image)
