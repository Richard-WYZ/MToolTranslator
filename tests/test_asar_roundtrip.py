"""
test_asar_roundtrip.py — ASAR 解包/回封往返完整性验证

验证步骤:
  1. 获取原始ASAR信息（大小、文件数、文件列表）
  2. 解包到临时目录
  3. 验证解包后的文件结构
  4. 回封为新ASAR
  5. 对比原始与回封的差异（大小 < 1%, 文件数一致）
  6. 清理临时文件
"""

import os
import sys
import pytest

pytest.skip("ASAR workflow was removed; only MTool JSON translation is supported.", allow_module_level=True)

import shutil
import tempfile

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asar_handler import get_asar_info, unpack, repack, verify_roundtrip


def main():
    # 目标ASAR路径
    asar_path = r"F:\gal\d_765404\resources\app.asar"

    if not os.path.isfile(asar_path):
        print(f"[SKIP] 目标ASAR不存在: {asar_path}")
        print("创建一个测试用小型ASAR来验证功能...")
        _run_test_with_dummy_asar()
        return

    print("=" * 60)
    print("ASAR 往返完整性验证")
    print("=" * 60)
    print(f"原始ASAR: {asar_path}")
    print()

    # Step 1: 获取原始信息
    print("[1/5] 获取原始ASAR信息...")
    original_info = get_asar_info(asar_path)
    print(f"  - 大小: {original_info['size_bytes']:,} bytes ({original_info['size_bytes']/1024/1024:.2f} MB)")
    print(f"  - 文件数: {original_info['file_count']}")
    print()

    # 创建临时工作目录
    work_dir = tempfile.mkdtemp(prefix="asar_roundtrip_")
    repacked_path = os.path.join(work_dir, "app.repacked.asar")

    try:
        # Step 2-5: 执行完整往返验证
        print("[2/5] 解包 → 记录 → 回封 → 对比...")
        result = verify_roundtrip(asar_path, work_dir, repacked_path)

        # Step 6: 输出结果
        print("\n" + "=" * 60)
        print("验证结果")
        print("=" * 60)
        print(f"原始文件大小: {result['original']['size_bytes']:,} bytes")
        print(f"回封文件大小: {result['repacked']['size_bytes']:,} bytes")
        print(f"大小差异: {result['diff']['size_bytes']:,} bytes ({result['diff']['size_percent']:.4f}%)")
        print(f"原始文件数: {result['original']['file_count']}")
        print(f"回封文件数: {result['repacked']['file_count']}")
        print(f"文件数一致: {'PASS' if result['diff']['file_count_match'] else 'FAIL'}")
        print(f"大小差异<1%: {'PASS' if result['diff']['size_within_1pct'] else 'FAIL'}")
        print(f"往返验证: {'** PASS **' if result['roundtrip_pass'] else '** FAIL **'}")
        print()

        if result['roundtrip_pass']:
            print("** PASS: 往返完整性验证通过！")
        else:
            print("** FAIL: 往返完整性验证失败！")
            sys.exit(1)

    finally:
        # 清理临时文件
        print(f"\n清理临时目录: {work_dir}")
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_test_with_dummy_asar():
    """当目标ASAR不存在时，创建一个小型测试ASAR来验证功能"""
    import json

    work_dir = tempfile.mkdtemp(prefix="asar_test_")
    dummy_asar = os.path.join(work_dir, "test.asar")

    try:
        # 创建一个测试目录结构
        src_dir = os.path.join(work_dir, "src")
        os.makedirs(os.path.join(src_dir, "data", "scenarios"))

        # package.json
        with open(os.path.join(src_dir, "package.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "test-app", "main": "index.js"}, f)

        # index.js
        with open(os.path.join(src_dir, "index.js"), "w", encoding="utf-8") as f:
            f.write('console.log("hello");')

        # event.csv
        with open(os.path.join(src_dir, "data", "scenarios", "event.csv"), "w", encoding="utf-8") as f:
            f.write("id,text\n1,Hello World\n2,Test Message\n")

        # intro.csv
        with open(os.path.join(src_dir, "data", "scenarios", "intro.csv"), "w", encoding="utf-8") as f:
            f.write("id,text\n100,Introduction\n200,Start\n")

        # 打包为ASAR
        print(f"创建测试ASAR: {dummy_asar}")
        repack(src_dir, dummy_asar)

        # 验证
        print("获取测试ASAR信息...")
        info = get_asar_info(dummy_asar)
        print(f"  大小: {info['size_bytes']} bytes")
        print(f"  文件数: {info['file_count']}")
        for f in info['files']:
            print(f"    - {f}")

        # 往返测试
        print("\n执行往返测试...")
        result = verify_roundtrip(dummy_asar, os.path.join(work_dir, "roundtrip"))

        print(f"  原始大小: {result['original']['size_bytes']} bytes")
        print(f"  回封大小: {result['repacked']['size_bytes']} bytes")
        print(f"  大小差异: {result['diff']['size_percent']:.4f}%")
        print(f"  文件数一致: {result['diff']['file_count_match']}")
        print(f"  结果: {'** PASS **' if result['roundtrip_pass'] else '** FAIL **'}")

        if result['roundtrip_pass']:
            print("\n** PASS: 测试ASAR往返验证通过！")
        else:
            print("\n** FAIL: 测试ASAR往返验证失败！")
            sys.exit(1)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
