"""T8: 字符约束验证器 — 测试脚本"""
import sys
sys.path.insert(0, '.')
from translator.constraints import validate, auto_wrap, get_violations


def test_validate():
    print('=== validate() 测试 ===')

    # 短文本应通过
    assert validate('短文本') == True
    print('PASS: validate("短文本") == True')

    # 超长行应失败 - 构造 35 个字符
    long_line = '超' * 35  # 35 chars
    assert len(long_line) == 35
    assert validate(long_line) == False
    print(f'PASS: validate(35字行) == False')

    # 刚好30字符应通过
    just_30 = '字' * 30
    assert len(just_30) == 30
    assert validate(just_30) == True
    print('PASS: validate(30字行) == True')

    # 超过4行应失败
    assert validate('a\nb\nc\nd\ne') == False
    print('PASS: validate(5行) == False')

    # 刚好4行应通过
    assert validate('a\nb\nc\nd') == True
    print('PASS: validate(4行) == True')

    # 空文本
    assert validate('') == True
    assert validate(None if False else '') == True  # empty string
    print('PASS: validate("") == True')


def test_auto_wrap_short():
    print('\n=== auto_wrap() 短文本测试 ===')
    short = '你好世界'
    assert auto_wrap(short) == short
    print('PASS: 短文本不变')


def test_auto_wrap_period():
    print('\n=== auto_wrap() 句号断句测试 ===')
    text = '这是第一句话。这是第二句话。这是第三句话。'
    r = auto_wrap(text, max_chars=10, max_lines=4)
    lines = r.split('\n')
    for i, l in enumerate(lines):
        assert len(l) <= 10, f'第{i+1}行长度{len(l)}>10'
    print(f'PASS: 句号断句 — {lines}')


def test_auto_wrap_comma():
    print('\n=== auto_wrap() 逗号断句测试 ===')
    text = '今天天气真好啊，我们去公园散步吧，然后去吃冰淇淋'
    r = auto_wrap(text, max_chars=10, max_lines=4)
    lines = r.split('\n')
    for i, l in enumerate(lines):
        assert len(l) <= 10, f'第{i+1}行长度{len(l)}>10'
    print(f'PASS: 逗号断句 — {lines}')


def test_auto_wrap_force():
    print('\n=== auto_wrap() 强制截断测试 ===')
    text = 'a' * 35 + 'b' * 35
    r = auto_wrap(text, max_chars=10, max_lines=5)
    lines = r.split('\n')
    for i, l in enumerate(lines):
        assert len(l) <= 10, f'第{i+1}行长度{len(l)}>10'
    print(f'PASS: 强制截断 — {lines}')


def test_auto_wrap_truncate_lines():
    print('\n=== auto_wrap() 超行截断+省略号测试 ===')
    text = '第1行\n第2行\n第3行\n第4行\n第5行\n第6行'
    r = auto_wrap(text, max_chars=10, max_lines=4)
    lines = r.split('\n')
    assert len(lines) <= 4, f'行数{len(lines)}>4: {r}'
    assert '……' in r, f'应含省略号: {r}'
    print(f'PASS: 超行截断 — "{r}"')


def test_auto_wrap_preserve():
    print('\n=== auto_wrap() 保留已有换行测试 ===')
    preserve = '你好\n世界\n测试'
    assert auto_wrap(preserve) == preserve
    print('PASS: 保留现有换行')


def test_auto_wrap_edge():
    print('\n=== auto_wrap() 长行合并测试 ===')
    # 5行短文本 → 应合并最后两行
    r = auto_wrap('A\nB\nC\nD\nE', max_chars=10, max_lines=4)
    lines = r.split('\n')
    assert len(lines) <= 4, f'行数{len(lines)}>4: {r}'
    print(f'PASS: 合并场景 — {lines}, result="{r}"')


def test_get_violations():
    print('\n=== get_violations() 测试 ===')
    # 行过长
    long_line = '超' * 35
    v = get_violations(f'正常\n{long_line}')
    assert len(v) > 0
    assert v[0]['type'] == 'line_too_long'
    print(f'PASS: 行过长违规 — {v}')

    # 行数过多
    v = get_violations('a\nb\nc\nd\ne')
    assert any(x['type'] == 'too_many_lines' for x in v)
    print(f'PASS: 行数违规 — {v}')


def test_integration():
    print('\n=== 集成测试 ===')
    # 日文台词翻译后需要修整的长文本
    text = (
        'これはとても長いセリフで、三十文字を超えてしまいます。'
        '翻訳後も同じように長くなる可能性があります。'
        'さらに続くテキストがここに入ります。'
    )
    r = auto_wrap(text, max_chars=15, max_lines=4)
    lines = r.split('\n')
    for i, l in enumerate(lines):
        assert len(l) <= 15, f'第{i+1}行长度{len(l)}>15'
    assert len(lines) <= 4, f'行数{len(lines)}>4'
    print(f'PASS: 集成测试 — {lines}')


if __name__ == '__main__':
    test_validate()
    test_auto_wrap_short()
    test_auto_wrap_period()
    test_auto_wrap_comma()
    test_auto_wrap_force()
    test_auto_wrap_truncate_lines()
    test_auto_wrap_preserve()
    test_auto_wrap_edge()
    test_get_violations()
    test_integration()
    print('\n=== 全部测试通过！ ===')
