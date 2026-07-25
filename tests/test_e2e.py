# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedCallResult=false
"""T17 端到端集成测试。

覆盖核心流程：CSV 导入→列映射→翻译→校对→导出、ASAR 解包→修改→回封、
断点续传、术语表一致性、约束验证。测试默认使用 mock 翻译，避免依赖 Ollama。
"""

import os
import sys
import tempfile
import shutil
import subprocess
import time
import pytest
from contextlib import contextmanager
from datetime import datetime

pytest.skip("CSV/ASAR workflows were removed; only MTool JSON translation is supported.", allow_module_level=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser.csv_parser import parse_csv, serialize_csv
from translator.pipeline import TranslationPipeline
from translator.glossary import Glossary
from translator.constraints import validate, auto_wrap
from translator.checkpoint import load_progress, save_progress
from translator import checkpoint
from asar_handler import unpack, repack, get_asar_info


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT_DIR, "tests")
INTRO_CSV = os.path.join(TESTS_DIR, "intro.csv")
REPORT_PATH = os.path.join(ROOT_DIR, ".omo", "evidence", "task-17-e2e-report.txt")
LEARNINGS_PATH = os.path.join(
    ROOT_DIR, ".omo", "notepads", "local-game-translator", "learnings.md"
)
REAL_ASAR_PATH = r"F:\gal\d_765404\resources\app.asar"
REAL_GAME_EXE = r"F:\gal\d_765404\Touch Simulation Game.exe"


class SkipTest(Exception):
    """标记环境依赖缺失导致的跳过，不计为失败。"""


@contextmanager
def temporary_checkpoint_dir():
    """隔离检查点目录，避免端到端测试污染真实进度。"""
    original_dir = checkpoint.CHECKPOINT_DIR
    temp_dir = tempfile.mkdtemp(prefix="e2e_checkpoints_")
    checkpoint.CHECKPOINT_DIR = temp_dir
    try:
        yield temp_dir
    finally:
        checkpoint.CHECKPOINT_DIR = original_dir
        shutil.rmtree(temp_dir, ignore_errors=True)


def assert_csv_shape_matches(original, exported):
    assert exported["header"] == original["header"], "导出CSV表头必须与原CSV一致"
    assert len(exported["rows"]) == len(original["rows"]), "导出CSV行数必须与原CSV一致"
    assert exported["column_count"] == original["column_count"], "导出CSV列数必须与原CSV一致"
    for index, row in enumerate(exported["rows"]):
        assert len(row) == original["column_count"], f"第 {index + 1} 行列数不一致"


def contains_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def mock_translate(text, row_idx=None, col_idx=None):
    mapping = {
        "主人公": "主角",
        "ヒロイン": "女主角",
        "NPC": "路人",
        "こんにちは": "你好",
        "おはようございます": "早上好",
        "「これは多行の\nセリフです」": "这是多行的\n台词。",
        "キモ男": "猥琐男",
    }
    if "猥琐男" in text:
        return f"那个猥琐男出现了 {row_idx}-{col_idx}"
    return mapping.get(text, f"译文{row_idx}_{col_idx}")


def test_csv_roundtrip():
    """测试CSV导入→翻译→校对→导出往返。"""
    with tempfile.TemporaryDirectory(prefix="e2e_csv_") as tmpdir, temporary_checkpoint_dir():
        source_csv = os.path.join(tmpdir, "intro.csv")
        output_csv = os.path.join(tmpdir, "intro.translated.csv")
        shutil.copy2(INTRO_CSV, source_csv)

        original = parse_csv(source_csv)
        target_cells = [
            (row_idx, col_idx, row[col_idx])
            for row_idx, row in enumerate(original["rows"])
            for col_idx in (0, 1)
            if col_idx < len(row) and row[col_idx].strip()
        ]
        # 本测试只真实执行前5条翻译；额外目标通过检查点模拟已完成，避免超量调用。
        for row_idx, col_idx, text in target_cells[5:]:
            save_progress(source_csv, row_idx, col_idx, text, mock_translate(text, row_idx, col_idx), status="done")

        pipeline = TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json")))
        translated_cells = []

        def _translate_cell(text, row_idx, col_idx):
            translated_cells.append((row_idx, col_idx, text))
            if len(translated_cells) > 5:
                raise AssertionError("测试只允许翻译前5个非空目标单元格")
            return mock_translate(text, row_idx, col_idx)

        pipeline.translate_cell = _translate_cell
        translated = pipeline.translate_file(source_csv, output_csv, translate_columns=[0, 1])

        # 模拟校对：人工修改1条译文后重新导出。
        translated["rows"][0][1] = "你好，校对完成"
        serialize_csv(translated, output_csv)
        exported = parse_csv(output_csv)

        assert_csv_shape_matches(original, exported)
        assert len(translated_cells) == 5, "intro.csv 应只翻译前5个非空目标单元格"
        assert exported["rows"][0][1] == "你好，校对完成", "校对修改应写入导出CSV"
        translated_values = [row[col] for row in exported["rows"] for col in (0, 1) if row[col].strip()]
        assert any(contains_cjk(value) for value in translated_values), "翻译列应包含中文"


