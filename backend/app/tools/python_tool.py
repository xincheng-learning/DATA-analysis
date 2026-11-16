"""Python/pandas execution tool via Docker sandbox.

The agent writes pandas code; we execute it inside a Docker container with:
  - No network access (--network=none)
  - Read-only data mount (only the session's Parquet files)
  - Memory limit (256MB)
  - Hard timeout (15s including container startup, then named-container cleanup)
  - Non-root user inside the container
  - No host env vars passed (API keys cannot leak)

For the LLM-generated code to work, the harness pre-loads data into `df`
(from a Parquet file) and captures `result` or stdout.
"""
from __future__ import annotations

import logging
import ast
import json
import math
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.config import settings

logger = logging.getLogger(__name__)

DOCKER_IMAGE = "datapilot-sandbox"
EXECUTION_TIMEOUT = 15  # seconds (includes container startup ~1-2s)
MEMORY_LIMIT = "256m"
RESULT_MARKER = '__DATAPILOT_RESULT_V1__'

# Imports we allow — validated before sending to Docker
ALLOWED_IMPORTS = {
    "pandas", "pd",
    "numpy", "np",
    "scipy", "scipy.stats",
    "sklearn", "sklearn.cluster", "sklearn.preprocessing", "sklearn.metrics",
    "statistics",
    "math",
    "datetime",
    "decimal",
}

_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
    re.MULTILINE,
)


def docker_command(*arguments: str) -> list[str]:
    prefix = ["wsl", "--distribution", settings.docker_wsl_distro, "--exec"] if settings.docker_wsl_distro else []
    return [*prefix, "docker", *arguments]


