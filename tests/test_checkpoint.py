"""验证断点续传功能 (T16)

测试场景:
1. 创建包含 10+ 可翻译单元格的 CSV
2. 模拟翻译前 5 条后中断 (通过 monkeypatch 控制)
3. 恢复翻译，验证从第 6 条继续
4. 验证检查点文件 JSON 格式正确
5. 验证无重复翻译
"""

import csv
import json
import os
import sys
import tempfile
import pytest

pytest.skip("CSV checkpoint workflow was removed; only MTool JSON translation is supported.", allow_module_level=True)

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from translator import checkpoint
from translator.pipeline import TranslationPipeline


TEST_CSV_PATH = os.path.join(os.path.dirname(__file__), "test_checkpoint.csv")
CHECKPOINT_DIR = ".checkpoints"


def _make_test_csv():
    """生成 6 行 × 2 列的测试 CSV（共 10 个可翻译单元格，排除空行）。"""
    rows = [
        ["キャラクター", "セリフ"],
        ["主人公", "こんにちは"],
        ["ヒロイン", "おはよう"],
        ["NPC1", "テスト1"],
        ["NPC2", "テスト2"],
        ["NPC3", "テスト3"],
        ["NPC4", "テスト4"],
        ["NPC5", "テスト5"],
        ["NPC6", "テスト6"],
        ["NPC7", "テスト7"],
        ["NPC8", "テスト8"],
    ]
    with open(TEST_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerows(rows)
    return TEST_CSV_PATH


def _cleanup():
    if os.path.exists(TEST_CSV_PATH):
        os.remove(TEST_CSV_PATH)
    translated_path = TEST_CSV_PATH.replace(".csv", ".translated.csv")
    if os.path.exists(translated_path):
        os.remove(translated_path)
    checkpoint.clear_checkpoint(TEST_CSV_PATH)


def _mock_translate_factory(limit):
    """返回一个 mock translate 函数，翻译 limit 次后抛出异常模拟中断。"""
    call_count = 0

    def _mock_translate(model, text, system_prompt=None, terminology=None):
        nonlocal call_count
        call_count += 1
        if call_count > limit:
            raise RuntimeError("模拟中断")
        return f"[译]{text}"

    return _mock_translate


def test_checkpoint_resume():
    print("=" * 60)
    print("T16 断点续传验证")
    print("=" * 60)

    _cleanup()
    _make_test_csv()

    # 统计可翻译单元格数
    import parser.csv_parser as csv_parser
    data = csv_parser.parse_csv(TEST_CSV_PATH)
    rows = data["rows"]
    total_cells = sum(1 for row in rows for cell in row if cell and cell.strip())
    print(f"测试 CSV: {TEST_CSV_PATH}")
    print(f"总行数: {len(rows)}，可翻译单元格数: {total_cells}")

    # ---- 阶段 1: 翻译 5 条后中断 ----
    print("\n[阶段 1] 翻译 5 条后模拟中断...")
    pipeline = TranslationPipeline()
    original_translate = pipeline.translate_cell

    call_count = 0

    def _limited_translate_cell(text, row_idx, col_idx):
        nonlocal call_count
        call_count += 1
        if call_count > 5:
            raise RuntimeError("模拟中断")
        return f"[译]{text}"

    pipeline.translate_cell = _limited_translate_cell

    try:
        pipeline.translate_file(TEST_CSV_PATH)
    except RuntimeError as e:
        print(f"  中断触发: {e}")

    # 验证检查点
    cp_path = checkpoint.get_checkpoint_path(TEST_CSV_PATH)
    print(f"  检查点文件: {cp_path}")
    assert os.path.exists(cp_path), "检查点文件应存在"

    with open(cp_path, "r", encoding="utf-8") as f:
        cp_data = json.load(f)

    print(f"  检查点 JSON 键: {list(cp_data.keys())}")
    assert cp_data.get("version") == 2
    assert cp_data.get("file_path") == os.path.abspath(TEST_CSV_PATH)
    assert cp_data.get("file_name") == os.path.basename(TEST_CSV_PATH)
    assert "created_at" in cp_data
    assert "updated_at" in cp_data
    assert "entries" in cp_data
    assert "stats" in cp_data

    completed = checkpoint.get_translated_count(TEST_CSV_PATH)
    print(f"  已翻译条数: {completed}")
    assert completed == 5, f"期望 5，实际 {completed}"

    # 验证 entries 格式
    for key, entry in cp_data["entries"].items():
        assert "original" in entry
        assert "translated" in entry
        assert "status" in entry
        assert entry["status"] == "translated"

    print("  [OK] 检查点 JSON 格式正确")

    # ---- 阶段 2: 恢复翻译 ----
    print("\n[阶段 2] 恢复翻译...")
    pipeline2 = TranslationPipeline()
    full_call_count = 0

    def _full_translate_cell(text, row_idx, col_idx):
        nonlocal full_call_count
        full_call_count += 1
        return f"[译]{text}"

    pipeline2.translate_cell = _full_translate_cell

    result = pipeline2.translate_file(TEST_CSV_PATH)

    # 验证总调用次数 = 总单元格数 - 已翻译 5 条
    print(f"  恢复后 translate_cell 调用次数: {full_call_count}")
    assert full_call_count == total_cells - 5, f"期望 {total_cells - 5}，实际 {full_call_count}"

    # 验证输出文件
    translated_path = TEST_CSV_PATH.replace(".csv", ".translated.csv")
    assert os.path.exists(translated_path), "输出文件应存在"

    output_data = csv_parser.parse_csv(translated_path)
    output_rows = output_data["rows"]

    # 验证所有可翻译单元格都已翻译
    for row in output_rows:
        for cell in row:
            if cell and cell.strip() and not cell.startswith("[译]"):
                # 表头不检查
                pass

    # 更严格的验证：所有非空数据行单元格都带 [译] 前缀
    for row in output_rows:
        for cell in row:
            if cell and cell.strip():
                assert cell.startswith("[译]"), f"单元格未翻译: {cell}"

    print("  [OK] 恢复后从第 6 条继续，无重复翻译")

    # 验证最终检查点 stats
    final_completed = checkpoint.get_translated_count(TEST_CSV_PATH)
    print(f"  最终已翻译条数: {final_completed}")
    assert final_completed == total_cells, f"期望 {total_cells}，实际 {final_completed}"

    # ---- 阶段 3: 再次运行（全部已翻译）----
    print("\n[阶段 3] 再次运行（应全部跳过）...")
    pipeline3 = TranslationPipeline()
    retranslate_count = 0

    def _counting_translate_cell(text, row_idx, col_idx):
        nonlocal retranslate_count
        retranslate_count += 1
        return f"[译]{text}"

    pipeline3.translate_cell = _counting_translate_cell
    pipeline3.translate_file(TEST_CSV_PATH)

    print(f"  再次运行 translate_cell 调用次数: {retranslate_count}")
    assert retranslate_count == 0, f"期望 0，实际 {retranslate_count}"
    print("  [OK] 已翻译行完全跳过，无重复翻译")

    # ---- 阶段 4: 多文件独立检查点 ----
    print("\n[阶段 4] 多文件独立检查点验证...")
    other_csv = os.path.join(os.path.dirname(__file__), "test_checkpoint_other.csv")
    with open(other_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerows([["A", "B"], ["X", "Y"]])

    checkpoint.save_progress(other_csv, 0, 0, "X", "[译]X")
    cp_path_other = checkpoint.get_checkpoint_path(other_csv)
    assert cp_path_other != cp_path, "不同文件应有不同检查点路径"
    assert checkpoint.get_translated_count(other_csv) == 1
    assert checkpoint.get_translated_count(TEST_CSV_PATH) == total_cells, "原文件检查点不应受影响"
    print("  [OK] 多文件检查点相互独立")

    # 清理
    checkpoint.clear_checkpoint(other_csv)
    os.remove(other_csv)
    _cleanup()

    print("\n" + "=" * 60)
    print("T16 断点续传验证全部通过 [PASS]")
    print("=" * 60)


if __name__ == "__main__":
    test_checkpoint_resume()