def test_csv_export_structure():
    """测试导出CSV与原CSV结构一致（行数、列数、表头）。"""
    with tempfile.TemporaryDirectory(prefix="e2e_structure_") as tmpdir:
        output_csv = os.path.join(tmpdir, "roundtrip.csv")
        original = parse_csv(INTRO_CSV)
        serialize_csv(original, output_csv)
        exported = parse_csv(output_csv)
        assert_csv_shape_matches(original, exported)


def find_first_csv(root_dir):
    for current_root, _, files in os.walk(root_dir):
        for name in files:
            if name.lower().endswith(".csv"):
                return os.path.join(current_root, name)
    return None


def make_minimal_asar_fixture(tmpdir):
    app_dir = os.path.join(tmpdir, "fixture_app")
    data_dir = os.path.join(app_dir, "dist", "data", "scenarios")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(app_dir, "package.json"), "w", encoding="utf-8") as f:
        f.write('{"name":"local-game-translator-e2e","main":"index.js","version":"1.0.0"}\n')
    with open(os.path.join(app_dir, "index.js"), "w", encoding="utf-8") as f:
        f.write("console.log('fixture game started')\n")
    serialize_csv(
        {
            "header": ["キャラクター", "セリフ"],
            "rows": [["NPC", "こんにちは"]],
            "column_count": 2,
        },
        os.path.join(data_dir, "event.csv"),
    )
    return app_dir


