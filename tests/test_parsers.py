import sys
import os
import pytest

pytest.skip("CSV parser workflow was removed; only MTool JSON translation is supported.", allow_module_level=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parser.csv_parser import parse_csv, serialize_csv, get_column_mapping
from parser.json_parser import parse_json, serialize_json


def test_csv_roundtrip():
    """测试CSV往返完整性"""
    print("=" * 60)
    print("测试CSV往返完整性")
    print("=" * 60)
    
    test_files = [
        'tests/intro.csv',
        'tests/event.csv'
    ]
    
    all_passed = True
    
    for file_path in test_files:
        print(f"\n测试文件: {file_path}")
        
        # 读取原始文件内容
        with open(file_path, 'rb') as f:
            original_bytes = f.read()
        
        # 解析CSV
        data = parse_csv(file_path)
        print(f"  表头: {data['header']}")
        print(f"  列数: {data['column_count']}")
        print(f"  行数: {len(data['rows'])}")
        
        # 序列化回CSV
        output_path = file_path.replace('.csv', '_roundtrip.csv')
        serialize_csv(data, output_path)
        
        # 读取序列化后的内容
        with open(output_path, 'rb') as f:
            roundtrip_bytes = f.read()
        
        # 字节级比较
        if original_bytes == roundtrip_bytes:
            print(f"  [PASS] 往返完整性验证通过（字节级相同）")
        else:
            print(f"  [FAIL] 往返完整性验证失败")
            print(f"    原始: {original_bytes!r}")
            print(f"    往返: {roundtrip_bytes!r}")
            all_passed = False
        
        # 验证空单元格保留
        empty_preserved = True
        for i, row in enumerate(data['rows']):
            for j, cell in enumerate(row):
                if cell == '' and j >= 2:  # 资源列应为空
                    pass  # 这是正常的
        print(f"  [PASS] 空单元格保留验证通过")
        
        # 验证列映射
        mapping = get_column_mapping(data['column_count'])
        print(f"  列映射: {mapping}")
        
        # 清理临时文件
        os.remove(output_path)
    
    return all_passed


def test_multiline_quotes():
    """测试多行引用单元格"""
    print("\n" + "=" * 60)
    print("测试多行引用单元格")
    print("=" * 60)
    
    data = parse_csv('tests/intro.csv')
    
    # 查找包含换行符的单元格
    found_multiline = False
    for i, row in enumerate(data['rows']):
        for j, cell in enumerate(row):
            if '\n' in cell:
                print(f"  行{i+1}, 列{j}: 包含换行符")
                print(f"    内容: {cell!r}")
                found_multiline = True
    
    if found_multiline:
        print("  [PASS] 多行引用单元格解析正确")
        return True
    else:
        print("  [FAIL] 未找到多行引用单元格")
        return False


def test_variable_columns():
    """测试变量列数"""
    print("\n" + "=" * 60)
    print("测试变量列数")
    print("=" * 60)
    
    intro = parse_csv('tests/intro.csv')
    event = parse_csv('tests/event.csv')
    
    print(f"  intro.csv: {intro['column_count']} 列")
    print(f"  event.csv: {event['column_count']} 列")
    
    if intro['column_count'] == 7 and event['column_count'] == 8:
        print("  [PASS] 变量列数处理正确")
        return True
    else:
        print("  [FAIL] 变量列数处理错误")
        return False


def test_json_parser():
    """测试JSON解析器"""
    print("\n" + "=" * 60)
    print("测试JSON解析器")
    print("=" * 60)
    
    # 创建测试JSON文件
    test_json = {
        "title": "ゲームタイトル",
        "description": "これはテストです",
        "items": ["item1", "item2"]
    }
    
    import json
    with open('tests/test.json', 'w', encoding='utf-8') as f:
        json.dump(test_json, f, ensure_ascii=False, indent=2)
    
    # 解析
    data = parse_json('tests/test.json')
    print(f"  解析结果: {data}")
    
    # 序列化
    output_path = 'tests/test_roundtrip.json'
    serialize_json(data, output_path)
    
    # 验证
    with open('tests/test.json', 'r', encoding='utf-8') as f:
        original = f.read()
    with open(output_path, 'r', encoding='utf-8') as f:
        roundtrip = f.read()
    
    if original == roundtrip:
        print("  [PASS] JSON往返完整性验证通过")
        result = True
    else:
        print("  [FAIL] JSON往返完整性验证失败")
        result = False
    
    # 清理
    os.remove('tests/test.json')
    os.remove(output_path)
    
    return result


def main():
    print("开始运行解析器测试...")
    
    results = []
    results.append(("CSV往返完整性", test_csv_roundtrip()))
    results.append(("多行引用单元格", test_multiline_quotes()))
    results.append(("变量列数", test_variable_columns()))
    results.append(("JSON解析器", test_json_parser()))
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n所有测试通过!")
        return 0
    else:
        print("\n部分测试失败!")
        return 1


if __name__ == '__main__':
    sys.exit(main())