def _mount_path(path: Path) -> str:
    if settings.docker_bind_root:
        try:
            relative = path.resolve().relative_to(settings.local_data_dir)
        except ValueError:
            raise ValueError("隔离环境仅允许使用当前项目的数据目录。") from None
        return (PurePosixPath(settings.docker_bind_root) / relative.as_posix()).as_posix()
    if not settings.docker_wsl_distro:
        return path.resolve().as_posix()
    result = subprocess.run(["wsl", "--distribution", settings.docker_wsl_distro, "--exec", "wslpath", "-u", str(path.resolve())],
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    return result.stdout.strip()


def _readonly_mount(path: Path, target: str) -> list[str]:
    source = _mount_path(path)
    # Desktop accepts Windows sources; --mount avoids splitting their drive colon.
    if settings.docker_bind_root:
        return ["--mount", f"type=bind,source={source},target={target},readonly"]
    return ["-v", f"{source}:{target}:ro"]


@dataclass
class PythonResult:
    """Output of a Python code execution."""
    code: str
    output: str | None = None
    error: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    summary: str = ''
    retryable: bool = True

    @property
    def ok(self) -> bool:
        return self.error is None


def validate_imports(code: str) -> str | None:
    """Check all imports are from the allowed set. Returns error message or None."""
    for match in _IMPORT_RE.finditer(code):
        module = match.group(1).split(".")[0]
        if module not in ALLOWED_IMPORTS:
            return (
                f"Import '{match.group(1)}' is not allowed. "
                f"Permitted: pandas, numpy, scipy, sklearn, statistics, math, datetime, decimal."
            )
    return None


def validate_result_precision(code: str) -> str | None:
    try: tree=ast.parse(code)
    except SyntaxError: return None  # Docker returns the syntax diagnostic through the same repair loop.
    # shortcut: covers round() and .round(); extend when another lossy operation appears in execution evidence.
    if any(isinstance(node,ast.Call) and (isinstance(node.func,ast.Name) and node.func.id=='round'
            or isinstance(node.func,ast.Attribute) and node.func.attr=='round') for node in ast.walk(tree)):
        return '结果必须保留原始数值精度，未经用户明确要求不能调用 round；极小的p值不能舍入成零。请返回原始数值，由页面控制显示格式。本段代码未执行。'
    return None


def execute_python(code: str, parquet_paths: list[str] | None = None, *, table_names: list[str] | None = None) -> PythonResult:
    """Execute pandas code inside a Docker sandbox.

    Parameters
    ----------
    code : str
        LLM-generated Python code.
    parquet_paths : list[str] | None
        Paths to Parquet files on the host. Mounted read-only into the
        container at /data/. Tables are available as dfs[table_name]; df
        remains a convenience alias only when exactly one file is loaded.
    table_names : list[str] | None
        Registered table names in the same order as parquet_paths.
    """
    # Pre-validate imports before even touching Docker
    import_error = validate_imports(code)
    if import_error:
        return PythonResult(code=code, error=import_error)

    # Build the harness script
    harness = _build_harness(code, parquet_paths or [], table_names=table_names)
    container_name = f"datapilot-run-{uuid.uuid4().hex}"

    # Write harness to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8", dir=tempfile.gettempdir()
    ) as f:
        f.write(harness)
        script_path = Path(f.name)

    container_started = False
    try:
        # The sandbox runs as a different user and needs to read this script.
        script_path.chmod(0o644)
        # Build docker run command
        cmd = docker_command(
            "run", "--rm",
            "--name", container_name, "--pull=never",
            "--network=none",
            f"--memory={MEMORY_LIMIT}",
            "--read-only",
            "--tmpfs", "/tmp:size=50m",
            "--cpus=1",
            "--pids-limit=64",
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
        )

        # Mount parquet files read-only
        if parquet_paths:
            for p in parquet_paths:
                filename = Path(p).name
                cmd.extend(_readonly_mount(Path(p), f"/data/{filename}"))

        # Mount the script
        cmd.extend(_readonly_mount(script_path, "/home/sandbox/run.py"))

        cmd.extend([DOCKER_IMAGE, "/home/sandbox/run.py"])

        container_started = True
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=EXECUTION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        if not container_started:
            return PythonResult(code=code, error="WSL隔离环境启动或路径转换超时，未执行Python代码；请启动WSL后重试。", retryable=False)
        try:
            subprocess.run(docker_command("rm", "-f", container_name), capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Could not remove timed-out sandbox container")
        return PythonResult(code=code, error=f"Execution exceeded {EXECUTION_TIMEOUT}s timeout.")
    except FileNotFoundError:
        return PythonResult(code=code, error="Python 隔离环境不可用，已停止执行；不会在本机直接运行模型生成的代码。", retryable=False)
    except Exception as e:
        return PythonResult(code=code, error=f"Execution failed: {type(e).__name__}: {e}", retryable=False)
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        error_msg = (result.stderr or "Unknown error").strip()
        if "docker API" in error_msg or "daemon is running" in error_msg or "Cannot connect to the Docker" in error_msg:
            return PythonResult(code=code, error="Python 隔离环境尚未启动，已停止执行，请启动隔离环境后重试。", retryable=False)
        return PythonResult(code=code, error=error_msg[-500:], retryable=result.returncode not in {125,126,127})

    output = result.stdout.strip()
    if not output:
        return PythonResult(code=code, output="(code ran successfully but produced no output)")

    return _parse_output(code,output)


def _parse_output(code: str, output: str) -> PythonResult:
    """Validate the container's table receipt, keeping its readable output separately."""
    text,marker,payload=output.rpartition(RESULT_MARKER)
    if not marker:
        return PythonResult(code=code,output=output)
    try:
        table=json.loads(payload)
        if not isinstance(table,dict):
            raise ValueError('结果回执必须是对象。')
        text=text.rstrip()
        if table.get('error'):
            return PythonResult(code=code,output=text,error=str(table['error']))
        columns,values=table['columns'],table['data']
        summary=table.get('summary','')
        if not isinstance(summary,str) or len(summary)>4000:
            raise ValueError('Python计算说明须为4000字以内的文本。')
        if not isinstance(columns,list) or not 1<=len(columns)<=200 or not all(isinstance(c,str) for c in columns) or len(set(columns))!=len(columns):
            raise ValueError('结果列名须为唯一文本，最多200列。')
        if len(values)>5000:
            raise ValueError('Python结果超过5000行，请先汇总或明确筛选；未截取部分结果。')
        if any(not isinstance(row,list) or len(row)!=len(columns) or any(
            not isinstance(v,(str,int,float,bool,type(None))) or isinstance(v,float) and not math.isfinite(v) for v in row) for row in values):
            raise ValueError('Python结果表必须包含有限数值、文本或明确空值。')
        return PythonResult(code=code,output=text,columns=columns,rows=[dict(zip(columns,row)) for row in values],summary=summary)
    except (ValueError,TypeError,KeyError) as exc:
        return PythonResult(code=code,output=text,error='Python结果格式未通过核对：'+str(exc))


def _build_harness(user_code: str, parquet_paths: list[str], *, table_names: list[str] | None = None) -> str:
    """Wrap user code in a harness that loads data and captures output.

    """
    load_lines = [
        "import pandas as pd",
        "import numpy as np",
        "import warnings",
        "import json as _receipt_json",
        "warnings.filterwarnings('ignore')",
    ]

    names = table_names if table_names is not None else [Path(p).stem for p in parquet_paths]
    if len(names) != len(parquet_paths) or len(set(names)) != len(names):
        raise ValueError("Python table names must uniquely match the input files")
    load_lines.append("dfs = {}")
    if parquet_paths:
        for i, p in enumerate(parquet_paths):
            path_ref = f"/data/{Path(p).name}"
            load_lines.append(f"df_{i} = pd.read_parquet({path_ref!r})")
            load_lines.append(f"dfs[{names[i]!r}] = df_{i}")
        if len(parquet_paths) == 1:
            load_lines.append("df = df_0")
    else:
        load_lines.append("df = pd.DataFrame()  # no data loaded")

    load_section = "\n".join(load_lines)

    return f"""\
{load_section}

# --- User code below ---
{user_code}
# --- User code above ---

# Capture result
if 'result' in dir():
    _r = eval('result')
    _table = _r if isinstance(_r, pd.DataFrame) else _r.to_frame().reset_index() if isinstance(_r,pd.Series) else pd.DataFrame([_r]) if isinstance(_r,dict) and _r and all(np.isscalar(v) or v is None for v in _r.values()) else None
    if _table is not None and (len(_table)>5000 or len(_table.columns)>200):
        print('\\n{RESULT_MARKER}'+_receipt_json.dumps({{'error':'Python结果最多5000行、200列，请先汇总或明确筛选；未截取部分结果。'}},ensure_ascii=False))
    else:
        if hasattr(_r, 'to_string'):
            print(_r.head(50).to_string())
            if len(_r)>50: print('文字预览仅前50行，完整结果另附结构化表。')
        else:
            print(_r)
        if _table is not None:
            # pandas.to_json rounds tiny nonzero floats even with higher double_precision.
            _receipt={{'columns':list(_table.columns),'data':_table.astype(object).where(pd.notna(_table),None).values.tolist()}}
            _receipt['summary']=_table.attrs.get('summary','')
            print('\\n{RESULT_MARKER}'+_receipt_json.dumps(_receipt,ensure_ascii=False,allow_nan=False,
                default=lambda value: value.isoformat() if hasattr(value,'isoformat') else value.item() if isinstance(value,np.generic) else str(value)))
"""
