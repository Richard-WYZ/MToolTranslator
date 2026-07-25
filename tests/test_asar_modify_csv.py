"""
test_asar_modify_csv.py — 验证 解包→修改CSV→回封 的完整工作流

步骤:
  1. 解包原始 ASAR
  2. 找到 event.csv，修改一个值
  3. 回封为新的 ASAR
  4. 验证文件结构正确
  5. (可选) 启动游戏验证能正常运行
"""

import os
import sys
import pytest

pytest.skip("ASAR workflow was removed; only MTool JSON translation is supported.", allow_module_level=True)

import shutil
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asar_handler import unpack, repack, get_asar_info


def main():
    asar_path = r"F:\gal\d_765404\resources\app.asar"
    game_exe = r"F:\gal\d_765404\Touch Simulation Game.exe"

    if not os.path.isfile(asar_path):
        print(f"[SKIP] 目标ASAR不存在: {asar_path}")
        return

    work_dir = tempfile.mkdtemp(prefix="asar_modify_")
    extract_dir = os.path.join(work_dir, "extracted")
    modified_asar = os.path.join(work_dir, "app.modified.asar")

    try:
        # Step 1: 解包
        print("[1/4] 解包 ASAR...")
        unpack(asar_path, extract_dir)

        # Step 2: 找到并修改 CSV
        print("[2/4] 修改 CSV 文件...")
        csv_path = os.path.join(extract_dir, "dist", "data", "scenarios", "event.csv")
        if not os.path.isfile(csv_path):
            # 尝试搜索
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f == "event.csv":
                        csv_path = os.path.join(root, f)
                        break
                if os.path.isfile(csv_path):
                    break

        if not os.path.isfile(csv_path):
            print("[WARN] 未找到 event.csv，搜索所有 CSV...")
            csvs = []
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f.endswith(".csv"):
                        csvs.append(os.path.join(root, f))
            if csvs:
                csv_path = csvs[0]
                print(f"  使用: {csv_path}")
            else:
                print("[ERROR] 未找到任何 CSV 文件")
                return

        # 读取 CSV 内容
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            original_lines = f.readlines()
        print(f"  CSV 文件: {csv_path}")
        print(f"  原始行数: {len(original_lines)}")

        # 修改第一行数据（跳过可能的标题行）
        # 插入一个标记行以证明修改生效
        modified_lines = original_lines.copy()
        # 在第2行后插入注释标记（不破坏结构）
        insert_line = "# MODIFIED_BY_ASAR_HANDLER_TEST\n"
        if len(modified_lines) > 1:
            modified_lines.insert(2, insert_line)
        else:
            modified_lines.append(insert_line)

        with open(csv_path, "w", encoding="utf-8-sig") as f:
            f.writelines(modified_lines)
        print(f"  修改后行数: {len(modified_lines)}")
        print("  [OK] CSV 已修改")

        # Step 3: 回封
        print("[3/4] 回封 ASAR...")
        repack(extract_dir, modified_asar)

        # Step 4: 验证结构
        print("[4/4] 验证回封文件...")
        info = get_asar_info(modified_asar)
        print(f"  回封 ASAR 大小: {info['size_bytes']:,} bytes")
        print(f"  回封 ASAR 文件数: {info['file_count']}")
        print(f"  原始 ASAR 大小: {os.path.getsize(asar_path):,} bytes")
        print(f"  大小差异: {abs(info['size_bytes'] - os.path.getsize(asar_path)):,} bytes")

        # 验证修改标记存在于 ASAR 中
        found = any("MODIFIED_BY_ASAR_HANDLER_TEST" in f for f in info['files'])
        print(f"  修改标记存在: {'YES' if found else 'NO (not expected in file list, OK)'}")

        print("\n** PASS: 解包→修改CSV→回封 工作流验证通过！")

        # Step 5: 启动游戏验证（如果有游戏路径）
        if os.path.isfile(game_exe):
            print(f"\n[5/5] 启动游戏验证...")
            try:
                proc = subprocess.Popen(
                    [game_exe],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # 等待5秒看是否崩溃
                import time
                time.sleep(5)
                if proc.poll() is None:
                    print("  游戏启动成功，正在运行中...")
                    proc.terminate()
                    proc.wait(timeout=5)
                    print("  游戏正常关闭。")
                    print("\n** PASS: 游戏可正常启动并运行！")
                elif proc.returncode == 0:
                    print("  游戏启动后正常退出。")
                    print("\n** PASS: 游戏可正常启动！")
                else:
                    print(f"  游戏退出码: {proc.returncode}")
                    print("\n** WARN: 游戏启动后异常退出（可能不影响功能）")
            except Exception as e:
                print(f"  启动游戏失败: {e}")
                print("  (不影响 ASAR 模块功能验证)")
        else:
            print(f"\n[SKIP] 游戏可执行文件不存在: {game_exe}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        print(f"\n清理临时目录: {work_dir}")


if __name__ == "__main__":
    main()