def test_asar_workflow():
    """测试ASAR解包→修改→回封，并在可用时验证游戏可启动。"""
    with tempfile.TemporaryDirectory(prefix="e2e_asar_") as tmpdir:
        fixture_app = make_minimal_asar_fixture(tmpdir)
        original_asar = os.path.join(tmpdir, "app.asar")
        extracted_dir = os.path.join(tmpdir, "extracted")
        repacked_asar = os.path.join(tmpdir, "app.repacked.asar")
        verify_dir = os.path.join(tmpdir, "verify")

        try:
            repack(fixture_app, original_asar)
            unpack(original_asar, extracted_dir)
        except (RuntimeError, FileNotFoundError) as exc:
            raise SkipTest(f"ASAR工具不可用，跳过ASAR工作流: {exc}")

        csv_path = find_first_csv(extracted_dir)
        assert csv_path, "解包后应能找到CSV文件"
        data = parse_csv(csv_path)
        data["rows"][0][1] = "ASAR回封测试译文"
        serialize_csv(data, csv_path)

        repack(extracted_dir, repacked_asar)
        info = get_asar_info(repacked_asar)
        assert info["file_count"] >= 3, "回封ASAR应保留fixture文件结构"
        assert any("event.csv" in path.replace("\\", "/") for path in info["files"]), "回封ASAR应包含CSV"

        unpack(repacked_asar, verify_dir)
        verified_csv = find_first_csv(verify_dir)
        assert verified_csv, "回封后再次解包应能找到CSV"
        verified_data = parse_csv(verified_csv)
        assert verified_data["rows"][0][1] == "ASAR回封测试译文", "CSV修改应在回封后保留"

        game_launch = "未配置真实游戏路径，fixture ASAR 已完成启动前结构验证"
        if os.path.isfile(REAL_GAME_EXE):
            proc = subprocess.Popen([REAL_GAME_EXE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
                game_launch = "真实游戏可启动并运行3秒"
            elif proc.returncode == 0:
                game_launch = "真实游戏可启动并正常退出"
            else:
                raise AssertionError(f"真实游戏启动失败，退出码: {proc.returncode}")
        return game_launch


def build_checkpoint_csv(path):
    serialize_csv(
        {
            "header": ["キャラクター", "セリフ"],
            "rows": [[f"NPC{i}", f"テスト{i}"] for i in range(1, 7)],
            "column_count": 2,
        },
        path,
    )


def test_checkpoint_resume():
    """测试断点续传。"""
    with tempfile.TemporaryDirectory(prefix="e2e_resume_") as tmpdir, temporary_checkpoint_dir():
        csv_path = os.path.join(tmpdir, "resume.csv")
        build_checkpoint_csv(csv_path)
        original = parse_csv(csv_path)
        targets = [(r, c, row[c]) for r, row in enumerate(original["rows"]) for c in (0, 1)]
        first_batch = targets[:5]

        for row_idx, col_idx, text in first_batch:
            save_progress(csv_path, row_idx, col_idx, text, f"已译:{text}", status="done")

        progress = load_progress(csv_path)
        assert len(progress) == 5, "模拟中断后应保存5条检查点"

        pipeline = TranslationPipeline()
        resumed_calls = []

        def _translate_cell(text, row_idx, col_idx):
            resumed_calls.append((row_idx, col_idx, text))
            return f"续译:{text}"

        pipeline.translate_cell = _translate_cell
        result = pipeline.translate_file(csv_path, translate_columns=[0, 1])

        assert resumed_calls == targets[5:], "恢复翻译应从第6条继续"
        assert len(resumed_calls) == len(set((r, c) for r, c, _ in resumed_calls)), "恢复过程不应重复翻译"
        final_progress = load_progress(csv_path)
        assert len(final_progress) == len(targets), "恢复完成后所有目标单元格应有检查点"
        assert result["rows"][0][0] == "已译:NPC1", "检查点译文应被复用"


def test_glossary_consistency():
    """测试术语表一致性。"""
    with tempfile.TemporaryDirectory(prefix="e2e_glossary_") as tmpdir:
        glossary = Glossary(file_path=os.path.join(tmpdir, "glossary.json"))
        glossary.add("キモ男", "猥琐男")
        pipeline = TranslationPipeline(glossary=glossary)
        result = pipeline.translate_cell("キモ男が来た", 0, 1)
        assert "猥琐男" in result, "翻译结果必须使用术语表指定译法"
        assert "キモ男" not in result, "翻译结果不应残留被术语表替换的原术语"


def test_constraint_enforcement():
    """测试约束验证。"""
    long_text = "这是一段很长的翻译文本，用来验证游戏文本框中的三十字符每行和四行每格约束会被自动修整。"
    wrapped = auto_wrap(long_text, max_chars=30, max_lines=4)
    assert validate(wrapped, max_chars=30, max_lines=4), "修整后的译文必须满足30字符/行、4行/单元格"
    assert len(wrapped.split("\n")) <= 4, "译文最多4行"
    assert all(len(line) <= 30 for line in wrapped.split("\n")), "每行最多30字符"


TESTS = [
    ("CSV导入→翻译→校对→导出", test_csv_roundtrip),
    ("导出CSV结构一致", test_csv_export_structure),
    ("ASAR解包→修改→回封→启动验证", test_asar_workflow),
    ("断点续传", test_checkpoint_resume),
    ("术语表一致性", test_glossary_consistency),
    ("约束验证", test_constraint_enforcement),
]


def run_all_tests():
    results = []
    for name, test_func in TESTS:
        started = time.perf_counter()
        try:
            detail = test_func()
            status = "PASS"
            message = detail or "通过"
        except SkipTest as exc:
            status = "SKIP"
            message = str(exc)
        except Exception as exc:
            status = "FAIL"
            message = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        results.append({"name": name, "status": status, "message": message, "elapsed": elapsed})
        print(f"[{status}] {name} ({elapsed:.2f}s) - {message}")
    return results


def write_report(results):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    passed = sum(1 for item in results if item["status"] == "PASS")
    skipped = sum(1 for item in results if item["status"] == "SKIP")
    failed = sum(1 for item in results if item["status"] == "FAIL")
    lines = [
        "# T17 端到端集成测试报告",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"汇总: PASS={passed}, SKIP={skipped}, FAIL={failed}",
        "",
    ]
    for item in results:
        lines.append(f"- [{item['status']}] {item['name']} ({item['elapsed']:.2f}s): {item['message']}")
    lines.append("")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def append_learnings(results):
    os.makedirs(os.path.dirname(LEARNINGS_PATH), exist_ok=True)
    failed = sum(1 for item in results if item["status"] == "FAIL")
    skipped = [item for item in results if item["status"] == "SKIP"]
    with open(LEARNINGS_PATH, "a", encoding="utf-8") as f:
        f.write("\n## T17: 端到端集成测试 (2026-06-13)\n\n")
        f.write("### 实现文件\n")
        f.write("- `tests/test_e2e.py` — mock-backed E2E 测试脚本，覆盖CSV、ASAR、断点续传、术语表、约束验证。\n")
        f.write(f"- `.omo/evidence/task-17-e2e-report.txt` — 最近一次运行报告。\n\n")
        f.write("### 验证结果\n")
        for item in results:
            f.write(f"- `{item['name']}`: {item['status']} — {item['message']}\n")
        f.write("\n### 关键发现\n")
        f.write("1. E2E 测试应直接 mock `TranslationPipeline.translate_cell`，避免真实 Ollama 服务依赖。\n")
        f.write("2. 检查点测试需临时替换 `translator.checkpoint.CHECKPOINT_DIR`，避免污染用户实际续传进度。\n")
        if skipped:
            f.write("3. ASAR 工作流在缺少 Node/npx/@electron/asar 时允许环境性 SKIP；有 npx 时会创建临时 fixture ASAR 完整回封验证。\n")
        else:
            f.write("3. ASAR 工作流可用临时 fixture ASAR 验证回封结构和CSV修改持久化，无需修改真实游戏文件。\n")
        f.write(f"\n### 总结\n- 失败数: {failed}\n")


if __name__ == "__main__":
    test_results = run_all_tests()
    write_report(test_results)
    append_learnings(test_results)
    failures = [item for item in test_results if item["status"] == "FAIL"]
    print(f"\n报告已写入: {REPORT_PATH}")
    if failures:
        sys.exit(1)
    sys.exit(0)
