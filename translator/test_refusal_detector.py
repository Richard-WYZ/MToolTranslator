"""验证 refusal_detector 核心功能"""
import sys
import os

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from translator import refusal_detector
from translation.config import default_system_prompt


def test_is_refusal():
    """测试拒绝检测"""
    print("=" * 50)
    print("测试 is_refusal()")
    print("=" * 50)

    # 英文拒绝
    assert refusal_detector.is_refusal("I cannot assist with that") is True
    assert refusal_detector.is_refusal("I'm sorry, I can't help") is True
    assert refusal_detector.is_refusal("This is inappropriate content") is True
    assert refusal_detector.is_refusal("I apologize, but I cannot") is True
    print("  [PASS] 英文拒绝检测")

    # 中文拒绝
    assert refusal_detector.is_refusal("作为AI助手，我不能") is True
    assert refusal_detector.is_refusal("这个请求不适当") is False
    assert refusal_detector.assess_model_output("这个请求不适当").is_advisory
    assert refusal_detector.is_refusal("抱歉，无法完成") is False
    assert refusal_detector.assess_model_output("抱歉，无法完成").is_advisory
    print("  [PASS] 中文拒绝检测")

    # 日文响应属于未翻译，不与模型拒绝混为一类
    assert refusal_detector.is_refusal("申し訳ありませんが") is False
    assert refusal_detector.is_unusable_model_output("申し訳ありませんが") is True
    assert refusal_detector.is_refusal("それはできません") is False
    assert refusal_detector.is_unusable_model_output("それはできません") is True
    print("  [PASS] 日文残留分类")

    # 正常中文翻译
    assert refusal_detector.is_refusal("他走了过来") is False
    assert refusal_detector.is_refusal("你好，世界") is False
    assert refusal_detector.is_refusal("这是一个测试") is False
    print("  [PASS] 正常中文翻译不被误判")

    # 空响应
    assert refusal_detector.is_refusal("") is False
    assert refusal_detector.is_unusable_model_output("") is True
    assert refusal_detector.is_unusable_model_output("   ") is True
    assert refusal_detector.is_unusable_model_output("\n\t") is True
    print("  [PASS] 空/空白响应检测")

    # 仅标点
    assert refusal_detector.is_refusal("...") is False
    assert refusal_detector.is_unusable_model_output("...") is True
    assert refusal_detector.is_unusable_model_output("！！！") is True
    print("  [PASS] 仅标点响应检测")

    # 日文残留（伪翻译）
    assert refusal_detector.is_refusal("他走了过来です") is False
    assert refusal_detector.is_unusable_model_output("他走了过来です") is True
    assert refusal_detector.is_unusable_model_output("测试テスト") is True
    print("  [PASS] 日文残留检测")

    # 英文占比过高（原文是日文）
    assert refusal_detector.is_refusal("Hello world", original="こんにちは") is False
    assert refusal_detector.is_unusable_model_output("Hello world", original="こんにちは") is False
    assert refusal_detector.is_refusal("你好", original="こんにちは") is False
    print("  [PASS] 英文占比检测")

    print("\n所有 is_refusal() 测试通过！")


def test_retry_needs_review():
    """测试 3 次重试后返回 NEEDS_REVIEW"""
    print("=" * 50)
    print("测试 retry_with_fallback() 3次重试 → NEEDS_REVIEW")
    print("=" * 50)

    call_count = [0]
    original_translate = refusal_detector.translate

    def mock_translate(model, text, system_prompt="", terminology=None, timeout=60):
        call_count[0] += 1
        # 始终返回拒绝响应
        return "I'm sorry, I cannot translate this content."

    # 替换为 mock
    refusal_detector.translate = mock_translate

    try:
        result = refusal_detector.retry_with_fallback(
            model="qwen2.5:7b",
            text="こんにちは、これはテストです",
            system_prompt=default_system_prompt("professional"),
            attempt=0,
            file_path="test.csv",
            row=1,
            col=0,
        )

        print(f"  调用次数: {call_count[0]}")
        print(f"  返回结果: {result}")

        assert result["status"] == "NEEDS_REVIEW", f"期望 NEEDS_REVIEW，实际 {result['status']}"
        assert result["original"] == "こんにちは、これはテストです"
        # 初始 1 次 + 提示切换 1 次 + 模型切换 1 次 + 分块翻译 N 次
        # 分块会把长句拆成多块，所以调用次数 >= 4
        assert call_count[0] >= 4, f"期望至少 4 次调用，实际 {call_count[0]}"
        print("  [PASS] 3 次重试后正确标记 NEEDS_REVIEW")
    finally:
        # 恢复原始函数
        refusal_detector.translate = original_translate

    print("\nretry_with_fallback() 测试通过！")


def test_has_japanese():
    """测试 has_japanese()"""
    print("=" * 50)
    print("测试 has_japanese()")
    print("=" * 50)

    assert refusal_detector.has_japanese("こんにちは") is True
    assert refusal_detector.has_japanese("カタカナ") is True
    assert refusal_detector.has_japanese("混合文本です") is True
    assert refusal_detector.has_japanese("纯中文") is False
    assert refusal_detector.has_japanese("English only") is False
    assert refusal_detector.has_japanese("") is False
    print("  [PASS] 平假名/片假名检测")

    print("\nhas_japanese() 测试通过！")


def test_chunk_translate():
    """测试分块翻译拆分逻辑（不调用真实 Ollama）"""
    print("=" * 50)
    print("测试 chunk_translate() 拆分逻辑")
    print("=" * 50)

    # 测试拆分逻辑：手动构造一个会被拆分的文本
    long_text = "a" * 100
    chunks = []

    call_count = [0]
    original_translate = refusal_detector.translate

    def mock_translate(model, text, system_prompt="", terminology=None, timeout=60):
        call_count[0] += 1
        chunks.append(text)
        return f"[translated:{text[:10]}...]"

    refusal_detector.translate = mock_translate

    try:
        result = refusal_detector.chunk_translate(
            "qwen2.5:7b", long_text, "prompt", max_chars=50, overlap=10
        )
        print(f"  调用次数: {call_count[0]}")
        print(f"  拆分块数: {len(chunks)}")
        print(f"  各块长度: {[len(c) for c in chunks]}")
        assert call_count[0] >= 2, "长文本应被拆成至少 2 块"
        assert len(result) > 0
        print("  [PASS] 分块拆分与合并")
    finally:
        refusal_detector.translate = original_translate

    print("\nchunk_translate() 测试通过！")


if __name__ == "__main__":
    test_is_refusal()
    print()
    test_has_japanese()
    print()
    test_retry_needs_review()
    print()
    test_chunk_translate()
    print()
    print("=" * 50)
    print("全部验证通过！")
    print("=" * 50)
